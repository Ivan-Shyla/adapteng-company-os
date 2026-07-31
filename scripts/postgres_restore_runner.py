#!/usr/bin/env python3
"""Measure and invoke only the sealed restore-rehearsal runner commands."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any


SCRIPT_DIR = Path(__file__).resolve().parent
MANIFEST_PATH = SCRIPT_DIR / "postgres_restore_runner_manifest.json"
PROCEDURE_MANIFEST = SCRIPT_DIR / "postgres_restore_procedure_manifest.json"
DATABASE_ENV = Path("/run/secrets/postgres-restore-runner.env")
DIGEST = re.compile(r"^[a-z0-9][a-z0-9._/-]*@sha256:[0-9a-f]{64}$")
CONFIG_ID = re.compile(r"^sha256:[0-9a-f]{64}$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")
SCRATCH_PASSWORD_PATTERN = re.compile(r"^[A-Za-z0-9_./+=:@%-]{32,256}$")
MANIFEST_KEYS = {
    "schema_version",
    "status",
    "repo_digest",
    "config_id",
    "os",
    "architecture",
    "psql_entrypoint",
    "probe_argv",
    "database_environment",
    "migration_entrypoint",
    "bootstrap_entrypoint",
    "bootstrap_argv",
    "migration_commands",
}


class RunnerError(RuntimeError):
    """Fail-closed runner identity or command error."""


def canonical_json(value: Any) -> bytes:
    return (
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        .encode("ascii")
        + b"\n"
    )


def secure_member_bytes(path: Path, label: str, *, private: bool = False) -> bytes:
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        info = os.fstat(descriptor)
        forbidden_mode = 0o077 if private else 0o022
        if (
            os.geteuid() != 0
            or info.st_uid != 0
            or not stat.S_ISREG(info.st_mode)
            or info.st_mode & forbidden_mode
        ):
            raise RunnerError(f"{label} ownership/mode is not secure")
        chunks: list[bytes] = []
        while chunk := os.read(descriptor, 65536):
            chunks.append(chunk)
        return b"".join(chunks)
    finally:
        os.close(descriptor)


def verify_procedure(expected_sha256: str) -> dict[str, Any]:
    if not SHA256.fullmatch(expected_sha256):
        raise RunnerError("procedure manifest SHA-256 is malformed")
    raw = secure_member_bytes(PROCEDURE_MANIFEST, "procedure manifest")
    if hashlib.sha256(raw).hexdigest() != expected_sha256:
        raise RunnerError("procedure manifest digest mismatch")
    value = json.loads(raw)
    artifacts = value.get("artifacts") if isinstance(value, dict) else None
    if not isinstance(artifacts, dict):
        raise RunnerError("procedure manifest artifacts are missing")
    for path, member in (
        (Path(__file__), "scripts/postgres_restore_runner.py"),
        (MANIFEST_PATH, "scripts/postgres_restore_runner_manifest.json"),
    ):
        expected = artifacts.get(member)
        if not isinstance(expected, str) or hashlib.sha256(
            secure_member_bytes(path, member)
        ).hexdigest() != expected:
            raise RunnerError(f"{member} is not the sealed procedure member")
    return value


def load_manifest() -> tuple[dict[str, Any], str]:
    raw = secure_member_bytes(MANIFEST_PATH, "runner manifest")
    manifest = json.loads(raw)
    if (
        not isinstance(manifest, dict)
        or set(manifest) != MANIFEST_KEYS
        or manifest.get("schema_version") != 1
    ):
        raise RunnerError("runner manifest is not exact v1")
    if manifest.get("status") != "APPROVED":
        raise RunnerError("runner manifest is NOT_CONFIGURED")
    if not DIGEST.fullmatch(str(manifest.get("repo_digest"))):
        raise RunnerError("runner repo digest is not approved")
    if not CONFIG_ID.fullmatch(str(manifest.get("config_id"))):
        raise RunnerError("runner config ID is not approved")
    if manifest.get("os") != "linux" or manifest.get("architecture") not in {
        "amd64",
        "arm64",
    }:
        raise RunnerError("runner platform is not approved")
    return manifest, hashlib.sha256(raw).hexdigest()


def validate_database_env(
    payload: bytes,
    manifest: dict[str, Any],
    generation: str,
) -> dict[str, str]:
    try:
        text = payload.decode("ascii")
    except UnicodeDecodeError as exc:
        raise RunnerError("database environment is not ASCII") from exc
    values: dict[str, str] = {}
    for line in text.splitlines():
        if not line or line.startswith("#") or "=" not in line:
            raise RunnerError("database environment line is not exact")
        key, value = line.split("=", 1)
        if key in values:
            raise RunnerError("database environment key is duplicated")
        values[key] = value
    policy = manifest.get("database_environment")
    if not isinstance(policy, dict) or set(policy) != {
        "fixed",
        "host_template",
        "secret_key",
        "minimum_secret_length",
    }:
        raise RunnerError("database environment policy is not exact")
    fixed = policy["fixed"]
    secret_key = policy["secret_key"]
    if (
        not isinstance(fixed, dict)
        or secret_key != "PGPASSWORD"
        or policy["host_template"] != "adapteng-db-{generation_lower}"
        or policy["minimum_secret_length"] != 32
    ):
        raise RunnerError("database environment policy is not approved")
    expected = {
        **fixed,
        "PGHOST": f"adapteng-db-{generation.lower()}",
    }
    if set(values) != {*expected, secret_key} or any(
        values.get(key) != value for key, value in expected.items()
    ):
        raise RunnerError("database environment target is not exact scratch state")
    if not SCRATCH_PASSWORD_PATTERN.fullmatch(values[secret_key]):
        raise RunnerError("database environment secret shape is not approved")
    return expected


def open_database_env(
    manifest: dict[str, Any], generation: str
) -> tuple[int, dict[str, str]]:
    descriptor = os.open(DATABASE_ENV, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        info = os.fstat(descriptor)
        if (
            os.geteuid() != 0
            or info.st_uid != 0
            or not stat.S_ISREG(info.st_mode)
            or info.st_mode & 0o077
        ):
            raise RunnerError("database environment must be root-owned mode 0600")
        payload = bytearray()
        while chunk := os.read(descriptor, 65536):
            payload.extend(chunk)
        target = validate_database_env(bytes(payload), manifest, generation)
        os.lseek(descriptor, 0, os.SEEK_SET)
        return descriptor, target
    except Exception:
        os.close(descriptor)
        raise


def validate_runner_inspection(
    container: dict[str, Any],
    image: dict[str, Any],
    manifest: dict[str, Any],
) -> dict[str, Any]:
    if container.get("State", {}).get("Running") is not False:
        raise RunnerError("runner measurement container is running")
    if container.get("Image") != manifest["config_id"]:
        raise RunnerError("runner container .Image is not approved")
    if container.get("Config", {}).get("Image") != manifest["repo_digest"]:
        raise RunnerError("runner container reference is not immutable/approved")
    if image.get("Id") != manifest["config_id"]:
        raise RunnerError("runner image config ID is not approved")
    if image.get("RepoDigests") != [manifest["repo_digest"]]:
        raise RunnerError("runner image must have exactly one approved RepoDigest")
    if image.get("Os") != manifest["os"] or image.get("Architecture") != (
        manifest["architecture"]
    ):
        raise RunnerError("runner platform is not approved")
    return {
        "schema_version": 1,
        "status": "MEASURED_APPROVED",
        "config_id": manifest["config_id"],
        "repo_digest": manifest["repo_digest"],
        "os": manifest["os"],
        "architecture": manifest["architecture"],
    }


def inspect_one(
    args: list[str],
    *,
    run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> dict[str, Any]:
    completed = run(
        ["docker", *args],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    value = json.loads(completed.stdout)
    if not isinstance(value, list) or len(value) != 1 or not isinstance(value[0], dict):
        raise RunnerError("Docker inspection did not return one object")
    return value[0]


def measure_runner(
    manifest: dict[str, Any],
    *,
    run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> tuple[dict[str, Any], str]:
    name = f"adapteng-runner-measure-{uuid.uuid4().hex}"
    try:
        run(
            [
                "docker",
                "create",
                "--name",
                name,
                "--network",
                "none",
                "--entrypoint",
                "/usr/bin/true",
                manifest["repo_digest"],
            ],
            check=True,
            capture_output=True,
        )
        container = inspect_one(["container", "inspect", name], run=run)
        image = inspect_one(
            ["image", "inspect", str(container.get("Image", ""))],
            run=run,
        )
        measured = validate_runner_inspection(container, image, manifest)
        return measured, hashlib.sha256(canonical_json(measured)).hexdigest()
    except (OSError, subprocess.CalledProcessError, json.JSONDecodeError) as exc:
        raise RunnerError(f"runner measurement failed: {exc}") from exc
    finally:
        run(["docker", "rm", "-f", name], capture_output=True)


def command_for_mode(
    manifest: dict[str, Any], mode: str
) -> tuple[str, list[str]]:
    if mode in {"probe", "assert-c-final"}:
        return str(manifest["psql_entrypoint"]), list(manifest["probe_argv"])
    commands = manifest.get("migration_commands")
    if not isinstance(commands, dict) or mode not in commands:
        raise RunnerError("runner mode is not sealed")
    argv = commands[mode]
    if not isinstance(argv, list) or not all(isinstance(item, str) for item in argv):
        raise RunnerError("sealed runner command is malformed")
    return str(manifest["migration_entrypoint"]), argv


def validate_target_container(
    container: dict[str, Any], generation: str
) -> dict[str, Any]:
    expected_name = f"adapteng-db-{generation.lower()}"
    if (
        container.get("Name") != f"/{expected_name}"
        or container.get("State", {}).get("Running") is not True
    ):
        raise RunnerError("database target container is not exact/running")
    networks = container.get("NetworkSettings", {}).get("Networks")
    if not isinstance(networks, dict) or set(networks) != {"pg-rehearsal"}:
        raise RunnerError("database target is not only on the locked network")
    if container.get("HostConfig", {}).get("PortBindings") not in (None, {}):
        raise RunnerError("database target has a published port")
    mounts = container.get("Mounts")
    if not isinstance(mounts, list) or len(mounts) != 1:
        raise RunnerError("database target mount inventory is not exact")
    mount = mounts[0]
    if (
        mount.get("Type") != "volume"
        or mount.get("Name") != f"adapteng-restore-{generation.lower()}"
        or "docker.sock" in str(mount)
    ):
        raise RunnerError("database target volume/socket identity is not exact")
    return {
        "container_ref_sha256": hashlib.sha256(
            str(container.get("Id", "")).encode("utf-8")
        ).hexdigest(),
        "generation": generation,
        "locked_network": "pg-rehearsal",
        "published_ports": 0,
        "volume_ref_sha256": hashlib.sha256(
            str(mount["Name"]).encode("utf-8")
        ).hexdigest(),
    }


def role_lifecycle_sql(mode: str, scratch_password: str) -> bytes:
    if mode == "bootstrap-role":
        return (
            "CREATE ROLE postgres_restore_runner LOGIN SUPERUSER PASSWORD "
            f"'{scratch_password}';\n"
        ).encode("ascii")
    if mode == "drop-role":
        return b"DROP ROLE postgres_restore_runner;\n"
    raise RunnerError("scratch role lifecycle mode is not exact")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "mode",
        choices=(
            "probe",
            "assert-c-final",
            "status",
            "apply-007",
            "apply-drive-008",
            "bootstrap-role",
            "drop-role",
        ),
    )
    parser.add_argument("--generation", choices=("A", "B", "C"), required=True)
    parser.add_argument("--procedure-manifest-sha256", required=True)
    parser.add_argument("--expect", action="append", default=[])
    args = parser.parse_args()
    database_env_fd = -1
    try:
        if os.name != "posix" or os.geteuid() != 0:
            raise RunnerError("runner requires a POSIX root host")
        verify_procedure(args.procedure_manifest_sha256)
        manifest, manifest_sha256 = load_manifest()
        measured, measured_sha256 = measure_runner(manifest)
        target_container = f"adapteng-db-{args.generation.lower()}"
        target_inspection = inspect_one(
            ["container", "inspect", target_container]
        )
        container_identity = validate_target_container(
            target_inspection, args.generation
        )
        database_env_fd, target = open_database_env(manifest, args.generation)
        if args.mode in {"bootstrap-role", "drop-role"}:
            if args.expect:
                raise RunnerError("--expect is valid only for status")
            os.lseek(database_env_fd, 0, os.SEEK_SET)
            env_payload = os.read(database_env_fd, 65536)
            values = dict(
                line.split("=", 1)
                for line in env_payload.decode("ascii").splitlines()
            )
            command = [
                "docker",
                "exec",
                "-i",
                "-u",
                "postgres",
                target_container,
                str(manifest["bootstrap_entrypoint"]),
                *list(manifest["bootstrap_argv"]),
            ]
            completed = subprocess.run(
                command,
                input=role_lifecycle_sql(args.mode, values["PGPASSWORD"]),
            )
        else:
            entrypoint, argv = command_for_mode(manifest, args.mode)
            if args.mode == "status":
                if not args.expect:
                    raise RunnerError("status mode requires expected states")
                argv = [*argv, *sum((["--expect", item] for item in args.expect), [])]
            elif args.expect:
                raise RunnerError("--expect is valid only for status")
            command = [
                "docker",
                "run",
                "--rm",
                "--network",
                "pg-rehearsal",
                "--env-file",
                f"/proc/self/fd/{database_env_fd}",
                "--entrypoint",
                entrypoint,
                manifest["repo_digest"],
                *argv,
            ]
            completed = subprocess.run(
                command,
                input=(
                    sys.stdin.buffer.read()
                    if args.mode in {"probe", "assert-c-final"}
                    else None
                ),
                pass_fds=(database_env_fd,),
            )
        if completed.returncode != 0:
            raise RunnerError("sealed runner command failed")
        print(f"runner_manifest_sha256={manifest_sha256}", file=sys.stderr)
        print(f"measured_runner_identity_sha256={measured_sha256}", file=sys.stderr)
        print(
            "database_target_identity_sha256="
            f"{hashlib.sha256(canonical_json(target)).hexdigest()}",
            file=sys.stderr,
        )
        print(
            "database_container_identity_sha256="
            f"{hashlib.sha256(canonical_json(container_identity)).hexdigest()}",
            file=sys.stderr,
        )
        return 0
    except (
        OSError,
        UnicodeError,
        json.JSONDecodeError,
        subprocess.CalledProcessError,
        RunnerError,
    ) as exc:
        print(f"STOP: {exc}", file=sys.stderr)
        return 2
    finally:
        if database_env_fd >= 0:
            os.close(database_env_fd)


if __name__ == "__main__":
    raise SystemExit(main())
