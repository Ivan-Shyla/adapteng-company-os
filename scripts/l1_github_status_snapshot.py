#!/usr/bin/env python3
"""Create a minimal, read-only L1 status snapshot from GitHub metadata.

The command deliberately uses only ``gh api`` read endpoints. It does not read
environment variables, request an auth token, or expose ``gh`` stderr. Outputs
must be outside this repository so generated snapshots cannot become tracked.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence


SCHEMA_VERSION = "adapteng-l1-github-status-snapshot/v1"
REPOSITORIES = (
    "Ivan-Shyla/adapteng-company-os",
    "Ivan-Shyla/adapteng-automation-platform",
    "Ivan-Shyla/adapteng-website",
    "Ivan-Shyla/adapteng-marketing",
    "Ivan-Shyla/ai-dev-loop-control-plane",
)
REPOSITORY_ROOT = Path(__file__).resolve().parent.parent


class SnapshotError(Exception):
    """A user-safe failure while preparing the snapshot."""


class GitHubMetadataError(SnapshotError):
    """A deliberately detail-free failure from the authenticated gh client."""


class GhTransport:
    """Small read-only transport that never forwards gh stderr."""

    def get_json(self, endpoint: str) -> Any:
        try:
            completed = subprocess.run(
                ["gh", "api", endpoint],
                check=False,
                capture_output=True,
            )
        except (OSError, subprocess.SubprocessError) as error:
            raise GitHubMetadataError("GitHub metadata request failed") from error
        if completed.returncode != 0:
            raise GitHubMetadataError("GitHub metadata request failed")
        try:
            stdout = completed.stdout
            if isinstance(stdout, bytes):
                stdout = stdout.decode("utf-8")
            if not isinstance(stdout, str):
                raise TypeError("unexpected gh stdout type")
            return json.loads(stdout)
        except (UnicodeDecodeError, TypeError, json.JSONDecodeError) as error:
            raise GitHubMetadataError("GitHub metadata request failed") from error


def _string(mapping: Mapping[str, Any], name: str) -> str | None:
    value = mapping.get(name)
    return value if isinstance(value, str) else None


def _empty_record(repository: str) -> dict[str, Any]:
    return {
        "repository": repository,
        "default_branch": None,
        "default_branch_sha": None,
        "open_pull_requests": [],
        "latest_ci": {"conclusion": None, "timestamp": None},
    }


def _pull_requests(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, list):
        return []
    selected: list[dict[str, Any]] = []
    for item in payload:
        if not isinstance(item, Mapping):
            continue
        number = item.get("number")
        state = _string(item, "state")
        draft = item.get("draft")
        head = item.get("head")
        if (
            not isinstance(number, int)
            or state != "open"
            or not isinstance(draft, bool)
            or not isinstance(head, Mapping)
        ):
            continue
        head_sha = _string(head, "sha")
        if head_sha is None:
            continue
        selected.append(
            {
                "number": number,
                "state": state,
                "draft": draft,
                "head_sha": head_sha,
            }
        )
    return sorted(selected, key=lambda pull_request: pull_request["number"])


def _latest_ci(payload: Any) -> dict[str, str | None]:
    runs = payload.get("workflow_runs") if isinstance(payload, Mapping) else None
    if not isinstance(runs, list):
        return {"conclusion": None, "timestamp": None}
    candidates: list[tuple[str, str | None]] = []
    for run in runs:
        if not isinstance(run, Mapping):
            continue
        timestamp = _string(run, "updated_at")
        if timestamp is None:
            continue
        candidates.append((timestamp, _string(run, "conclusion")))
    if not candidates:
        return {"conclusion": None, "timestamp": None}
    timestamp, conclusion = max(candidates, key=lambda candidate: candidate[0])
    return {"conclusion": conclusion, "timestamp": timestamp}


def collect_repository(transport: GhTransport, repository: str) -> tuple[dict[str, Any], bool]:
    """Return an allowlisted record and whether every required request succeeded."""
    record = _empty_record(repository)
    try:
        metadata = transport.get_json(f"repos/{repository}")
    except GitHubMetadataError:
        return record, False
    if not isinstance(metadata, Mapping):
        return record, False

    default_branch = _string(metadata, "default_branch")
    if default_branch is None:
        return record, False
    record["default_branch"] = default_branch
    succeeded = True

    try:
        branch_ref = transport.get_json(
            f"repos/{repository}/git/ref/heads/{default_branch}"
        )
        if isinstance(branch_ref, Mapping):
            target = branch_ref.get("object")
            if isinstance(target, Mapping):
                record["default_branch_sha"] = _string(target, "sha")
        if record["default_branch_sha"] is None:
            succeeded = False
    except GitHubMetadataError:
        succeeded = False

    try:
        pulls = transport.get_json(f"repos/{repository}/pulls?state=open&per_page=100")
        record["open_pull_requests"] = _pull_requests(pulls)
    except GitHubMetadataError:
        succeeded = False

    try:
        runs = transport.get_json(
            f"repos/{repository}/actions/runs?branch={default_branch}&per_page=100"
        )
        record["latest_ci"] = _latest_ci(runs)
    except GitHubMetadataError:
        succeeded = False

    return record, succeeded


def collect_snapshot(
    transport: GhTransport,
    *,
    observed_at: str,
) -> tuple[dict[str, Any], bool]:
    """Collect all repository records in a fixed, deterministic schema."""
    records: list[dict[str, Any]] = []
    all_succeeded = True
    for repository in REPOSITORIES:
        record, succeeded = collect_repository(transport, repository)
        records.append(record)
        all_succeeded = all_succeeded and succeeded
    return {
        "schema_version": SCHEMA_VERSION,
        "timestamp": observed_at,
        "repositories": records,
    }, all_succeeded


def output_path(path: Path) -> Path:
    """Accept only an external, user-selected output location."""
    resolved = path.resolve()
    try:
        resolved.relative_to(REPOSITORY_ROOT)
    except ValueError:
        return resolved
    raise SnapshotError("Snapshot output must be outside the repository")


def write_snapshot(path: Path, snapshot: Mapping[str, Any], *, overwrite: bool) -> None:
    """Write JSON without replacing a pre-existing file unless opted in."""
    if not path.parent.is_dir():
        raise SnapshotError("Snapshot output directory does not exist")
    mode = "w" if overwrite else "x"
    try:
        with path.open(mode, encoding="utf-8", newline="\n") as destination:
            json.dump(snapshot, destination, indent=2, sort_keys=True)
            destination.write("\n")
    except FileExistsError as error:
        raise SnapshotError("Snapshot output already exists; use --overwrite") from error
    except OSError as error:
        raise SnapshotError("Unable to write snapshot") from error


def parse_args(arguments: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        required=True,
        help="External JSON path to create; repository paths are rejected.",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Allow replacement of an existing output file.",
    )
    return parser.parse_args(arguments)


def main(arguments: Sequence[str] | None = None) -> int:
    args = parse_args(arguments)
    try:
        destination = output_path(args.output)
        if destination.exists() and not args.overwrite:
            raise SnapshotError("Snapshot output already exists; use --overwrite")
        observed_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        snapshot, all_succeeded = collect_snapshot(
            GhTransport(), observed_at=observed_at
        )
        write_snapshot(destination, snapshot, overwrite=args.overwrite)
    except SnapshotError:
        print("Unable to create GitHub status snapshot.", file=sys.stderr)
        return 1

    if not all_succeeded:
        print("One or more GitHub metadata requests failed.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
