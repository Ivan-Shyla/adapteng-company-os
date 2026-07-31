#!/usr/bin/env python3
"""Stream the sealed generation-C final assertion through the measured runner."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
import subprocess
import sys
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
MANIFEST = SCRIPT_DIR / "postgres_restore_procedure_manifest.json"
ASSERTION = SCRIPT_DIR / "postgres_restore_c_final_assert.sql"
RUNNER = SCRIPT_DIR / "postgres_restore_runner.py"
STATE_DIR = Path("/var/lib/adapteng/postgres-restore-rehearsal/generation-C")
CLEAN_ENVIRONMENT = {
    "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
    "LANG": "C",
    "LC_ALL": "C",
}
RUNNER_EVIDENCE_PREFIXES = (
    "runner_manifest_sha256=",
    "measured_runner_identity_sha256=",
    "database_target_identity_sha256=",
    "database_container_identity_sha256=",
    "pre_sql_host_inventory_sha256=",
    "post_sql_host_inventory_sha256=",
    "pre_sql_provider_inventory_sha256=",
    "post_sql_provider_inventory_sha256=",
    "runner_exit=0",
)


class FinalAssertionError(RuntimeError):
    """Fail-closed generation-C assertion error."""


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--procedure-manifest-sha256", required=True)
    args = parser.parse_args()
    assertion_fd = -1
    directory_fd = -1
    try:
        manifest_raw = MANIFEST.read_bytes()
        if hashlib.sha256(manifest_raw).hexdigest() != (
            args.procedure_manifest_sha256
        ):
            raise FinalAssertionError("procedure manifest digest mismatch")
        manifest = json.loads(manifest_raw)
        expected = str(
            manifest["artifacts"]["scripts/postgres_restore_c_final_assert.sql"]
        )
        source_fd = os.open(ASSERTION, os.O_RDONLY | os.O_NOFOLLOW)
        try:
            source_info = os.fstat(source_fd)
            if (
                not stat.S_ISREG(source_info.st_mode)
                or source_info.st_uid != 0
                or source_info.st_mode & 0o022
            ):
                raise FinalAssertionError(
                    "generation-C assertion source is not root-owned/read-only"
                )
            payload = bytearray()
            while chunk := os.read(source_fd, 65536):
                payload.extend(chunk)
        finally:
            os.close(source_fd)
        if hashlib.sha256(payload).hexdigest() != expected:
            raise FinalAssertionError("generation-C assertion digest mismatch")

        info = STATE_DIR.lstat()
        if (
            os.geteuid() != 0
            or not stat.S_ISDIR(info.st_mode)
            or info.st_uid != 0
            or info.st_mode & 0o077
        ):
            raise FinalAssertionError("generation-C state directory is not secure")
        directory_fd = os.open(
            STATE_DIR, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        )
        assertion_fd = os.open(
            "c-final-assert.sql",
            os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
            dir_fd=directory_fd,
        )
        offset = 0
        while offset < len(payload):
            offset += os.write(assertion_fd, payload[offset:])
        os.fsync(assertion_fd)
        os.lseek(assertion_fd, 0, os.SEEK_SET)
        completed = subprocess.run(
            [
                sys.executable,
                str(RUNNER),
                "assert-c-final",
                "--generation",
                "C",
                "--procedure-manifest-sha256",
                args.procedure_manifest_sha256,
            ],
            stdin=assertion_fd,
            capture_output=True,
            env=CLEAN_ENVIRONMENT,
        )
        if completed.returncode != 0:
            raise FinalAssertionError("sealed generation-C assertion failed")
        evidence = completed.stderr.decode("utf-8", errors="strict")
        evidence_lines = evidence.splitlines()
        if len(evidence_lines) != len(RUNNER_EVIDENCE_PREFIXES) or not all(
            sum(line.startswith(prefix) for line in evidence_lines) == 1
            for prefix in RUNNER_EVIDENCE_PREFIXES
        ):
            raise FinalAssertionError("measured runner identity evidence is missing")
        for line in evidence_lines:
            if line.startswith(RUNNER_EVIDENCE_PREFIXES):
                print(line)
        print(f"c_final_assertion_sha256={expected}")
        print("c_final_assertion_status=passed")
        return 0
    except (
        OSError,
        UnicodeError,
        KeyError,
        json.JSONDecodeError,
        FinalAssertionError,
    ) as exc:
        print(f"STOP: {exc}", file=sys.stderr)
        return 2
    finally:
        if assertion_fd >= 0:
            os.close(assertion_fd)
        if directory_fd >= 0:
            os.close(directory_fd)


if __name__ == "__main__":
    raise SystemExit(main())
