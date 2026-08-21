#!/usr/bin/env python3
"""Create sanitized JSON and Markdown L1 status reports from GitHub metadata.

The command uses only ``gh api`` read endpoints. It does not read environment
variables, request an auth token, or expose ``gh`` stderr. Outputs must be
outside this repository so generated reports cannot become tracked.
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
FAILED_CONCLUSIONS = frozenset({"failure"})
REPOSITORY_ROOT = Path(__file__).resolve().parent.parent


class SnapshotError(Exception):
    """A user-safe failure while preparing the status reports."""


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
        if timestamp is not None:
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


def classify_snapshot(snapshot: Mapping[str, Any], *, all_succeeded: bool) -> str:
    """Classify only collection failure or a confirmed default-branch CI failure RED."""
    if not all_succeeded:
        return "RED"
    repositories = snapshot.get("repositories")
    if not isinstance(repositories, list):
        return "RED"
    for record in repositories:
        if not isinstance(record, Mapping):
            return "RED"
        latest_ci = record.get("latest_ci")
        if isinstance(latest_ci, Mapping) and latest_ci.get("conclusion") in FAILED_CONCLUSIONS:
            return "RED"
    if any(
        isinstance(record, Mapping)
        and (record.get("open_pull_requests") or record.get("latest_ci", {}).get("conclusion") is None)
        for record in repositories
    ):
        return "YELLOW"
    return "GREEN"


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
    snapshot: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "timestamp": observed_at,
        "repositories": records,
    }
    snapshot["overall_status"] = classify_snapshot(
        snapshot, all_succeeded=all_succeeded
    )
    return snapshot, all_succeeded


def _markdown(value: Any) -> str:
    text = "unknown" if value is None else str(value)
    return text.replace("\\", "\\\\").replace("|", "\\|").replace("\n", " ").replace("\r", " ")


def _pull_request_summary(pull_requests: Any) -> str:
    if not isinstance(pull_requests, list) or not pull_requests:
        return "none"
    selected: list[str] = []
    for pull_request in pull_requests:
        if not isinstance(pull_request, Mapping):
            continue
        draft = "draft" if pull_request.get("draft") else "ready"
        selected.append(
            "#{number} ({state}, {draft}, {head_sha})".format(
                number=_markdown(pull_request.get("number")),
                state=_markdown(pull_request.get("state")),
                draft=draft,
                head_sha=_markdown(pull_request.get("head_sha")),
            )
        )
    return "; ".join(selected) if selected else "none"


def markdown_report(snapshot: Mapping[str, Any]) -> str:
    """Render only fields already allowlisted into the snapshot schema."""
    lines = [
        "# AdaptEng L1 platform status",
        "",
        f"**Overall:** {_markdown(snapshot.get('overall_status'))}",
        f"**Schema:** `{_markdown(snapshot.get('schema_version'))}`",
        f"**Collected:** `{_markdown(snapshot.get('timestamp'))}`",
        "",
        "| Repository | Default branch | SHA | Latest CI | Open pull requests |",
        "|---|---|---|---|---|",
    ]
    repositories = snapshot.get("repositories")
    if not isinstance(repositories, list):
        return "\n".join(lines + ["| unknown | unknown | unknown | unknown | none |", ""])
    for record in repositories:
        if not isinstance(record, Mapping):
            continue
        latest_ci = record.get("latest_ci")
        conclusion = latest_ci.get("conclusion") if isinstance(latest_ci, Mapping) else None
        timestamp = latest_ci.get("timestamp") if isinstance(latest_ci, Mapping) else None
        ci = f"{_markdown(conclusion)} ({_markdown(timestamp)})"
        lines.append(
            "| {repository} | {branch} | `{sha}` | {ci} | {pull_requests} |".format(
                repository=_markdown(record.get("repository")),
                branch=_markdown(record.get("default_branch")),
                sha=_markdown(record.get("default_branch_sha")),
                ci=ci,
                pull_requests=_pull_request_summary(record.get("open_pull_requests")),
            )
        )
    return "\n".join(lines) + "\n"


def output_path(path: Path) -> Path:
    """Accept only an external, user-selected output location."""
    resolved = path.resolve()
    try:
        resolved.relative_to(REPOSITORY_ROOT)
    except ValueError:
        return resolved
    raise SnapshotError("Snapshot output must be outside the repository")


def write_output(path: Path, content: str, *, overwrite: bool) -> None:
    """Write a report without replacing a pre-existing file unless opted in."""
    if not path.parent.is_dir():
        raise SnapshotError("Snapshot output directory does not exist")
    mode = "w" if overwrite else "x"
    try:
        with path.open(mode, encoding="utf-8", newline="\n") as destination:
            destination.write(content)
    except FileExistsError as error:
        raise SnapshotError("Snapshot output already exists; use --overwrite") from error
    except OSError as error:
        raise SnapshotError("Unable to write snapshot") from error


def parse_args(arguments: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True, help="External JSON path.")
    parser.add_argument(
        "--markdown-output", type=Path, required=True, help="External Markdown path."
    )
    parser.add_argument("--overwrite", action="store_true", help="Replace existing outputs.")
    return parser.parse_args(arguments)


def main(arguments: Sequence[str] | None = None) -> int:
    args = parse_args(arguments)
    try:
        json_destination = output_path(args.output)
        markdown_destination = output_path(args.markdown_output)
        if json_destination == markdown_destination:
            raise SnapshotError("JSON and Markdown output paths must differ")
        for destination in (json_destination, markdown_destination):
            if destination.exists() and not args.overwrite:
                raise SnapshotError("Snapshot output already exists; use --overwrite")
        observed_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        snapshot, all_succeeded = collect_snapshot(
            GhTransport(), observed_at=observed_at
        )
        write_output(
            json_destination,
            json.dumps(snapshot, indent=2, sort_keys=True) + "\n",
            overwrite=args.overwrite,
        )
        write_output(
            markdown_destination,
            markdown_report(snapshot),
            overwrite=args.overwrite,
        )
    except SnapshotError:
        print("Unable to create GitHub status reports.", file=sys.stderr)
        return 1
    if not all_succeeded:
        print("GitHub metadata collection was incomplete.", file=sys.stderr)
        return 1
    if snapshot["overall_status"] == "RED":
        print("A default-branch CI failure was reported.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
