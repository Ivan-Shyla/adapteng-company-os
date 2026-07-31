#!/usr/bin/env python3
"""Measure a Docker container image and compare it with a reviewed manifest."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
from pathlib import Path
from collections.abc import Callable
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
    "image_environment",
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


CLEAN_ENVIRONMENT = {
    "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
    "LANG": "C",
    "LC_ALL": "C",
}
FORBIDDEN_OVERRIDE_KEYS = {
    "PGHOST",
    "PGPORT",
    "PGDATABASE",
    "PGUSER",
    "PGPASSWORD",
    "PGOPTIONS",
    "PGSERVICE",
}


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


def docker_json(
    *args: str,
    run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> Any:
    try:
        completed = run(
            ["docker", *args],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=CLEAN_ENVIRONMENT,
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
    try:
        descriptor = os.open(
            path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        )
    except OSError as exc:
        raise IdentityError(f"approved image manifest cannot be opened: {exc}") from exc
    try:
        raw = bytearray()
        while chunk := os.read(descriptor, 1024 * 1024):
            raw.extend(chunk)
    finally:
        os.close(descriptor)
    actual_sha256 = hashlib.sha256(raw).hexdigest()
    if actual_sha256 != expected_sha256:
        raise IdentityError("approved image manifest digest mismatch")
    try:
        manifest = json.loads(raw)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise IdentityError(f"approved image manifest is invalid JSON: {exc}") from exc
    if not isinstance(manifest, dict):
        raise IdentityError("approved image manifest must be an object")
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
    environment = manifest["image_environment"]
    if (
        not isinstance(environment, list)
        or not all(isinstance(item, str) and "=" in item for item in environment)
        or len({item.split("=", 1)[0] for item in environment}) != len(environment)
    ):
        raise IdentityError("approved image environment is not exact")
    environment_keys = {item.split("=", 1)[0] for item in environment}
    if any(
        key.startswith(("PGBACKREST_", "AWS_", "B2_"))
        or key in FORBIDDEN_OVERRIDE_KEYS
        for key in environment_keys
    ):
        raise IdentityError("approved image environment contains a runtime override")
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
    *,
    inspect: Callable[..., Any] = docker_json,
) -> dict[str, Any]:
    manifest, manifest_sha256 = validate_manifest(
        manifest_path, expected_manifest_sha256
    )
    container = one_inspect(
        inspect("container", "inspect", container_name),
        "container inspection",
    )
    image = one_inspect(
        inspect("image", "inspect", str(container.get("Image", ""))),
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
    if not isinstance(env, list) or env != manifest["image_environment"]:
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
        "image_environment_sha256": hashlib.sha256(canonical_json(env)).hexdigest(),
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
        parent = args.output.parent
        parent_info = parent.lstat()
        if (
            os.name != "posix"
            or os.geteuid() != 0
            or not stat.S_ISDIR(parent_info.st_mode)
            or parent_info.st_uid != 0
            or parent_info.st_mode & 0o077
        ):
            raise IdentityError("image identity output directory is not root-owned/private")
        payload = canonical_json(packet)
        descriptor = os.open(
            args.output,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
        )
        try:
            offset = 0
            while offset < len(payload):
                offset += os.write(descriptor, payload[offset:])
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        print(f"image_identity_sha256={hashlib.sha256(payload).hexdigest()}")
        return 0
    except IdentityError as exc:
        print(f"STOP: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
