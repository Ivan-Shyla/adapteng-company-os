#!/usr/bin/env python3
"""Measure a Docker container image and compare it with a reviewed manifest."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any


SHA256 = re.compile(r"^[0-9a-f]{64}$")
DOCKER_DIGEST = re.compile(r"^[a-z0-9][a-z0-9._/-]*@sha256:[0-9a-f]{64}$")
CONFIG_ID = re.compile(r"^sha256:[0-9a-f]{64}$")
MANIFEST_KEYS = {
    "schema_version",
    "status",
    "image_reference",
    "repo_digest",
    "config_id",
    "os",
    "architecture",
    "postgres_pgdata",
    "postgres_version",
    "pgbackrest_version",
    "pgbackrest_binary_sha256",
    "build_artifact_sha256",
    "reviewed_at_utc",
    "reviewed_by",
}


class IdentityError(RuntimeError):
    """Fail-closed image identity error."""


def canonical_json(value: Any) -> bytes:
    return (
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        .encode("ascii")
        + b"\n"
    )


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_json_object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise IdentityError(f"{label} is not valid UTF-8 JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise IdentityError(f"{label} must be a JSON object")
    return value


def docker_json(*args: str) -> Any:
    command = ["docker", *args]
    test_docker = os.environ.get("POSTGRES_RESTORE_TEST_DOCKER")
    if test_docker:
        if os.environ.get("POSTGRES_RESTORE_TEST_MODE") != "1":
            raise IdentityError("test Docker override is forbidden outside test mode")
        command = [sys.executable, test_docker, *args]
    try:
        completed = subprocess.run(
            command,
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        return json.loads(completed.stdout)
    except (OSError, subprocess.CalledProcessError, json.JSONDecodeError) as exc:
        raise IdentityError(f"docker {' '.join(args)} failed: {exc}") from exc


def one_inspect(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, list) or len(value) != 1 or not isinstance(value[0], dict):
        raise IdentityError(f"{label} must return exactly one inspect object")
    return value[0]


def validate_manifest(
    path: Path, expected_sha256: str
) -> tuple[dict[str, Any], str]:
    if not SHA256.fullmatch(expected_sha256):
        raise IdentityError("approved manifest SHA-256 is malformed")
    if not path.is_file() or path.is_symlink():
        raise IdentityError("approved image manifest must be a regular non-symlink file")
    actual_sha256 = sha256_file(path)
    if actual_sha256 != expected_sha256:
        raise IdentityError("approved image manifest digest mismatch")
    manifest = load_json_object(path, "approved image manifest")
    if set(manifest) != MANIFEST_KEYS:
        raise IdentityError("approved image manifest has missing or unknown fields")
    if manifest["schema_version"] != 1 or manifest["status"] != "APPROVED":
        raise IdentityError("approved image manifest is not an approved v1 manifest")
    if manifest["image_reference"] != manifest["repo_digest"]:
        raise IdentityError("approved image reference must equal its immutable repo digest")
    if not DOCKER_DIGEST.fullmatch(str(manifest["repo_digest"])):
        raise IdentityError("approved image must use exactly one immutable repo digest")
    if not CONFIG_ID.fullmatch(str(manifest["config_id"])):
        raise IdentityError("approved image config ID is malformed")
    if manifest["os"] != "linux" or manifest["architecture"] not in {
        "amd64",
        "arm64",
    }:
        raise IdentityError("approved image platform is unsupported")
    if not str(manifest["postgres_pgdata"]).startswith("/"):
        raise IdentityError("approved image PGDATA must be an absolute path")
    if manifest["postgres_version"] != "16":
        raise IdentityError("approved image must pin PostgreSQL major 16")
    if manifest["pgbackrest_version"] not in {"absent", "2.59.0"}:
        raise IdentityError("approved image pgBackRest state is unsupported")
    if manifest["pgbackrest_version"] == "absent":
        if manifest["pgbackrest_binary_sha256"] is not None:
            raise IdentityError("source image with absent pgBackRest must use null hash")
    elif not SHA256.fullmatch(str(manifest["pgbackrest_binary_sha256"])):
        raise IdentityError("approved image pgBackRest binary SHA-256 is malformed")
    if not SHA256.fullmatch(str(manifest["build_artifact_sha256"])):
        raise IdentityError("approved image build artifact SHA-256 is malformed")
    return manifest, actual_sha256


def measure_container(
    container_name: str,
    manifest_path: Path,
    expected_manifest_sha256: str,
) -> dict[str, Any]:
    manifest, manifest_sha256 = validate_manifest(
        manifest_path, expected_manifest_sha256
    )
    container = one_inspect(
        docker_json("container", "inspect", container_name),
        "container inspection",
    )
    image = one_inspect(
        docker_json("image", "inspect", str(container.get("Image", ""))),
        "image inspection",
    )

    if container.get("Image") != manifest["config_id"]:
        raise IdentityError("container .Image does not match approved image config ID")
    config_image = container.get("Config", {}).get("Image")
    if config_image != manifest["repo_digest"]:
        raise IdentityError("container was not created from the approved immutable digest")
    platform = container.get("Platform")
    if platform not in (None, manifest["os"]):
        raise IdentityError("container platform does not match approved image")
    if image.get("Id") != manifest["config_id"]:
        raise IdentityError("Docker image config ID does not match approved manifest")
    repo_digests = image.get("RepoDigests")
    if repo_digests != [manifest["repo_digest"]]:
        raise IdentityError("Docker image must expose exactly one approved RepoDigest")
    if image.get("Os") != manifest["os"]:
        raise IdentityError("Docker image OS does not match approved manifest")
    if image.get("Architecture") != manifest["architecture"]:
        raise IdentityError("Docker image architecture does not match approved manifest")

    env = image.get("Config", {}).get("Env")
    if not isinstance(env, list):
        raise IdentityError("Docker image environment is missing")
    pgdata_values = [
        item.split("=", 1)[1]
        for item in env
        if isinstance(item, str) and item.startswith("PGDATA=")
    ]
    if pgdata_values != [manifest["postgres_pgdata"]]:
        raise IdentityError("Docker image PGDATA does not match approved manifest")

    container_id = str(container.get("Id", ""))
    if not container_id:
        raise IdentityError("container ID is missing")
    return {
        "schema_version": 1,
        "status": "MEASURED_APPROVED",
        "container_ref_sha256": hashlib.sha256(container_id.encode("utf-8")).hexdigest(),
        "approved_manifest_sha256": manifest_sha256,
        "image_config_id": manifest["config_id"],
        "repo_digest": manifest["repo_digest"],
        "os": manifest["os"],
        "architecture": manifest["architecture"],
        "postgres_pgdata": manifest["postgres_pgdata"],
        "postgres_version": manifest["postgres_version"],
        "pgbackrest_version": manifest["pgbackrest_version"],
        "pgbackrest_binary_sha256": manifest["pgbackrest_binary_sha256"],
        "build_artifact_sha256": manifest["build_artifact_sha256"],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--container", required=True)
    parser.add_argument("--approved-manifest", required=True, type=Path)
    parser.add_argument("--approved-manifest-sha256", required=True)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()

    try:
        packet = measure_container(
            args.container,
            args.approved_manifest,
            args.approved_manifest_sha256,
        )
        args.output.write_bytes(canonical_json(packet))
        print(f"image_identity_sha256={sha256_file(args.output)}")
        return 0
    except IdentityError as exc:
        print(f"STOP: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
