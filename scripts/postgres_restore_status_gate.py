#!/usr/bin/env python3
"""Preserve status-command exit semantics while checking exact output."""

from __future__ import annotations

import argparse
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path


RUNNER = Path(__file__).resolve().parent / "postgres_restore_runner.py"
CLEAN_ENVIRONMENT = {
    "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
    "LANG": "C",
    "LC_ALL": "C",
}


class StatusGateError(RuntimeError):
    """Fail-closed migration status error."""


def execute_status_gate(
    expected_output: str,
    expected_states: list[str],
    generation: str,
    procedure_manifest_sha256: str,
    *,
    run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> str:
    if expected_output not in {"absent", "exact"}:
        raise StatusGateError("expected output must be absent or exact")
    if not expected_states:
        raise StatusGateError("at least one expected migration state is required")
    command = [
        sys.executable,
        str(RUNNER),
        "status",
        "--generation",
        generation,
        "--procedure-manifest-sha256",
        procedure_manifest_sha256,
    ]
    for expected in expected_states:
        command.extend(["--expect", expected])
    completed = run(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=CLEAN_ENVIRONMENT,
    )
    if completed.returncode != 0:
        raise StatusGateError("migration status command failed")
    if completed.stdout != f"{expected_output}\n":
        raise StatusGateError("migration status output is not exact")
    evidence = completed.stderr.splitlines()
    prefixes = (
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
    if len(evidence) != len(prefixes) or not all(
        sum(line.startswith(prefix) for line in evidence) == 1 for prefix in prefixes
    ):
        raise StatusGateError("measured runner identity evidence is not exact")
    for line in evidence:
        print(line, file=sys.stderr)
    return completed.stdout


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--expect-output", choices=("absent", "exact"), required=True)
    parser.add_argument("--generation", choices=("A", "B", "C"), required=True)
    parser.add_argument("--procedure-manifest-sha256", required=True)
    parser.add_argument("--expect", action="append", required=True)
    args = parser.parse_args()
    try:
        sys.stdout.write(
            execute_status_gate(
                args.expect_output,
                args.expect,
                args.generation,
                args.procedure_manifest_sha256,
            )
        )
        return 0
    except StatusGateError as exc:
        print(f"STOP: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
