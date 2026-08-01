#!/usr/bin/env python3
"""Verify a fresh signed provider lock measurement on the current scratch host."""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import stat
import subprocess
import sys
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    import fcntl
except ModuleNotFoundError:  # pragma: no cover - production is Linux
    fcntl = None  # type: ignore[assignment]

try:
    from postgres_restore_generation import (
        GenerationError,
        load_provider_policy,
        validate_locked_measurement,
    )
except ModuleNotFoundError:  # pragma: no cover - package import in unit tests
    from scripts.postgres_restore_generation import (
        GenerationError,
        load_provider_policy,
        validate_locked_measurement,
    )


SCRIPT_DIR = Path(__file__).resolve().parent
PROVIDER_MANIFEST = SCRIPT_DIR / "postgres_restore_provider_manifest.json"
PROVIDER_COLLECTOR = SCRIPT_DIR / "postgres_restore_provider_inventory.py"
CLOUD_METADATA = Path("/run/cloud-init/instance-data.json")
OWNER_CIDR = Path("/run/secrets/postgres-restore-owner-ssh-cidr")
STATE_ROOT = Path("/var/lib/adapteng/postgres-restore-rehearsal")
CLEAN_ENVIRONMENT = {
    "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
    "LANG": "C",
    "LC_ALL": "C",
}
TIMESTAMP = "%Y-%m-%dT%H:%M:%SZ"
PROVIDER_BROKER_REQUEST_FD = 197
PROVIDER_BROKER_RESPONSE_FD = 198


class IsolationGateError(RuntimeError):
    """Fail-closed signed isolation measurement error."""


def canonical_json(value: Any) -> bytes:
    return (
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        .encode("ascii")
        + b"\n"
    )


def secure_bytes(path: Path, label: str, *, private: bool) -> bytes:
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        info = os.fstat(descriptor)
        forbidden = 0o077 if private else 0o022
        if (
            os.geteuid() != 0
            or info.st_uid != 0
            or not stat.S_ISREG(info.st_mode)
            or info.st_mode & forbidden
        ):
            raise IsolationGateError(f"{label} ownership/mode is not secure")
        payload = bytearray()
        while chunk := os.read(descriptor, 65536):
            payload.extend(chunk)
        return bytes(payload)
    finally:
        os.close(descriptor)


def current_server_identity() -> tuple[str, str]:
    raw = secure_bytes(CLOUD_METADATA, "cloud instance metadata", private=False)
    value = json.loads(raw)
    if not isinstance(value, dict) or not str(value.get("instance_id", "")).strip():
        raise IsolationGateError("cloud instance metadata lacks instance_id")
    instance_id = str(value["instance_id"]).strip()
    if not instance_id.isdecimal() or int(instance_id) <= 0:
        raise IsolationGateError("cloud instance metadata instance_id is invalid")
    return instance_id, hashlib.sha256(instance_id.encode("utf-8")).hexdigest()


def canonical_operation_request(
    *,
    generation: str,
    phase: str,
    target_container_id: str,
    target_image_identity_sha256: str,
    nonce: bytes,
    requested_at: datetime,
) -> dict[str, Any]:
    if generation not in {"A", "B", "C"}:
        raise IsolationGateError("provider operation generation is not exact")
    if phase not in {
        "TARGET_START_RECOVERY",
        "TARGET_START_FINAL",
        "PRE_SQL",
        "POST_SQL",
    }:
        raise IsolationGateError("provider operation phase is not exact")
    if (
        len(nonce) != 32
        or not target_container_id
        or len(target_image_identity_sha256) != 64
        or any(
            character not in "0123456789abcdef"
            for character in target_image_identity_sha256
        )
    ):
        raise IsolationGateError("provider operation target/challenge is malformed")
    context = {
        "generation": generation,
        "phase": phase,
        "target_container_id_sha256": hashlib.sha256(
            target_container_id.encode("ascii")
        ).hexdigest(),
        "target_image_identity_sha256": target_image_identity_sha256,
        "requested_at": requested_at.astimezone(timezone.utc)
        .replace(microsecond=0)
        .strftime(TIMESTAMP),
    }
    challenge_sha256 = hashlib.sha256(nonce).hexdigest()
    operation_id = hashlib.sha256(
        b"adapteng-postgres-restore-provider-operation-v1\0"
        + nonce
        + canonical_json(context)
    ).hexdigest()
    return {
        "schema_version": 1,
        "operation_id": operation_id,
        "challenge_sha256": challenge_sha256,
        **context,
    }


