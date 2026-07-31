#!/usr/bin/env python3
"""Execute the sealed transaction probe from descriptor-owned verified bytes."""

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


SCRIPT_DIR = Path(__file__).resolve().parent
ROOT = SCRIPT_DIR.parent
MANIFEST = SCRIPT_DIR / "postgres_restore_procedure_manifest.json"
PROBE = SCRIPT_DIR / "postgres_restore_transaction_probe.sql"
RUNNER = SCRIPT_DIR / "postgres_restore_runner.py"
STATE_DIR = Path("/var/lib/adapteng/postgres-restore-rehearsal/generation-B")
SHA256 = re.compile(r"^[0-9a-f]{64}$")


class ProbeError(RuntimeError):
    """Fail-closed probe staging or execution error."""


def verify_probe_payload(payload: bytes, expected_sha256: str) -> str:
    actual = hashlib.sha256(payload).hexdigest()
    if actual != expected_sha256:
        raise ProbeError("tracked transaction probe digest mismatch")
    return actual


def secure_info(path: Path, kind: str) -> os.stat_result:
    if os.name != "posix" or os.geteuid() != 0:
        raise ProbeError("transaction probe requires a POSIX root host")
    info = path.lstat()
    if path.is_symlink() or info.st_uid != 0 or info.st_mode & 0o077:
        raise ProbeError(f"{kind} must be root-owned, non-symlink and private")
    expected = stat.S_ISDIR if kind == "state directory" else stat.S_ISREG
    if not expected(info.st_mode):
        raise ProbeError(f"{kind} has the wrong file type")
    return info


def read_verified_source(expected_sha256: str) -> bytes:
    fd = os.open(PROBE, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
            raise ProbeError("tracked probe source is not root-owned/read-only")
        chunks: list[bytes] = []
        while True:
            chunk = os.read(fd, 65536)
            if not chunk:
                break
            chunks.append(chunk)
        payload = b"".join(chunks)
    finally:
        os.close(fd)
    verify_probe_payload(payload, expected_sha256)
    return payload


def stage_probe(directory_fd: int, payload: bytes) -> tuple[int, str]:
    fd = os.open(
        "transaction-probe.sql",
        os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
        0o600,
        dir_fd=directory_fd,
    )
    try:
        offset = 0
        while offset < len(payload):
            offset += os.write(fd, payload[offset:])
        os.fsync(fd)
        os.lseek(fd, 0, os.SEEK_SET)
        digest = hashlib.sha256()
        while True:
            chunk = os.read(fd, 65536)
            if not chunk:
                break
            digest.update(chunk)
        if digest.hexdigest() != hashlib.sha256(payload).hexdigest():
            raise ProbeError("staged transaction probe bytes changed")
        return fd, digest.hexdigest()
    except Exception:
        os.close(fd)
        raise


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--procedure-manifest-sha256", required=True)
    args = parser.parse_args()
    directory_fd = -1
    probe_fd = -1
    try:
        if not SHA256.fullmatch(args.procedure_manifest_sha256):
            raise ProbeError("procedure manifest digest is malformed")
        manifest_raw = MANIFEST.read_bytes()
        if hashlib.sha256(manifest_raw).hexdigest() != (
            args.procedure_manifest_sha256
        ):
            raise ProbeError("procedure manifest digest mismatch")
        manifest = json.loads(manifest_raw)
        expected = manifest["artifacts"][
            "scripts/postgres_restore_transaction_probe.sql"
        ]
        payload = read_verified_source(expected)

        path_info = secure_info(STATE_DIR, "state directory")
        directory_fd = os.open(
            STATE_DIR, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        )
        fd_info = os.fstat(directory_fd)
        if (path_info.st_dev, path_info.st_ino) != (fd_info.st_dev, fd_info.st_ino):
            raise ProbeError("generation-B state directory changed")
        probe_fd, probe_sha256 = stage_probe(directory_fd, payload)
        os.lseek(probe_fd, 0, os.SEEK_SET)
        with os.fdopen(os.dup(probe_fd), "rb", closefd=True) as probe_stream:
            completed = subprocess.run(
                [
                    sys.executable,
                    str(RUNNER),
                    "probe",
                    "--generation",
                    "B",
                    "--procedure-manifest-sha256",
                    args.procedure_manifest_sha256,
                ],
                stdin=probe_stream,
                capture_output=True,
            )
        if completed.returncode != 0:
            raise ProbeError(
                completed.stderr.decode("utf-8", errors="replace").strip()
            )
        evidence = completed.stderr.decode("utf-8", errors="strict")
        if (
            "runner_manifest_sha256=" not in evidence
            or "measured_runner_identity_sha256=" not in evidence
            or "database_target_identity_sha256=" not in evidence
            or "database_container_identity_sha256=" not in evidence
        ):
            raise ProbeError("runner identity evidence is missing")
        print(f"transaction_probe_sha256={probe_sha256}")
        print("transaction_result=rolled_back")
        print("durable_synthetic_rows_or_allocator_state=0")
        print("identity_sequence_unchanged=true")
        for line in evidence.splitlines():
            if line.startswith(
                (
                    "runner_manifest_sha256=",
                    "measured_runner_identity_sha256=",
                    "database_target_identity_sha256=",
                    "database_container_identity_sha256=",
                )
            ):
                print(line)
        return 0
    except (
        KeyError,
        OSError,
        UnicodeError,
        json.JSONDecodeError,
        ProbeError,
    ) as exc:
        print(f"STOP: {exc}", file=sys.stderr)
        return 2
    finally:
        for fd in (probe_fd, directory_fd):
            if fd >= 0:
                os.close(fd)


if __name__ == "__main__":
    raise SystemExit(main())
