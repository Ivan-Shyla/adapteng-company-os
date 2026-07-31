#!/usr/bin/env python3
"""Verify a fresh signed provider lock measurement on the current scratch host."""

from __future__ import annotations

import hashlib
import json
import os
import stat
import subprocess
import time
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

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
PROVIDER_INBOX = Path("/run/adapteng/postgres-restore-provider")
CLOUD_METADATA = Path("/run/cloud-init/instance-data.json")
CLEAN_ENVIRONMENT = {
    "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
    "LANG": "C",
    "LC_ALL": "C",
}
TIMESTAMP = "%Y-%m-%dT%H:%M:%SZ"


class IsolationGateError(RuntimeError):
    """Fail-closed signed isolation measurement error."""


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


def memfd(payload: bytes, label: str) -> int:
    if not hasattr(os, "memfd_create"):
        raise IsolationGateError("provider verification requires Linux memfd_create")
    descriptor = os.memfd_create(label, flags=0)
    os.write(descriptor, payload)
    os.lseek(descriptor, 0, os.SEEK_SET)
    return descriptor


def current_server_ref_sha256() -> str:
    raw = secure_bytes(CLOUD_METADATA, "cloud instance metadata", private=False)
    value = json.loads(raw)
    if not isinstance(value, dict) or not str(value.get("instance_id", "")).strip():
        raise IsolationGateError("cloud instance metadata lacks instance_id")
    return hashlib.sha256(str(value["instance_id"]).strip().encode("utf-8")).hexdigest()


def verify_signature(
    packet: bytes,
    signature: bytes,
    public_key: bytes,
    *,
    run: Callable[..., subprocess.CompletedProcess[Any]] = subprocess.run,
) -> None:
    descriptors = [
        memfd(public_key, "provider-public-key"),
        memfd(packet, "provider-packet"),
        memfd(signature, "provider-signature"),
    ]
    try:
        run(
            [
                "openssl",
                "pkeyutl",
                "-verify",
                "-pubin",
                "-inkey",
                f"/proc/self/fd/{descriptors[0]}",
                "-rawin",
                "-in",
                f"/proc/self/fd/{descriptors[1]}",
                "-sigfile",
                f"/proc/self/fd/{descriptors[2]}",
            ],
            check=True,
            capture_output=True,
            pass_fds=tuple(descriptors),
            env=CLEAN_ENVIRONMENT,
        )
    finally:
        for descriptor in descriptors:
            os.close(descriptor)


def evaluate_signed_packet(
    packet_bytes: bytes,
    signature_bytes: bytes,
    manifest_bytes: bytes,
    *,
    generation: str,
    now: datetime,
    expected_server_ref_sha256: str,
    after_observed_at: datetime | None = None,
    run: Callable[..., subprocess.CompletedProcess[Any]] = subprocess.run,
) -> dict[str, Any]:
    manifest = json.loads(manifest_bytes)
    policy = load_provider_policy(manifest)
    verify_signature(
        packet_bytes,
        signature_bytes,
        policy.public_key_pem.encode("utf-8"),
        run=run,
    )
    packet = json.loads(packet_bytes)
    if not isinstance(packet, dict):
        raise IsolationGateError("signed provider packet is not an object")
    validate_locked_measurement(
        packet,
        generation,
        now,
        expected_server_ref_sha256,
        policy,
    )
    observed = datetime.strptime(str(packet["observed_at"]), TIMESTAMP).replace(
        tzinfo=timezone.utc
    )
    require_measurement_after(observed, after_observed_at)
    return {
        "packet": packet,
        "packet_sha256": hashlib.sha256(packet_bytes).hexdigest(),
        "observed_at": observed,
        "collector_sha256": policy.collector_sha256,
        "manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
    }


def require_measurement_after(
    observed_at: datetime, boundary: datetime | None
) -> None:
    if boundary is not None and observed_at <= boundary:
        raise IsolationGateError(
            "provider measurement was not recollected after SQL and runner cleanup"
        )


def wait_for_provider_measurement(
    generation: str,
    *,
    after_observed_at: datetime | None = None,
    timeout_seconds: int = 600,
    run: Callable[..., subprocess.CompletedProcess[Any]] = subprocess.run,
    now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    manifest = secure_bytes(PROVIDER_MANIFEST, "provider manifest", private=False)
    server_ref = current_server_ref_sha256()
    packet_path = PROVIDER_INBOX / f"generation-{generation}.json"
    signature_path = PROVIDER_INBOX / f"generation-{generation}.sig"
    deadline = time.monotonic() + timeout_seconds
    last_error = "provider packet is unavailable"
    while time.monotonic() < deadline:
        try:
            packet = secure_bytes(packet_path, "provider packet", private=True)
            signature = secure_bytes(
                signature_path, "provider packet signature", private=True
            )
            return evaluate_signed_packet(
                packet,
                signature,
                manifest,
                generation=generation,
                now=now().astimezone(timezone.utc).replace(microsecond=0),
                expected_server_ref_sha256=server_ref,
                after_observed_at=after_observed_at,
                run=run,
            )
        except (
            GenerationError,
            IsolationGateError,
            OSError,
            UnicodeError,
            json.JSONDecodeError,
            subprocess.CalledProcessError,
        ) as exc:
            last_error = str(exc)
            sleep(5)
    raise IsolationGateError(f"fresh provider lock was not measured: {last_error}")