def write_operation_request(
    operation: dict[str, Any],
    *,
    state_root: Path = STATE_ROOT,
) -> tuple[Path, int]:
    generation = str(operation["generation"])
    generation_dir = state_root / f"generation-{generation}"
    info = generation_dir.lstat()
    if (
        os.geteuid() != 0
        or info.st_uid != 0
        or not stat.S_ISDIR(info.st_mode)
        or info.st_mode & 0o077
        or generation_dir.is_symlink()
    ):
        raise IsolationGateError("generation operation directory is not secure")
    operations = generation_dir / "provider-operations"
    try:
        os.mkdir(operations, 0o700)
    except FileExistsError:
        pass
    operation_info = operations.lstat()
    if (
        operation_info.st_uid != 0
        or not stat.S_ISDIR(operation_info.st_mode)
        or operation_info.st_mode & 0o077
        or operations.is_symlink()
    ):
        raise IsolationGateError("provider operation directory is not secure")
    path = operations / f"{operation['operation_id']}.request.json"
    descriptor = os.open(
        path,
        os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
        0o600,
    )
    payload = canonical_json(operation)
    try:
        os.write(descriptor, payload)
        os.fsync(descriptor)
        os.lseek(descriptor, 0, os.SEEK_SET)
        if os.read(descriptor, len(payload) + 1) != payload:
            raise IsolationGateError("provider operation request descriptor changed")
        os.lseek(descriptor, 0, os.SEEK_SET)
        return path, descriptor
    except Exception:
        os.close(descriptor)
        raise


def invoke_pinned_collector(
    operation: dict[str, Any],
    request_descriptor: int,
    expected_collector_sha256: str,
    *,
    run: Callable[..., subprocess.CompletedProcess[Any]] = subprocess.run,
) -> bytes:
    owner_cidr = secure_bytes(
        OWNER_CIDR, "owner SSH CIDR capability", private=True
    )
    if not hasattr(os, "memfd_create") or fcntl is None:
        raise IsolationGateError("one-shot collector requires memfd_create")
    owner_cidr_descriptor = os.memfd_create(
        "owner-ssh-cidr", flags=getattr(os, "MFD_CLOEXEC", 0)
    )
    os.fchmod(owner_cidr_descriptor, 0o600)
    os.write(owner_cidr_descriptor, owner_cidr)
    os.lseek(owner_cidr_descriptor, 0, os.SEEK_SET)
    collector_descriptor = os.open(
        PROVIDER_COLLECTOR, os.O_RDONLY | os.O_NOFOLLOW
    )
    capability_descriptors = (
        PROVIDER_BROKER_REQUEST_FD,
        PROVIDER_BROKER_RESPONSE_FD,
    )
    try:
        info = os.fstat(collector_descriptor)
        payload = bytearray()
        while chunk := os.read(collector_descriptor, 65536):
            payload.extend(chunk)
        if (
            os.geteuid() != 0
            or info.st_uid != 0
            or not stat.S_ISREG(info.st_mode)
            or info.st_mode & 0o022
            or hashlib.sha256(payload).hexdigest() != expected_collector_sha256
        ):
            raise IsolationGateError("provider collector descriptor is not sealed")
        os.lseek(collector_descriptor, 0, os.SEEK_SET)
        os.lseek(request_descriptor, 0, os.SEEK_SET)
        for descriptor in capability_descriptors:
            try:
                capability_info = os.fstat(descriptor)
            except OSError as exc:
                raise IsolationGateError(
                    "one-shot provider capability descriptor is absent"
                ) from exc
            access_mode = fcntl.fcntl(descriptor, fcntl.F_GETFL) & os.O_ACCMODE
            expected_mode = (
                os.O_WRONLY
                if descriptor == PROVIDER_BROKER_REQUEST_FD
                else os.O_RDONLY
            )
            if (
                os.geteuid() != 0
                or not stat.S_ISFIFO(capability_info.st_mode)
                or access_mode != expected_mode
            ):
                raise IsolationGateError(
                    "one-shot provider transport descriptor is not inherited pipe/stdio"
                )
        completed = run(
            [
                sys.executable,
                f"/proc/self/fd/{collector_descriptor}",
                "collect",
                str(operation["generation"]),
                "--owner-cidr-fd",
                str(owner_cidr_descriptor),
                "--operation-request-fd",
                str(request_descriptor),
                "--broker-request-fd",
                str(PROVIDER_BROKER_REQUEST_FD),
                "--broker-response-fd",
                str(PROVIDER_BROKER_RESPONSE_FD),
            ],
            check=True,
            capture_output=True,
            pass_fds=(
                collector_descriptor,
                request_descriptor,
                owner_cidr_descriptor,
                *capability_descriptors,
            ),
            env=CLEAN_ENVIRONMENT,
            timeout=20,
        )
    finally:
        os.close(collector_descriptor)
        os.close(owner_cidr_descriptor)
    packet = bytes(completed.stdout)
    if (
        not packet
        or len(packet) > 4 * 1024 * 1024
        or canonical_json(json.loads(packet)) != packet
    ):
        raise IsolationGateError("pinned provider collector output is not canonical")
    return packet


