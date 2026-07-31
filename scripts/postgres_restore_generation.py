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


SCRIPT_DIR = Path(__file__).resolve().parent
PROCEDURE_MANIFEST = SCRIPT_DIR / "postgres_restore_procedure_manifest.json"
GUARD = SCRIPT_DIR / "postgres_restore_guard.py"
PROVIDER_COLLECTOR = SCRIPT_DIR / "postgres_restore_provider_inventory.py"
PROVIDER_MANIFEST = SCRIPT_DIR / "postgres_restore_provider_manifest.json"
RUNNER_MANIFEST = SCRIPT_DIR / "postgres_restore_runner_manifest.json"
EXPORTER_MANIFEST = SCRIPT_DIR / "postgres_restore_inventory_exporter_manifest.json"
PROVIDER_INBOX = Path("/run/adapteng/postgres-restore-provider")
STATE_ROOT = Path("/var/lib/adapteng/postgres-restore-rehearsal")
ACCEPTED_RETENTION = STATE_ROOT / "retention" / "accepted.json"
TIMESTAMP = "%Y-%m-%dT%H:%M:%SZ"


class GenerationError(RuntimeError):
    """Fail-closed generation orchestration error."""


def canonical_json(value: Any) -> bytes:
    return (
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        .encode("ascii")
        + b"\n"
    )


@dataclass(frozen=True)
class GenerationState:
    generation: str
    procedure_manifest_sha256: str
    image_config_id: str
    recovery_container: str
    final_container: str
    volume: str
    bootstrap_network: str
    locked_network: str
    restore_pg1_path: str
    repository_config_path: str
    repository_config_sha256: str
    restore_env_path: str
    restore_env_sha256: str
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


def project_state(packet: dict[str, Any]) -> GenerationState:
    required = {
        "generation",
        "procedure_manifest_sha256",
        "image_config_id",
        "recovery_container",
        "final_container",
        "volume",
        "bootstrap_network",
        "locked_network",
        "restore_pg1_path",
        "repository_config_path",
        "repository_config_sha256",
        "restore_env_path",
        "restore_env_sha256",
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
    try:
        return subprocess.run(command, check=True, **kwargs)
    except (OSError, subprocess.CalledProcessError) as exc:
        raise GenerationError(f"command failed: {command[0]}") from exc


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
        "--procedure-manifest-sha256",
        args.procedure_manifest_sha256,
    ]
    return run_checked(command, capture_output=True).stdout


