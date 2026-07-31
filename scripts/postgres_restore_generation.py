#!/usr/bin/env python3
"""Descriptor-bound orchestration for one guarded restore generation."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

try:
    import fcntl
except ModuleNotFoundError:  # pragma: no cover - unavailable on non-POSIX test hosts
    fcntl = None  # type: ignore[assignment]


SCRIPT_DIR = Path(__file__).resolve().parent
PROCEDURE_MANIFEST = SCRIPT_DIR / "postgres_restore_procedure_manifest.json"
GUARD = SCRIPT_DIR / "postgres_restore_guard.py"
PROVIDER_COLLECTOR = SCRIPT_DIR / "postgres_restore_provider_inventory.py"
PROVIDER_MANIFEST = SCRIPT_DIR / "postgres_restore_provider_manifest.json"
RUNNER_MANIFEST = SCRIPT_DIR / "postgres_restore_runner_manifest.json"
RUNNER = SCRIPT_DIR / "postgres_restore_runner.py"
EXPORTER_MANIFEST = SCRIPT_DIR / "postgres_restore_inventory_exporter_manifest.json"
PROVIDER_INBOX = Path("/run/adapteng/postgres-restore-provider")
STATE_ROOT = Path("/var/lib/adapteng/postgres-restore-rehearsal")
ACCEPTED_RETENTION = STATE_ROOT / "retention" / "accepted.json"
REPOSITORY_SECRET = Path("/run/secrets/postgres-restore-repository.json")
TIMESTAMP = "%Y-%m-%dT%H:%M:%SZ"
SAFE_SECRET = __import__("re").compile(r"^[A-Za-z0-9_./+=:@%-]{32,512}$")
CLEAN_ENVIRONMENT = {
    "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
    "LANG": "C",
    "LC_ALL": "C",
}
PROVIDER_BROKER_REQUEST_FD = 197
PROVIDER_BROKER_RESPONSE_FD = 198


class GenerationError(RuntimeError):
    """Fail-closed generation orchestration error."""


def host_isolation_shape(host: dict[str, Any]) -> dict[str, Any]:
    try:
        from postgres_restore_host_inventory import host_isolation_shape as validate
    except ModuleNotFoundError:  # pragma: no cover - package import in unit tests
        from scripts.postgres_restore_host_inventory import (
            host_isolation_shape as validate,
        )
    return validate(host)


def canonical_json(value: Any) -> bytes:
    return (
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        .encode("ascii")
        + b"\n"
    )


def strict_json_object(payload: bytes, label: str) -> dict[str, Any]:
    def pairs(values: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in values:
            if key in result:
                raise GenerationError(f"{label} contains a duplicate key")
            result[key] = value
        return result

    try:
        value = json.loads(payload, object_pairs_hook=pairs)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise GenerationError(f"{label} is invalid JSON") from exc
    if not isinstance(value, dict):
        raise GenerationError(f"{label} must be a JSON object")
    return value


@dataclass(frozen=True)
class GenerationState:
    generation: str
    procedure_manifest_sha256: str
    image_config_id: str
    image_repo_digest: str
    image_environment_sha256: str
    recovery_container: str
    recovery_container_id: str
    final_container: str
    final_container_id: str
    volume: str
    bootstrap_network: str
    locked_network: str
    restore_pg1_path: str
    database_pgdata: str
    repository_endpoint: str
    repository_bucket: str
    repository_region: str
    repository_path: str
    restore_key_attestation_sha256: str
    stanza: str
    repo: str
    selected_set_ref_sha256: str
    selected_set_info_sha256: str
    completed_at: str
    inventory_sha256: str
    measured_image_identity_sha256: str
    cloud_instance_id_sha256: str


@dataclass(frozen=True)
class ProviderPolicy:
    collector_id: str
    collector_version: int
    collector_sha256: str
    public_key_pem: str
    public_key_pem_sha256: str
    owner_ssh_cidr_sha256: str
    account_context_sha256: str
    provider_target_config_sha256: str
    max_age_seconds: int


def validate_owned_metadata(
    *,
    uid: int,
    mode: int,
    expected_kind: str,
    is_symlink: bool,
) -> None:
    if uid != 0:
        raise GenerationError("secure state must be root-owned")
    if is_symlink:
        raise GenerationError("secure state must not be a symlink")
    if expected_kind == "directory":
        if not stat.S_ISDIR(mode) or mode & 0o077:
            raise GenerationError("secure directory must be mode 0700 or stricter")
    elif expected_kind == "file":
        if not stat.S_ISREG(mode) or mode & 0o077:
            raise GenerationError("secure file must be mode 0600 or stricter")
    else:
        raise GenerationError("unknown secure metadata kind")


def validate_exclusive_target(*, exists: bool, is_symlink: bool) -> None:
    if exists or is_symlink:
        raise GenerationError("exclusive state target already exists or is a symlink")


def parse_descriptor_owned_bytes(
    expected: bytes,
    descriptor_bytes: bytes,
    *,
    uid: int,
    mode: int,
) -> tuple[dict[str, Any], str]:
    validate_owned_metadata(
        uid=uid,
        mode=mode,
        expected_kind="file",
        is_symlink=False,
    )
    if descriptor_bytes != expected:
        raise GenerationError("secured packet bytes changed")
    parsed = json.loads(descriptor_bytes)
    if not isinstance(parsed, dict):
        raise GenerationError("secured packet is not a JSON object")
    return parsed, hashlib.sha256(descriptor_bytes).hexdigest()


def require_root_owned(path: Path, expected_kind: str) -> os.stat_result:
    if os.name != "posix" or os.geteuid() != 0:
        raise GenerationError("generation wrapper requires a POSIX root host")
    info = path.lstat()
    validate_owned_metadata(
        uid=info.st_uid,
        mode=info.st_mode,
        expected_kind=expected_kind,
        is_symlink=path.is_symlink(),
    )
    return info


def create_generation_directory(generation: str) -> tuple[Path, int]:
    require_root_owned(STATE_ROOT, "directory")
    path = STATE_ROOT / f"generation-{generation}"
    try:
        path.lstat()
    except FileNotFoundError:
        validate_exclusive_target(exists=False, is_symlink=False)
    else:
        validate_exclusive_target(exists=True, is_symlink=path.is_symlink())
    try:
        os.mkdir(path, 0o700)
    except FileExistsError as exc:
        raise GenerationError("generation state already exists") from exc
    info = require_root_owned(path, "directory")
    directory_fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    fd_info = os.fstat(directory_fd)
    validate_owned_metadata(
        uid=fd_info.st_uid,
        mode=fd_info.st_mode,
        expected_kind="directory",
        is_symlink=False,
    )
    if (info.st_dev, info.st_ino) != (fd_info.st_dev, fd_info.st_ino):
        os.close(directory_fd)
        raise GenerationError("generation directory changed during creation")
    return path, directory_fd


def write_and_parse_once(
    directory_fd: int,
    name: str,
    payload: bytes,
) -> tuple[int, dict[str, Any], str]:
    flags = os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
    fd = os.open(name, flags, 0o600, dir_fd=directory_fd)
    try:
        offset = 0
        while offset < len(payload):
            offset += os.write(fd, payload[offset:])
        os.fsync(fd)
        os.lseek(fd, 0, os.SEEK_SET)
        owned = bytearray()
        while True:
            chunk = os.read(fd, 65536)
            if not chunk:
                break
            owned.extend(chunk)
        info = os.fstat(fd)
        parsed, digest = parse_descriptor_owned_bytes(
            payload,
            bytes(owned),
            uid=info.st_uid,
            mode=info.st_mode,
        )
        return fd, parsed, digest
    except Exception:
        os.close(fd)
        raise


def stage_secured_input(
    source: Path,
    expected_sha256: str,
    directory: Path,
    directory_fd: int,
    target_name: str,
) -> Path:
    flags = os.O_RDONLY | os.O_NOFOLLOW
    try:
        source_fd = os.open(source, flags)
    except OSError as exc:
        raise GenerationError(f"secured input cannot be opened: {exc}") from exc
    try:
        before = os.fstat(source_fd)
        validate_owned_metadata(
            uid=before.st_uid,
            mode=before.st_mode,
            expected_kind="file",
            is_symlink=False,
        )
        payload = bytearray()
        while chunk := os.read(source_fd, 1024 * 1024):
            payload.extend(chunk)
        after = os.fstat(source_fd)
        if (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino):
            raise GenerationError("secured input changed while open")
        if hashlib.sha256(payload).hexdigest() != expected_sha256:
            raise GenerationError("secured input digest mismatch")
    finally:
        os.close(source_fd)

    target_fd = os.open(
        target_name,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
        0o600,
        dir_fd=directory_fd,
    )
    try:
        offset = 0
        while offset < len(payload):
            offset += os.write(target_fd, payload[offset:])
        os.fsync(target_fd)
    finally:
        os.close(target_fd)
    return directory / target_name


def build_pgbackrest_config(state: GenerationState) -> bytes:
    values = {
        "repo1-type": "s3",
        "repo1-path": state.repository_path,
        "repo1-s3-endpoint": state.repository_endpoint,
        "repo1-s3-bucket": state.repository_bucket,
        "repo1-s3-region": state.repository_region,
        "repo1-s3-uri-style": "path",
        "repo1-storage-verify-tls": "y",
        "repo1-cipher-type": "aes-256-cbc",
        "repo1-retention-full": "12",
        "repo1-retention-full-type": "count",
        "repo1-retention-archive": "12",
        "repo1-retention-archive-type": "full",
    }
    for value in values.values():
        if not value or "\n" in value or "\r" in value or "\x00" in value:
            raise GenerationError("repository public configuration is malformed")
    lines = ["[global]", *(f"{key}={values[key]}" for key in sorted(values))]
    lines.extend(("", f"[{state.stanza}]", ""))
    return "\n".join(lines).encode("ascii")


def validate_repository_secret(payload: bytes, state: GenerationState) -> dict[str, str]:
    value = strict_json_object(payload, "repository secret capability")
    required = {
        "schema_version",
        "endpoint_sha256",
        "bucket_sha256",
        "region_sha256",
        "s3_key",
        "s3_key_secret",
        "cipher_pass",
    }
    if set(value) != required:
        raise GenerationError("repository secret capability fields are not exact")
    expected = {
        "endpoint_sha256": hashlib.sha256(
            state.repository_endpoint.encode("ascii")
        ).hexdigest(),
        "bucket_sha256": hashlib.sha256(
            state.repository_bucket.encode("ascii")
        ).hexdigest(),
        "region_sha256": hashlib.sha256(
            state.repository_region.encode("ascii")
        ).hexdigest(),
    }
    if value["schema_version"] != 1 or any(
        value.get(key) != item for key, item in expected.items()
    ):
        raise GenerationError("repository secret capability target is not exact")
    secrets = {
        "PGBACKREST_REPO1_S3_KEY": value["s3_key"],
        "PGBACKREST_REPO1_S3_KEY_SECRET": value["s3_key_secret"],
        "PGBACKREST_REPO1_CIPHER_PASS": value["cipher_pass"],
    }
    if any(not isinstance(item, str) or not SAFE_SECRET.fullmatch(item) for item in secrets.values()):
        raise GenerationError("repository secret capability value is malformed")
    return secrets


def create_restore_secret_env(
    state: GenerationState, directory_fd: int
) -> tuple[int, dict[str, str], str]:
    payload = read_secured_once(REPOSITORY_SECRET, "repository secret capability")
    secrets = validate_repository_secret(payload, state)
    env_payload = "".join(
        f"{key}={secrets[key]}\n" for key in sorted(secrets)
    ).encode("ascii")
    fd = os.open(
        "restore-secret.env",
        os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
        0o600,
        dir_fd=directory_fd,
    )
    try:
        os.write(fd, env_payload)
        os.fsync(fd)
        os.lseek(fd, 0, os.SEEK_SET)
        info = os.fstat(fd)
        validate_owned_metadata(
            uid=info.st_uid,
            mode=info.st_mode,
            expected_kind="file",
            is_symlink=False,
        )
    except Exception:
        os.close(fd)
        raise
    public_identity = {
        "endpoint_sha256": hashlib.sha256(
            state.repository_endpoint.encode("ascii")
        ).hexdigest(),
        "bucket_sha256": hashlib.sha256(
            state.repository_bucket.encode("ascii")
        ).hexdigest(),
        "region_sha256": hashlib.sha256(
            state.repository_region.encode("ascii")
        ).hexdigest(),
        "key_attestation_sha256": state.restore_key_attestation_sha256,
        "secret_keys": sorted(secrets),
    }
    return fd, secrets, hashlib.sha256(canonical_json(public_identity)).hexdigest()


def project_state(packet: dict[str, Any]) -> GenerationState:
    required = {
        "generation",
        "procedure_manifest_sha256",
        "image_config_id",
        "image_repo_digest",
        "image_environment_sha256",
        "recovery_container",
        "recovery_container_id",
        "final_container",
        "final_container_id",
        "volume",
        "bootstrap_network",
        "locked_network",
        "restore_pg1_path",
        "database_pgdata",
        "repository_endpoint",
        "repository_bucket",
        "repository_region",
        "repository_path",
        "restore_key_attestation_sha256",
        "stanza",
        "repo",
        "selected_set_ref_sha256",
        "selected_set_info_sha256",
        "completed_at",
        "inventory_sha256",
        "measured_image_identity_sha256",
        "cloud_instance_id_sha256",
    }
    missing = required - set(packet)
    if missing:
        raise GenerationError(f"guard packet omits fields: {sorted(missing)}")
    return GenerationState(**{key: str(packet[key]) for key in required})


def run_checked(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[Any]:
    kwargs.setdefault("env", CLEAN_ENVIRONMENT)
    try:
        return subprocess.run(command, check=True, **kwargs)
    except (OSError, subprocess.CalledProcessError) as exc:
        raise GenerationError(f"command failed: {command[0]}") from exc


def exact_environment(entries: Any, label: str) -> tuple[list[str], dict[str, str]]:
    if not isinstance(entries, list):
        raise GenerationError(f"{label} environment is not exact")
    values: dict[str, str] = {}
    ordered: list[str] = []
    for item in entries:
        if not isinstance(item, str) or "=" not in item:
            raise GenerationError(f"{label} environment is malformed")
        key, value = item.split("=", 1)
        if not key or key in values:
            raise GenerationError(f"{label} environment contains duplicate keys")
        values[key] = value
        ordered.append(item)
    return ordered, values


def validate_restore_container(
    *,
    container: dict[str, Any],
    image: dict[str, Any],
    state: GenerationState,
    container_id: str,
    container_name: str,
    restore_command: list[str],
    config_path: Path,
    secrets: dict[str, str],
) -> dict[str, Any]:
    config = container.get("Config", {})
    host = container.get("HostConfig", {})
    networks = container.get("NetworkSettings", {}).get("Networks")
    mounts = container.get("Mounts")
    image_environment, _ = exact_environment(
        image.get("Config", {}).get("Env"), "restore image"
    )
    container_environment, container_values = exact_environment(
        config.get("Env"), "restore container"
    )
    if (
        hashlib.sha256(canonical_json(image_environment)).hexdigest()
        != state.image_environment_sha256
        or image.get("Id") != state.image_config_id
        or image.get("RepoDigests") != [state.image_repo_digest]
        or container.get("Id") != container_id
        or container.get("Name") != f"/{container_name}"
        or container.get("Image") != state.image_config_id
        or container.get("State", {}).get("Running") is not False
        or container.get("Path") != "pgbackrest"
        or container.get("Args") != restore_command
        or config.get("Image") != state.image_repo_digest
        or config.get("Hostname") != container_name
        or config.get("User") != image.get("Config", {}).get("User")
        or config.get("Entrypoint") != ["pgbackrest"]
        or config.get("Cmd") != restore_command
        or host.get("NetworkMode") != state.bootstrap_network
        or host.get("PortBindings") not in (None, {})
        or host.get("Privileged") is not False
        or not isinstance(networks, dict)
        or set(networks) != {state.bootstrap_network}
        or not isinstance(mounts, list)
        or len(mounts) != 2
    ):
        raise GenerationError("restore container identity/command/isolation is not exact")
    host_isolation_shape(host)

    secret_keys = set(secrets)
    if set(container_values) != {
        *(item.split("=", 1)[0] for item in image_environment),
        *secret_keys,
    } or any(container_values.get(key) != value for key, value in secrets.items()):
        raise GenerationError("restore container environment is not exact")
    public_environment = [
        item
        for item in container_environment
        if item.split("=", 1)[0] not in secret_keys
    ]
    if public_environment != image_environment:
        raise GenerationError("restore container inherited environment changed")

    normalized_mounts = {
        (
            str(mount.get("Type")),
            str(mount.get("Source", "")) if mount.get("Type") == "bind" else "",
            str(mount.get("Name", "")) if mount.get("Type") == "volume" else "",
            str(mount.get("Destination")),
            bool(mount.get("RW")),
        )
        for mount in mounts
    }
    expected_mounts = {
        ("volume", "", state.volume, state.restore_pg1_path, True),
        (
            "bind",
            str(config_path),
            "",
            "/etc/pgbackrest/pgbackrest.conf",
            False,
        ),
    }
    if normalized_mounts != expected_mounts or "docker.sock" in str(mounts):
        raise GenerationError("restore container mount identity is not exact")

    identity = {
        "container_id_sha256": hashlib.sha256(container_id.encode("ascii")).hexdigest(),
        "container_name": container_name,
        "image_config_id": state.image_config_id,
        "image_repo_digest": state.image_repo_digest,
        "image_environment_sha256": state.image_environment_sha256,
        "command_sha256": hashlib.sha256(canonical_json(restore_command)).hexdigest(),
        "public_environment_sha256": hashlib.sha256(
            canonical_json(public_environment)
        ).hexdigest(),
        "secret_keys": sorted(secret_keys),
        "network": state.bootstrap_network,
        "mounts_sha256": hashlib.sha256(
            canonical_json(sorted(normalized_mounts))
        ).hexdigest(),
        "published_ports": 0,
        "docker_socket_mounts": 0,
    }
    return identity


def docker_inspect_one(kind: str, reference: str) -> dict[str, Any]:
    completed = run_checked(
        ["docker", kind, "inspect", reference],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    try:
        values = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise GenerationError(f"Docker {kind} inspection is invalid JSON") from exc
    if (
        not isinstance(values, list)
        or len(values) != 1
        or not isinstance(values[0], dict)
    ):
        raise GenerationError(f"Docker {kind} inspection is ambiguous")
    return values[0]


def load_target_policy(state: GenerationState) -> tuple[dict[str, Any], str]:
    raw = read_secured_once(RUNNER_MANIFEST, "runner manifest")
    manifest = strict_json_object(raw, "runner manifest")
    target = manifest.get("target")
    required = {
        "repo_digest",
        "config_id",
        "path",
        "entrypoint",
        "cmd",
        "user",
        "working_dir",
        "image_environment",
        "labels",
        "hostname_template",
        "runtime",
        "apparmor_profile",
        "masked_paths",
        "readonly_paths",
        "readonly_rootfs",
        "tmpfs",
    }
    if (
        manifest.get("schema_version") != 3
        or manifest.get("status") != "APPROVED"
        or not isinstance(target, dict)
        or set(target) != required
        or target.get("repo_digest") != state.image_repo_digest
        or target.get("config_id") != state.image_config_id
        or not isinstance(target.get("path"), str)
        or not str(target["path"]).startswith("/")
        or not isinstance(target.get("entrypoint"), list)
        or not isinstance(target.get("cmd"), list)
        or not isinstance(target.get("user"), str)
        or not target["user"]
        or not isinstance(target.get("working_dir"), str)
        or not isinstance(target.get("image_environment"), list)
        or not isinstance(target.get("labels"), dict)
        or target.get("hostname_template") != "{target_name}"
        or target.get("runtime") != "runc"
        or not isinstance(target.get("apparmor_profile"), str)
        or not target["apparmor_profile"]
        or not isinstance(target.get("masked_paths"), list)
        or not isinstance(target.get("readonly_paths"), list)
        or target.get("readonly_rootfs") is not True
        or not isinstance(target.get("tmpfs"), dict)
        or set(target["tmpfs"]) != {"/tmp", "/var/run/postgresql"}
        or not all(
            isinstance(value, str) and value for value in target["tmpfs"].values()
        )
    ):
        raise GenerationError("target execution policy is not approved/exact")
    return target, hashlib.sha256(raw).hexdigest()


def inspect_sealed_target(
    *,
    state: GenerationState,
    target_policy: dict[str, Any],
    container_id: str,
    container_name: str,
    running: bool,
) -> dict[str, Any]:
    try:
        from postgres_restore_host_inventory import validate_sealed_target
    except ModuleNotFoundError:  # pragma: no cover - package import in unit tests
        from scripts.postgres_restore_host_inventory import validate_sealed_target
    container = docker_inspect_one("container", container_id)
    image = docker_inspect_one("image", str(container.get("Image", "")))
    rootfs_diff = run_checked(
        ["docker", "diff", container_id], capture_output=True
    ).stdout
    if rootfs_diff:
        raise GenerationError("sealed target writable layer is not pristine")
    try:
        return validate_sealed_target(
            container=container,
            image=image,
            expected_id=container_id,
            expected_name=container_name,
            expected_network=state.locked_network,
            expected_host_network_mode="none",
            expected_volume=state.volume,
            expected_pgdata=state.database_pgdata,
            target_policy=target_policy,
            generation=state.generation,
            running=running,
            forbidden_identifiers={"adapteng-ops-db", "postgres-adapteng-ops"},
        )
    except RuntimeError as exc:
        raise GenerationError("sealed target validation failed") from exc


def stable_target_identity(identity: dict[str, Any]) -> dict[str, Any]:
    ignored = {"running", "state_status"}
    return {key: value for key, value in identity.items() if key not in ignored}


def collect_provider_for_target(
    *,
    state: GenerationState,
    phase: str,
    container_id: str,
    target_identity: dict[str, Any],
    consumed_operations: set[str],
) -> dict[str, Any]:
    try:
        from postgres_restore_isolation_gate import collect_provider_operation
    except ModuleNotFoundError:  # pragma: no cover - package import in unit tests
        from scripts.postgres_restore_isolation_gate import collect_provider_operation
    return collect_provider_operation(
        generation=state.generation,
        phase=phase,
        target_container_id=container_id,
        target_image_identity_sha256=hashlib.sha256(
            canonical_json(stable_target_identity(target_identity))
        ).hexdigest(),
        consumed_operations=consumed_operations,
    )


def authorize_and_start_target(
    *,
    state: GenerationState,
    target_policy: dict[str, Any],
    container_id: str,
    container_name: str,
    phase: str,
    consumed_operations: set[str],
    inspect_target: Any = inspect_sealed_target,
    collect_provider: Any = collect_provider_for_target,
    start: Any = run_checked,
) -> tuple[dict[str, Any], dict[str, Any]]:
    pristine = inspect_target(
        state=state,
        target_policy=target_policy,
        container_id=container_id,
        container_name=container_name,
        running=False,
    )
    provider = collect_provider(
        state=state,
        phase=phase,
        container_id=container_id,
        target_identity=pristine,
        consumed_operations=consumed_operations,
    )
    remeasured = inspect_target(
        state=state,
        target_policy=target_policy,
        container_id=container_id,
        container_name=container_name,
        running=False,
    )
    if stable_target_identity(remeasured) != stable_target_identity(pristine):
        raise GenerationError("target changed after provider authorization")
    start(["docker", "start", container_id])
    running_identity = inspect_target(
        state=state,
        target_policy=target_policy,
        container_id=container_id,
        container_name=container_name,
        running=True,
    )
    if stable_target_identity(running_identity) != stable_target_identity(pristine):
        raise GenerationError("target changed during start")
    return provider, running_identity


def capture_guard_packet(args: argparse.Namespace) -> bytes:
    command = [
        sys.executable,
        str(GUARD),
        "--generation",
        args.generation,
        "--guard-config",
        args.guard_config,
        "--guard-config-sha256",
        args.guard_config_sha256,
        "--selected-set",
        args.selected_set,
        "--selected-info",
        args.selected_info,
        "--selected-info-sha256",
        args.selected_info_sha256,
        "--approved-image-manifest",
        args.approved_image_manifest,
        "--approved-image-manifest-sha256",
        args.approved_image_manifest_sha256,
        "--recovery-container-id",
        args.recovery_container_id,
        "--final-container-id",
        args.final_container_id,
        "--procedure-manifest-sha256",
        args.procedure_manifest_sha256,
    ]
    return run_checked(command, capture_output=True).stdout


def acquire_host_lock() -> int:
    if fcntl is None:
        raise GenerationError("scratch-host locking requires POSIX flock")
    require_root_owned(STATE_ROOT, "directory")
    fd = os.open(
        STATE_ROOT / "active-host.lock",
        os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW,
        0o600,
    )
    try:
        info = os.fstat(fd)
        validate_owned_metadata(
            uid=info.st_uid,
            mode=info.st_mode,
            expected_kind="file",
            is_symlink=False,
        )
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return fd
    except Exception:
        os.close(fd)
        raise GenerationError("another restore operation holds the scratch-host lock")


def load_provider_policy(value: dict[str, Any]) -> ProviderPolicy:
    required = {
        "schema_version",
        "status",
        "collector_id",
        "collector_version",
        "collector_sha256",
        "broker_id",
        "broker_version",
        "signature_algorithm",
        "public_key_pem",
        "public_key_pem_sha256",
        "owner_ssh_cidr_sha256",
        "account_context_sha256",
        "provider_target_config_sha256",
        "max_age_seconds",
    }
    if set(value) != required:
        raise GenerationError("provider manifest fields are not exact")
    if value["status"] != "APPROVED":
        raise GenerationError("provider measurement manifest is not approved")
    if (
        value["schema_version"] != 3
        or value["collector_id"] != "company-os-hetzner-locked-inventory"
        or value["collector_version"] != 2
        or value["broker_id"] != "company-os-hetzner-inventory-broker"
        or value["broker_version"] != 1
        or value["signature_algorithm"] != "ed25519"
        or value["max_age_seconds"] != 30
    ):
        raise GenerationError("provider measurement policy is not exact")
    collector_sha256 = str(value["collector_sha256"])
    public_key_pem = str(value["public_key_pem"])
    public_key_sha256 = str(value["public_key_pem_sha256"])
    owner_ssh_cidr_sha256 = str(value["owner_ssh_cidr_sha256"])
    account_context_sha256 = str(value["account_context_sha256"])
    provider_target_config_sha256 = str(value["provider_target_config_sha256"])
    if (
        hashlib.sha256(PROVIDER_COLLECTOR.read_bytes()).hexdigest()
        != collector_sha256
        or hashlib.sha256(public_key_pem.encode("utf-8")).hexdigest()
        != public_key_sha256
        or len(owner_ssh_cidr_sha256) != 64
        or any(character not in "0123456789abcdef" for character in owner_ssh_cidr_sha256)
        or len(account_context_sha256) != 64
        or any(character not in "0123456789abcdef" for character in account_context_sha256)
        or len(provider_target_config_sha256) != 64
        or any(
            character not in "0123456789abcdef"
            for character in provider_target_config_sha256
        )
    ):
        raise GenerationError("provider measurement identity digest mismatch")
    return ProviderPolicy(
        collector_id=str(value["collector_id"]),
        collector_version=int(value["collector_version"]),
        collector_sha256=collector_sha256,
        public_key_pem=public_key_pem,
        public_key_pem_sha256=public_key_sha256,
        owner_ssh_cidr_sha256=owner_ssh_cidr_sha256,
        account_context_sha256=account_context_sha256,
        provider_target_config_sha256=provider_target_config_sha256,
        max_age_seconds=int(value["max_age_seconds"]),
    )


def read_secured_once(path: Path, label: str) -> bytes:
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError as exc:
        raise GenerationError(f"{label} cannot be opened: {exc}") from exc
    try:
        info = os.fstat(fd)
        validate_owned_metadata(
            uid=info.st_uid,
            mode=info.st_mode,
            expected_kind="file",
            is_symlink=False,
        )
        payload = bytearray()
        while chunk := os.read(fd, 1024 * 1024):
            payload.extend(chunk)
        return bytes(payload)
    finally:
        os.close(fd)


def read_root_owned_member(path: Path, label: str) -> bytes:
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError as exc:
        raise GenerationError(f"{label} cannot be opened: {exc}") from exc
    try:
        info = os.fstat(fd)
        if (
            os.geteuid() != 0
            or info.st_uid != 0
            or not stat.S_ISREG(info.st_mode)
            or info.st_mode & 0o022
        ):
            raise GenerationError(f"{label} is not root-owned/read-only")
        payload = bytearray()
        while chunk := os.read(fd, 1024 * 1024):
            payload.extend(chunk)
        return bytes(payload)
    finally:
        os.close(fd)


def require_approved_manifest(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or value.get("status") != "APPROVED":
        raise GenerationError(f"{label} is NOT_CONFIGURED")
    return value


def validate_restore_acceptance(
    accepted: dict[str, Any],
    *,
    selected_set: str,
    selected_info_sha256: str,
    exporter_manifest_sha256: str,
) -> None:
    required = {
        "schema_version",
        "packet_kind",
        "mode",
        "status",
        "selected_set_ref_sha256",
        "selected_set_info_sha256",
        "completed_at",
        "scheduler_inventory_sha256",
        "scheduler_inventory_observed_at",
        "repository_inventory_sha256",
        "inventory_checked_at_utc",
        "completed_newer_fulls",
        "retention_full_count",
        "retention_valid_until",
        "latest_rollout_start_utc",
        "required_from_completion_through_utc",
        "authorization_status",
        "inventory_exporter_id",
        "inventory_exporter_version",
        "inventory_exporter_artifact_sha256",
        "inventory_exporter_manifest_sha256",
        "weekly_cadence_seconds",
        "weekly_slot_count",
        "repository_write_capability_sha256",
        "capability_inventory_sha256",
        "full_job_identity_sha256",
        "differential_job_identity_sha256",
    }
    if set(accepted) != required:
        raise GenerationError("accepted retention packet fields are not exact")
    if (
        accepted["schema_version"] != 1
        or accepted["packet_kind"] != "ACCEPTED_RETENTION"
        or accepted["mode"] != "acceptance"
        or accepted["status"] != "RETENTION_ACCEPTED"
        or accepted["authorization_status"] != "NOT_AUTHORIZED"
        or accepted["retention_full_count"] != 12
        or accepted["weekly_cadence_seconds"] != 604800
        or accepted["weekly_slot_count"] != 12
        or accepted["selected_set_info_sha256"] != selected_info_sha256
        or accepted["selected_set_ref_sha256"]
        != hashlib.sha256(selected_set.encode("utf-8")).hexdigest()
        or accepted["inventory_exporter_manifest_sha256"]
        != exporter_manifest_sha256
    ):
        raise GenerationError("accepted retention packet is not exact/bound")


def load_approved_inputs(
    args: argparse.Namespace,
) -> tuple[bytes, ProviderPolicy, bytes, dict[str, Any], str]:
    procedure_raw = read_root_owned_member(PROCEDURE_MANIFEST, "procedure manifest")
    if hashlib.sha256(procedure_raw).hexdigest() != args.procedure_manifest_sha256:
        raise GenerationError("procedure manifest digest mismatch")
    procedure = json.loads(procedure_raw)
    artifacts = procedure.get("artifacts") if isinstance(procedure, dict) else None
    git_blobs = procedure.get("git_blobs") if isinstance(procedure, dict) else None
    git_modes = procedure.get("git_modes") if isinstance(procedure, dict) else None
    if (
        b"\r" in procedure_raw
        or b"\0" in procedure_raw
        or not procedure_raw.endswith(b"\n")
        or procedure.get("schema_version") != 2
        or procedure.get("docker_inspect_schema_version") != 1
        or not isinstance(artifacts, dict)
        or not isinstance(git_blobs, dict)
        or not isinstance(git_modes, dict)
        or set(artifacts) != set(git_blobs)
        or set(artifacts) != set(git_modes)
        or hashlib.sha256(
            canonical_json({"git_blobs": git_blobs, "git_modes": git_modes})
        ).hexdigest()
        != procedure.get("member_tree_sha256")
    ):
        raise GenerationError("procedure manifest Git-object binding is incomplete")
    root = SCRIPT_DIR.parent
    for member, expected in artifacts.items():
        if not isinstance(member, str) or not member.startswith("scripts/"):
            raise GenerationError("procedure manifest member path is not exact")
        payload = read_root_owned_member(root / member, member)
        git_oid = hashlib.sha1(
            f"blob {len(payload)}\0".encode("ascii") + payload
        ).hexdigest()
        if (
            not isinstance(expected, str)
            or b"\r" in payload
            or b"\0" in payload
            or not payload.endswith(b"\n")
            or hashlib.sha256(payload).hexdigest() != expected
            or git_blobs.get(member) != git_oid
            or git_modes.get(member) not in {"100644", "100755"}
        ):
            raise GenerationError(f"{member} digest differs from sealed procedure")

    runner_raw = read_root_owned_member(RUNNER_MANIFEST, "runner manifest")
    exporter_raw = read_root_owned_member(EXPORTER_MANIFEST, "exporter manifest")
    provider_raw = read_root_owned_member(PROVIDER_MANIFEST, "provider manifest")
    require_approved_manifest(json.loads(runner_raw), "runner manifest")
    exporter = require_approved_manifest(
        json.loads(exporter_raw), "inventory exporter manifest"
    )
    provider_value = require_approved_manifest(
        json.loads(provider_raw), "provider manifest"
    )
    provider_policy = load_provider_policy(provider_value)

    accepted_raw = read_secured_once(ACCEPTED_RETENTION, "accepted retention packet")
    accepted_sha256 = hashlib.sha256(accepted_raw).hexdigest()
    if accepted_sha256 != args.accepted_retention_packet_sha256:
        raise GenerationError("accepted retention packet digest mismatch")
    accepted = json.loads(accepted_raw)
    if not isinstance(accepted, dict) or canonical_json(accepted) != accepted_raw:
        raise GenerationError("accepted retention packet is not canonical")
    validate_restore_acceptance(
        accepted,
        selected_set=args.selected_set,
        selected_info_sha256=args.selected_info_sha256,
        exporter_manifest_sha256=hashlib.sha256(exporter_raw).hexdigest(),
    )
    return provider_raw, provider_policy, accepted_raw, accepted, accepted_sha256


def write_owned_bytes(directory_fd: int, name: str, payload: bytes) -> None:
    fd = os.open(
        name,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
        0o600,
        dir_fd=directory_fd,
    )
    try:
        offset = 0
        while offset < len(payload):
            offset += os.write(fd, payload[offset:])
        os.fsync(fd)
    finally:
        os.close(fd)


def validate_locked_measurement(
    packet: dict[str, Any],
    generation: str,
    now: datetime,
    expected_server_ref_sha256: str | None = None,
    policy: ProviderPolicy | None = None,
) -> None:
    required = {
        "schema_version",
        "collector_id",
        "collector_version",
        "collector_sha256",
        "status",
        "generation",
        "observed_at",
        "server_ref_sha256",
        "firewall_ref_sha256",
        "locked_policy_sha256",
        "owner_ssh_cidr_sha256",
        "locked_firewall_attached",
        "other_firewalls_attached",
        "private_networks_attached",
        "private_network_inventory_sha256",
        "floating_ips_attached",
        "broker_id",
        "broker_version",
        "broker_response_sha256",
        "operation_id",
        "challenge_sha256",
        "phase",
        "target_container_id_sha256",
        "target_image_identity_sha256",
        "requested_at",
    }
    if set(packet) != required or packet.get("schema_version") != 2:
        raise GenerationError("provider measurement fields are not exact v2")
    if packet.get("status") != "LOCKED_CURRENT":
        raise GenerationError("provider collector did not prove locked state")
    if packet.get("generation") != generation:
        raise GenerationError("provider measurement generation mismatch")
    observed = datetime.strptime(str(packet.get("observed_at")), TIMESTAMP).replace(
        tzinfo=timezone.utc
    )
    age = now.astimezone(timezone.utc) - observed
    max_age = policy.max_age_seconds if policy is not None else 30
    if age < timedelta(0) or age > timedelta(seconds=max_age):
        raise GenerationError("provider measurement is not current")
    if packet.get("locked_firewall_attached") is not True or (
        packet.get("other_firewalls_attached") != 0
        or packet.get("private_networks_attached") != 0
        or packet.get("private_network_inventory_sha256")
        != hashlib.sha256(canonical_json([])).hexdigest()
        or packet.get("floating_ips_attached") != 0
        or packet.get("broker_id") != "company-os-hetzner-inventory-broker"
        or packet.get("broker_version") != 1
        or len(str(packet.get("broker_response_sha256"))) != 64
    ):
        raise GenerationError("provider measurement does not prove exclusive lock")
    if (
        expected_server_ref_sha256 is not None
        and packet.get("server_ref_sha256") != expected_server_ref_sha256
    ):
        raise GenerationError("provider measurement server identity mismatch")
    if policy is not None and (
        packet.get("collector_id") != policy.collector_id
        or packet.get("collector_version") != policy.collector_version
        or packet.get("collector_sha256") != policy.collector_sha256
        or packet.get("owner_ssh_cidr_sha256")
        != policy.owner_ssh_cidr_sha256
    ):
        raise GenerationError("provider measurement collector identity mismatch")


def wait_postgres(container: str, attempts: int) -> None:
    for _ in range(attempts):
        completed = subprocess.run(
            [
                "docker",
                "exec",
                "-u",
                "postgres",
                container,
                "/usr/lib/postgresql/16/bin/pg_isready",
                "--quiet",
            ],
            env=CLEAN_ENVIRONMENT,
        )
        if completed.returncode == 0:
            return
        time.sleep(1)
    raise GenerationError("PostgreSQL did not become ready")


def validate_recovery_evidence(evidence: dict[str, str]) -> dict[str, str]:
    required = {
        "measured_runner_identity_sha256",
        "database_container_identity_sha256",
        "pre_sql_host_inventory_sha256",
        "post_sql_host_inventory_sha256",
        "pre_sql_provider_inventory_sha256",
        "post_sql_provider_inventory_sha256",
        "runner_exit",
    }
    if not required <= set(evidence) or evidence.get("runner_exit") != "0":
        raise GenerationError("sealed recovery assertion evidence is incomplete")
    return evidence


def assert_recovery_complete(
    generation: str, target_kind: str, procedure_manifest_sha256: str
) -> dict[str, str]:
    sql = (
        "DO $assert$ BEGIN IF pg_is_in_recovery() THEN "
        "RAISE EXCEPTION 'restore is still in recovery'; END IF; END $assert$;\n"
    ).encode("ascii")
    completed = run_checked(
        [
            sys.executable,
            str(RUNNER),
            "assert-recovery",
            "--generation",
            generation,
            "--target-kind",
            target_kind,
            "--procedure-manifest-sha256",
            procedure_manifest_sha256,
        ],
        input=sql,
        capture_output=True,
        pass_fds=(PROVIDER_BROKER_REQUEST_FD, PROVIDER_BROKER_RESPONSE_FD),
    )
    evidence: dict[str, str] = {}
    for line in completed.stderr.decode("utf-8").splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            evidence[key] = value
    return validate_recovery_evidence(evidence)


def execute(args: argparse.Namespace) -> dict[str, str]:
    (
        provider_manifest_raw,
        _provider_policy,
        accepted_raw,
        accepted,
        accepted_sha256,
    ) = load_approved_inputs(args)
    host_lock_fd = acquire_host_lock()
    directory, directory_fd = create_generation_directory(args.generation)
    guard_fd = -1
    secret_env_fd = -1
    try:
        write_owned_bytes(
            directory_fd,
            "accepted-retention.json",
            accepted_raw,
        )
        guard_bytes = capture_guard_packet(args)
        guard_fd, packet, guard_sha256 = write_and_parse_once(
            directory_fd, "guard-packet.json", guard_bytes
        )
        state = project_state(packet)
        if state.generation != args.generation:
            raise GenerationError("guard generation changed")
        if accepted.get("completed_at") != state.completed_at:
            raise GenerationError("accepted retention completion differs from backup")
        target_policy, runner_manifest_sha256 = load_target_policy(state)
        config_raw = build_pgbackrest_config(state)
        write_owned_bytes(directory_fd, "pgbackrest.conf", config_raw)
        staged_config = directory / "pgbackrest.conf"
        (
            secret_env_fd,
            restore_secrets,
            repository_capability_sha256,
        ) = create_restore_secret_env(state, directory_fd)
        restore_name = f"adapteng-pgbackrest-{args.generation.lower()}"
        restore_command = [
            "--config=/etc/pgbackrest/pgbackrest.conf",
            f"--stanza={state.stanza}",
            f"--repo={state.repo}",
            f"--pg1-path={state.restore_pg1_path}",
            f"--set={args.selected_set}",
            "--type=immediate",
            "--target-action=promote",
            "restore",
        ]
        created = run_checked(
            [
                "docker",
                "create",
                "--name",
                restore_name,
                "--hostname",
                restore_name,
                "--network",
                state.bootstrap_network,
                "--mount",
                f"type=volume,src={state.volume},dst={state.restore_pg1_path}",
                "--mount",
                (
                    f"type=bind,src={staged_config},"
                    "dst=/etc/pgbackrest/pgbackrest.conf,readonly"
                ),
                "--env-file",
                f"/proc/self/fd/{secret_env_fd}",
                "--entrypoint",
                "pgbackrest",
                state.image_repo_digest,
                *restore_command,
            ],
            capture_output=True,
            pass_fds=(secret_env_fd,),
        )
        restore_id = created.stdout.decode("ascii").strip()
        if not restore_id:
            raise GenerationError("Docker did not return the restore container ID")
        restore_container = docker_inspect_one("container", restore_id)
        restore_image = docker_inspect_one(
            "image", str(restore_container.get("Image", ""))
        )
        restore_identity = validate_restore_container(
            container=restore_container,
            image=restore_image,
            state=state,
            container_id=restore_id,
            container_name=restore_name,
            restore_command=restore_command,
            config_path=staged_config,
            secrets=restore_secrets,
        )
        restore_reinspection = docker_inspect_one("container", restore_id)
        if (
            validate_restore_container(
                container=restore_reinspection,
                image=docker_inspect_one(
                    "image", str(restore_reinspection.get("Image", ""))
                ),
                state=state,
                container_id=restore_id,
                container_name=restore_name,
                restore_command=restore_command,
                config_path=staged_config,
                secrets=restore_secrets,
            )
            != restore_identity
        ):
            raise GenerationError("restore container identity changed before start")
        restore_container_identity_sha256 = hashlib.sha256(
            canonical_json(restore_identity)
        ).hexdigest()
        run_checked(["docker", "start", "--attach", restore_id])
        run_checked(["docker", "rm", restore_id])
        run_checked(["docker", "network", "rm", state.bootstrap_network])

        consumed_provider_operations: set[str] = set()
        run_checked(
            [
                "docker",
                "network",
                "connect",
                state.locked_network,
                state.recovery_container_id,
            ]
        )
        recovery_provider, recovery_running = authorize_and_start_target(
            state=state,
            target_policy=target_policy,
            container_id=state.recovery_container_id,
            container_name=state.recovery_container,
            phase="TARGET_START_RECOVERY",
            consumed_operations=consumed_provider_operations,
        )
        wait_postgres(state.recovery_container_id, 120)
        recovery_runner = assert_recovery_complete(
            args.generation, "recovery", state.procedure_manifest_sha256
        )
        run_checked(["docker", "stop", "--time", "30", state.recovery_container_id])
        run_checked(["docker", "rm", state.recovery_container_id])

        run_checked(
            [
                "docker",
                "network",
                "connect",
                state.locked_network,
                state.final_container_id,
            ]
        )
        final_provider, final_running = authorize_and_start_target(
            state=state,
            target_policy=target_policy,
            container_id=state.final_container_id,
            container_name=state.final_container,
            phase="TARGET_START_FINAL",
            consumed_operations=consumed_provider_operations,
        )
        wait_postgres(state.final_container_id, 60)
        final_runner = assert_recovery_complete(
            args.generation, "final", state.procedure_manifest_sha256
        )

        locked_inventory = {
            "schema_version": 1,
            "status": "LOCKED_LOCAL_CURRENT",
            "generation": args.generation,
            "recovery_provider_operation_sha256": recovery_provider[
                "operation_binding_sha256"
            ],
            "final_provider_operation_sha256": final_provider[
                "operation_binding_sha256"
            ],
            "recovery_target_identity_sha256": hashlib.sha256(
                canonical_json(stable_target_identity(recovery_running))
            ).hexdigest(),
            "final_target_identity_sha256": hashlib.sha256(
                canonical_json(stable_target_identity(final_running))
            ).hexdigest(),
            "recovery_runner": recovery_runner,
            "final_runner": final_runner,
        }
        locked_inventory_raw = canonical_json(locked_inventory)
        locked_inventory_sha256 = hashlib.sha256(locked_inventory_raw).hexdigest()
        write_owned_bytes(
            directory_fd, "locked-local-inventory.json", locked_inventory_raw
        )
        return {
            "generation": args.generation,
            "procedure_manifest_sha256": state.procedure_manifest_sha256,
            "guard_packet_sha256": guard_sha256,
            "provider_isolation_sha256": final_provider["packet_sha256"],
            "provider_manifest_sha256": hashlib.sha256(
                provider_manifest_raw
            ).hexdigest(),
            "runner_manifest_sha256": runner_manifest_sha256,
            "accepted_retention_packet_sha256": accepted_sha256,
            "repository_configuration_sha256": hashlib.sha256(
                config_raw
            ).hexdigest(),
            "repository_capability_identity_sha256": repository_capability_sha256,
            "restore_container_identity_sha256": (
                restore_container_identity_sha256
            ),
            "locked_local_inventory_sha256": locked_inventory_sha256,
            "inventory_sha256": state.inventory_sha256,
            "measured_image_identity_sha256": (
                state.measured_image_identity_sha256
            ),
            "selected_set_ref_sha256": state.selected_set_ref_sha256,
            "selected_set_info_sha256": state.selected_set_info_sha256,
            "completed_at": state.completed_at,
            "status": "RESTORED_LOCKED_READY",
        }
    finally:
        for fd in (secret_env_fd, guard_fd, directory_fd, host_lock_fd):
            if fd >= 0:
                os.close(fd)


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser()
    value.add_argument("--generation", choices=("A", "B", "C"), required=True)
    value.add_argument("--guard-config", required=True)
    value.add_argument("--guard-config-sha256", required=True)
    value.add_argument("--selected-set", required=True)
    value.add_argument("--selected-info", required=True)
    value.add_argument("--selected-info-sha256", required=True)
    value.add_argument("--approved-image-manifest", required=True)
    value.add_argument("--approved-image-manifest-sha256", required=True)
    value.add_argument("--recovery-container-id", required=True)
    value.add_argument("--final-container-id", required=True)
    value.add_argument("--procedure-manifest-sha256", required=True)
    value.add_argument("--accepted-retention-packet-sha256", required=True)
    return value


def main() -> int:
    try:
        result = execute(parser().parse_args())
        for key, value in result.items():
            print(f"{key}={value}")
        return 0
    except (GenerationError, OSError, UnicodeError, json.JSONDecodeError) as exc:
        print(f"STOP: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