def evaluate_collected_packet(
    packet: bytes,
    manifest: bytes,
    *,
    operation: dict[str, Any],
    now: datetime,
    server_ref_sha256: str,
    consumed_operations: set[str],
) -> dict[str, Any]:
    value = json.loads(packet)
    if not isinstance(value, dict) or canonical_json(value) != packet:
        raise IsolationGateError("provider collector packet is not canonical")
    policy = load_provider_policy(json.loads(manifest))
    try:
        validate_locked_measurement(
            value,
            str(operation["generation"]),
            now.astimezone(timezone.utc).replace(microsecond=0),
            server_ref_sha256,
            policy,
        )
    except GenerationError as exc:
        raise IsolationGateError("provider response is not locked/current/bound") from exc
    operation_fields = {
        "operation_id",
        "challenge_sha256",
        "generation",
        "phase",
        "target_container_id_sha256",
        "target_image_identity_sha256",
        "requested_at",
    }
    if any(value.get(key) != operation.get(key) for key in operation_fields):
        raise IsolationGateError("provider response operation binding is not exact")
    operation_id = str(operation["operation_id"])
    if operation_id in consumed_operations:
        raise IsolationGateError("provider operation challenge was already consumed")
    observed = datetime.strptime(str(value["observed_at"]), TIMESTAMP).replace(
        tzinfo=timezone.utc
    )
    requested = datetime.strptime(str(operation["requested_at"]), TIMESTAMP).replace(
        tzinfo=timezone.utc
    )
    if observed < requested:
        raise IsolationGateError("provider response predates its operation challenge")
    consumed_operations.add(operation_id)
    return {
        "packet": value,
        "packet_sha256": hashlib.sha256(packet).hexdigest(),
        "observed_at": observed,
        "collector_sha256": policy.collector_sha256,
        "manifest_sha256": hashlib.sha256(manifest).hexdigest(),
        "operation_binding_sha256": hashlib.sha256(
            canonical_json(operation)
        ).hexdigest(),
    }


def collect_provider_operation(
    *,
    generation: str,
    phase: str,
    target_container_id: str,
    target_image_identity_sha256: str,
    consumed_operations: set[str],
    run: Callable[..., subprocess.CompletedProcess[Any]] = subprocess.run,
    now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    nonce_factory: Callable[[int], bytes] = secrets.token_bytes,
    state_root: Path = STATE_ROOT,
) -> dict[str, Any]:
    manifest = secure_bytes(PROVIDER_MANIFEST, "provider manifest", private=False)
    policy = load_provider_policy(json.loads(manifest))
    _, server_ref = current_server_identity()
    operation = canonical_operation_request(
        generation=generation,
        phase=phase,
        target_container_id=target_container_id,
        target_image_identity_sha256=target_image_identity_sha256,
        nonce=nonce_factory(32),
        requested_at=now(),
    )
    request_path, request_descriptor = write_operation_request(
        operation, state_root=state_root
    )
    try:
        packet = invoke_pinned_collector(
            operation,
            request_descriptor,
            policy.collector_sha256,
            run=run,
        )
    finally:
        os.close(request_descriptor)
    packet_path = request_path.with_suffix(".packet.json")
    descriptor = os.open(
        packet_path,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
        0o600,
    )
    try:
        os.write(descriptor, packet)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    return evaluate_collected_packet(
        packet,
        manifest,
        operation=operation,
        now=now(),
        server_ref_sha256=server_ref,
        consumed_operations=consumed_operations,
    )