def load_provider_policy(value: dict[str, Any]) -> ProviderPolicy:
    required = {
        "schema_version",
        "status",
        "collector_id",
        "collector_version",
        "collector_sha256",
        "signature_algorithm",
        "public_key_pem",
        "public_key_pem_sha256",
        "owner_ssh_cidr_sha256",
        "max_age_seconds",
    }
    if set(value) != required:
        raise GenerationError("provider manifest fields are not exact")
    if value["status"] != "APPROVED":
        raise GenerationError("provider measurement manifest is not approved")
    if (
        value["schema_version"] != 1
        or value["collector_id"] != "company-os-hetzner-locked-inventory"
        or value["collector_version"] != 1
        or value["signature_algorithm"] != "ed25519"
        or value["max_age_seconds"] != 120
    ):
        raise GenerationError("provider measurement policy is not exact")
    collector_sha256 = str(value["collector_sha256"])
    public_key_pem = str(value["public_key_pem"])
    public_key_sha256 = str(value["public_key_pem_sha256"])
    owner_ssh_cidr_sha256 = str(value["owner_ssh_cidr_sha256"])
    if (
        hashlib.sha256(PROVIDER_COLLECTOR.read_bytes()).hexdigest()
        != collector_sha256
        or hashlib.sha256(public_key_pem.encode("utf-8")).hexdigest()
        != public_key_sha256
        or len(owner_ssh_cidr_sha256) != 64
        or any(character not in "0123456789abcdef" for character in owner_ssh_cidr_sha256)
    ):
        raise GenerationError("provider measurement identity digest mismatch")
    return ProviderPolicy(
        collector_id=str(value["collector_id"]),
        collector_version=int(value["collector_version"]),
        collector_sha256=collector_sha256,
        public_key_pem=public_key_pem,
        public_key_pem_sha256=public_key_sha256,
        owner_ssh_cidr_sha256=owner_ssh_cidr_sha256,
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
    if not isinstance(artifacts, dict):
        raise GenerationError("procedure manifest artifacts are missing")
    root = SCRIPT_DIR.parent
    for member, expected in artifacts.items():
        if not isinstance(member, str) or not member.startswith("scripts/"):
            raise GenerationError("procedure manifest member path is not exact")
        payload = read_root_owned_member(root / member, member)
        if not isinstance(expected, str) or hashlib.sha256(payload).hexdigest() != expected:
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


def wait_for_locked_provider_state(
    generation: str,
    expected_server_ref_sha256: str,
    policy: ProviderPolicy,
    directory: Path,
    directory_fd: int,
) -> tuple[bytes, dict[str, Any]]:
    deadline = time.monotonic() + 600
    last_error = ""
    packet_source = PROVIDER_INBOX / f"generation-{generation}.json"
    signature_source = PROVIDER_INBOX / f"generation-{generation}.sig"
    while time.monotonic() < deadline:
        try:
            packet_bytes = read_secured_once(packet_source, "provider packet")
            signature_bytes = read_secured_once(
                signature_source, "provider packet signature"
            )
        except GenerationError as exc:
            last_error = str(exc)
            time.sleep(5)
            continue
        write_owned_bytes(directory_fd, "provider-signed.json", packet_bytes)
        write_owned_bytes(directory_fd, "provider-signed.sig", signature_bytes)
        public_key = policy.public_key_pem.encode("utf-8")
        write_owned_bytes(directory_fd, "provider-public-key.pem", public_key)
        run_checked(
            [
                "openssl",
                "pkeyutl",
                "-verify",
                "-pubin",
                "-inkey",
                str(directory / "provider-public-key.pem"),
                "-rawin",
                "-in",
                str(directory / "provider-signed.json"),
                "-sigfile",
                str(directory / "provider-signed.sig"),
            ],
            capture_output=True,
        )
        try:
            packet = json.loads(packet_bytes)
        except json.JSONDecodeError as exc:
            raise GenerationError("signed provider packet is invalid JSON") from exc
        validate_locked_measurement(
            packet,
            generation,
            datetime.now(timezone.utc).replace(microsecond=0),
            expected_server_ref_sha256,
            policy,
        )
        return packet_bytes, packet
    raise GenerationError(f"locked provider state was not measured: {last_error}")


def validate_locked_measurement(
    packet: dict[str, Any],
    generation: str,
    now: datetime,
    expected_server_ref_sha256: str | None = None,
    policy: ProviderPolicy | None = None,
) -> None:
    if packet.get("status") != "LOCKED_CURRENT":
        raise GenerationError("provider collector did not prove locked state")
    if packet.get("generation") != generation:
        raise GenerationError("provider measurement generation mismatch")
    observed = datetime.strptime(str(packet.get("observed_at")), TIMESTAMP).replace(
        tzinfo=timezone.utc
    )
    age = now.astimezone(timezone.utc) - observed
    max_age = policy.max_age_seconds if policy is not None else 120
    if age < timedelta(0) or age > timedelta(seconds=max_age):
        raise GenerationError("provider measurement is not current")
    if packet.get("locked_firewall_attached") is not True or (
        packet.get("other_firewalls_attached") != 0
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


def assert_container_locked(
    container: str, locked_network: str, expected_volume: str
) -> dict[str, Any]:
    completed = run_checked(
        ["docker", "container", "inspect", container],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    value = json.loads(completed.stdout)
    if not isinstance(value, list) or len(value) != 1:
        raise GenerationError("locked container inspection is ambiguous")
    item = value[0]
    networks = item.get("NetworkSettings", {}).get("Networks")
    if not isinstance(networks, dict) or set(networks) != {locked_network}:
        raise GenerationError("SQL container is not only on the locked network")
    if item.get("HostConfig", {}).get("PortBindings") not in (None, {}):
        raise GenerationError("SQL container has published ports")
    for mount in item.get("Mounts", []):
        if "docker.sock" in str(mount.get("Source", "")) or "docker.sock" in str(
            mount.get("Destination", "")
        ):
            raise GenerationError("SQL container mounts the Docker socket")
    volume_mounts = [
        mount
        for mount in item.get("Mounts", [])
        if mount.get("Type") == "volume" and mount.get("Name") == expected_volume
    ]
    if len(volume_mounts) != 1:
        raise GenerationError("locked container volume identity changed")
    endpoint = networks[locked_network]
    if not isinstance(endpoint, dict):
        raise GenerationError("locked container endpoint inventory is invalid")
    return {
        "container_ref_sha256": hashlib.sha256(
            str(item.get("Id", "")).encode("utf-8")
        ).hexdigest(),
        "locked_network": locked_network,
        "network_id_sha256": hashlib.sha256(
            str(endpoint.get("NetworkID", "")).encode("utf-8")
        ).hexdigest(),
        "endpoint_id_sha256": hashlib.sha256(
            str(endpoint.get("EndpointID", "")).encode("utf-8")
        ).hexdigest(),
        "published_ports": 0,
        "docker_socket_mounts": 0,
        "volume_ref_sha256": hashlib.sha256(
            expected_volume.encode("utf-8")
        ).hexdigest(),
    }


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
            ]
        )
        if completed.returncode == 0:
            return
        time.sleep(1)
    raise GenerationError("PostgreSQL did not become ready")


def assert_recovery_complete(container: str) -> None:
    sql = (
        "DO $assert$ BEGIN IF pg_is_in_recovery() THEN "
        "RAISE EXCEPTION 'restore is still in recovery'; END IF; END $assert$;\n"
    ).encode("ascii")
    run_checked(
        [
            "docker",
            "exec",
            "-i",
            "-u",
            "postgres",
            container,
            "/usr/lib/postgresql/16/bin/psql",
            "--dbname=adapteng_ops",
            "--no-psqlrc",
            "-v",
            "ON_ERROR_STOP=1",
        ],
        input=sql,
    )


def execute(args: argparse.Namespace) -> dict[str, str]:
    (
        provider_manifest_raw,
        provider_policy,
        accepted_raw,
        accepted,
        accepted_sha256,
    ) = load_approved_inputs(args)
    directory, directory_fd = create_generation_directory(args.generation)
    guard_fd = -1
    provider_fd = -1
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
        staged_config = stage_secured_input(
            Path(state.repository_config_path),
            state.repository_config_sha256,
            directory,
            directory_fd,
            "pgbackrest.conf",
        )
        staged_env = stage_secured_input(
            Path(state.restore_env_path),
            state.restore_env_sha256,
            directory,
            directory_fd,
            "restore.env",
        )

        run_checked(
            [
                "docker",
                "run",
                "--rm",
                "--name",
                f"adapteng-pgbackrest-{args.generation.lower()}",
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
                str(staged_env),
                "--entrypoint",
                "pgbackrest",
                state.image_config_id,
                "--config=/etc/pgbackrest/pgbackrest.conf",
                f"--stanza={state.stanza}",
                f"--repo={state.repo}",
                f"--pg1-path={state.restore_pg1_path}",
                f"--set={args.selected_set}",
                "--type=immediate",
                "--target-action=promote",
                "restore",
            ]
        )

        provider_bytes, provider_packet = wait_for_locked_provider_state(
            args.generation,
            state.cloud_instance_id_sha256,
            provider_policy,
            directory,
            directory_fd,
        )
        provider_fd, _, provider_sha256 = write_and_parse_once(
            directory_fd, "provider-isolation.json", provider_bytes
        )
        if provider_packet.get("generation") != args.generation:
            raise GenerationError("provider measurement generation mismatch")

        run_checked(
            [
                "docker",
                "network",
                "connect",
                state.locked_network,
                state.recovery_container,
            ]
        )
        recovery_locked = assert_container_locked(
            state.recovery_container, state.locked_network, state.volume
        )
        run_checked(["docker", "start", state.recovery_container])
        wait_postgres(state.recovery_container, 120)
        assert_recovery_complete(state.recovery_container)
        run_checked(["docker", "stop", "--time", "30", state.recovery_container])
        run_checked(["docker", "rm", state.recovery_container])

        run_checked(
            [
                "docker",
                "network",
                "connect",
                state.locked_network,
                state.final_container,
            ]
        )
        final_locked = assert_container_locked(
            state.final_container, state.locked_network, state.volume
        )
        run_checked(["docker", "start", state.final_container])
        wait_postgres(state.final_container, 60)
        assert_recovery_complete(state.final_container)

        locked_inventory = {
            "schema_version": 1,
            "status": "LOCKED_LOCAL_CURRENT",
            "generation": args.generation,
            "provider_isolation_sha256": provider_sha256,
            "recovery": recovery_locked,
            "final": final_locked,
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
            "provider_isolation_sha256": provider_sha256,
            "provider_manifest_sha256": hashlib.sha256(
                provider_manifest_raw
            ).hexdigest(),
            "accepted_retention_packet_sha256": accepted_sha256,
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
        for fd in (provider_fd, guard_fd, directory_fd):
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
