#!/usr/bin/env python3
"""Derive selected-set retention from verified metadata and fresh inventories."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


SHA256 = re.compile(r"^[0-9a-f]{64}$")
TIMESTAMP = "%Y-%m-%dT%H:%M:%SZ"


class RetentionError(RuntimeError):
    """Fail-closed retention validation error."""


def canonical_json(value: Any) -> bytes:
    return (
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        .encode("ascii")
        + b"\n"
    )


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def checked_file(path: Path, expected: str, label: str) -> None:
    if not SHA256.fullmatch(expected):
        raise RetentionError(f"{label} expected SHA-256 is malformed")
    if not path.is_file() or path.is_symlink():
        raise RetentionError(f"{label} must be a regular non-symlink file")
    if sha256_file(path) != expected:
        raise RetentionError(f"{label} digest mismatch")


def load_json(path: Path, label: str) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RetentionError(f"{label} is not valid UTF-8 JSON: {exc}") from exc


def parse_timestamp(value: Any, label: str) -> datetime:
    if not isinstance(value, str):
        raise RetentionError(f"{label} must be an RFC3339 UTC timestamp")
    try:
        parsed = datetime.strptime(value, TIMESTAMP).replace(tzinfo=timezone.utc)
    except ValueError as exc:
        raise RetentionError(f"{label} must use YYYY-MM-DDTHH:MM:SSZ") from exc
    return parsed


def selected_completion(info: Any, selected_set: str) -> datetime:
    if not isinstance(info, list) or len(info) != 1 or not isinstance(info[0], dict):
        raise RetentionError("selected-set info must contain exactly one stanza")
    stanza = info[0]
    if not isinstance(stanza.get("status"), dict) or stanza["status"].get("code") != 0:
        raise RetentionError("selected-set repository status is not ok")
    backups = stanza.get("backup")
    if not isinstance(backups, list) or len(backups) != 1:
        raise RetentionError("selected-set info must contain exactly one backup")
    backup = backups[0]
    if (
        not isinstance(backup, dict)
        or backup.get("label") != selected_set
        or backup.get("type") != "full"
        or backup.get("error") is not False
    ):
        raise RetentionError("selected-set info does not identify the healthy full")
    timestamp = backup.get("timestamp")
    if not isinstance(timestamp, dict) or not isinstance(timestamp.get("stop"), int):
        raise RetentionError("selected full completion timestamp is missing")
    return datetime.fromtimestamp(timestamp["stop"], timezone.utc)


def now_utc(explicit: str | None) -> datetime:
    if explicit is None:
        return datetime.now(timezone.utc).replace(microsecond=0)
    if os.environ.get("POSTGRES_RESTORE_TEST_MODE") != "1":
        raise RetentionError("--now is forbidden outside explicit test mode")
    return parse_timestamp(explicit, "test now")


def require_exact_keys(value: dict[str, Any], keys: set[str], label: str) -> None:
    if set(value) != keys:
        raise RetentionError(f"{label} has missing or unknown fields")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("acceptance", "authorization"), required=True)
    parser.add_argument("--selected-set", required=True)
    parser.add_argument("--selected-info", required=True, type=Path)
    parser.add_argument("--selected-info-sha256", required=True)
    parser.add_argument("--scheduler-inventory", required=True, type=Path)
    parser.add_argument("--scheduler-inventory-sha256", required=True)
    parser.add_argument("--repository-inventory", required=True, type=Path)
    parser.add_argument("--repository-inventory-sha256", required=True)
    parser.add_argument("--rollout-start")
    parser.add_argument("--now")
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()

    try:
        checked_file(
            args.selected_info, args.selected_info_sha256, "selected-set info"
        )
        checked_file(
            args.scheduler_inventory,
            args.scheduler_inventory_sha256,
            "scheduler inventory",
        )
        checked_file(
            args.repository_inventory,
            args.repository_inventory_sha256,
            "repository inventory",
        )
        completed_at = selected_completion(
            load_json(args.selected_info, "selected-set info"), args.selected_set
        )
        scheduler = load_json(args.scheduler_inventory, "scheduler inventory")
        repository = load_json(args.repository_inventory, "repository inventory")
        if not isinstance(scheduler, dict) or not isinstance(repository, dict):
            raise RetentionError("scheduler/repository inventories must be objects")
        require_exact_keys(
            scheduler,
            {
                "schema_version",
                "generated_at_utc",
                "full_jobs_count",
                "timezone",
                "future_fulls_utc",
            },
            "scheduler inventory",
        )
        require_exact_keys(
            repository,
            {
                "schema_version",
                "generated_at_utc",
                "retention_full",
                "retention_full_type",
                "selected_set",
                "completed_fulls",
            },
            "repository inventory",
        )
        if (
            scheduler["schema_version"] != 1
            or scheduler["full_jobs_count"] != 1
            or scheduler["timezone"] != "UTC"
            or repository["schema_version"] != 1
            or repository["retention_full"] != 12
            or repository["retention_full_type"] != "count"
            or repository["selected_set"] != args.selected_set
        ):
            raise RetentionError("inventory policy or selected set is not exact")

        checked_at = now_utc(args.now)
        scheduler_at = parse_timestamp(
            scheduler["generated_at_utc"], "scheduler generated_at_utc"
        )
        repository_at = parse_timestamp(
            repository["generated_at_utc"], "repository generated_at_utc"
        )
        max_age = timedelta(minutes=15) if args.mode == "authorization" else timedelta(
            hours=24
        )
        for label, generated_at in (
            ("scheduler", scheduler_at),
            ("repository", repository_at),
        ):
            age = checked_at - generated_at
            if age < timedelta(0) or age > max_age:
                raise RetentionError(f"{label} inventory is not fresh")

        fulls = repository["completed_fulls"]
        if not isinstance(fulls, list) or not fulls:
            raise RetentionError("repository inventory has no completed fulls")
        completed_entries: list[tuple[str, datetime]] = []
        for index, item in enumerate(fulls):
            if not isinstance(item, dict):
                raise RetentionError("completed full entry is not an object")
            require_exact_keys(
                item,
                {"label", "completed_at_utc", "type", "status"},
                f"completed full {index}",
            )
            if item["type"] != "full" or item["status"] != "complete":
                raise RetentionError("repository inventory contains a non-complete full")
            completed_entries.append(
                (
                    str(item["label"]),
                    parse_timestamp(
                        item["completed_at_utc"], f"completed full {index} timestamp"
                    ),
                )
            )
        if any(timestamp > checked_at for _, timestamp in completed_entries):
            raise RetentionError("repository inventory contains a future completion")
        selected_entries = [
            timestamp
            for label, timestamp in completed_entries
            if label == args.selected_set
        ]
        if selected_entries != [completed_at]:
            raise RetentionError(
                "repository inventory does not bind the exact selected completion"
            )
        newer_fulls = sum(
            1 for _, timestamp in completed_entries if timestamp > completed_at
        )
        remaining_slots = 12 - newer_fulls
        if remaining_slots <= 0:
            raise RetentionError("selected full has already expired by count")

        future_values = scheduler["future_fulls_utc"]
        if not isinstance(future_values, list) or len(future_values) != 12:
            raise RetentionError("scheduler inventory must contain the next 12 fulls")
        future_fulls = [
            parse_timestamp(value, f"future full {index}")
            for index, value in enumerate(future_values)
        ]
        if future_fulls != sorted(set(future_fulls)):
            raise RetentionError("future full schedule is not unique and ordered")
        if future_fulls[0] <= checked_at:
            raise RetentionError("future full schedule contains a past/nonfuture run")
        expiry_at = future_fulls[remaining_slots - 1]
        retention_valid_until = expiry_at - timedelta(seconds=1)
        latest_rollout_start = completed_at + timedelta(days=21)
        required_from_completion = completed_at + timedelta(days=70)
        if retention_valid_until < required_from_completion:
            raise RetentionError(
                "selected set does not cover 21 + 35 + 14 days from completion"
            )

        packet: dict[str, Any] = {
            "schema_version": 1,
            "mode": args.mode,
            "status": "RETENTION_ACCEPTED",
            "selected_set_ref_sha256": hashlib.sha256(
                args.selected_set.encode("utf-8")
            ).hexdigest(),
            "selected_info_sha256": args.selected_info_sha256,
            "selected_full_completed_at_utc": completed_at.strftime(TIMESTAMP),
            "scheduler_inventory_sha256": args.scheduler_inventory_sha256,
            "repository_inventory_sha256": args.repository_inventory_sha256,
            "inventory_checked_at_utc": checked_at.strftime(TIMESTAMP),
            "completed_newer_fulls": newer_fulls,
            "retention_full_count": 12,
            "retention_valid_until_utc": retention_valid_until.strftime(TIMESTAMP),
            "latest_rollout_start_utc": latest_rollout_start.strftime(TIMESTAMP),
            "required_from_completion_through_utc": required_from_completion.strftime(
                TIMESTAMP
            ),
            "authorization_status": "NOT_AUTHORIZED",
        }
        if args.mode == "authorization":
            if args.rollout_start is None:
                raise RetentionError("authorization requires actual rollout start")
            rollout_start = parse_timestamp(
                args.rollout_start, "actual rollout start"
            )
            if not (
                checked_at - timedelta(minutes=5)
                <= rollout_start
                <= checked_at + timedelta(minutes=15)
            ):
                raise RetentionError(
                    "actual rollout start is not contemporaneous with authorization"
                )
            if rollout_start > latest_rollout_start:
                raise RetentionError(
                    "actual rollout starts more than 21 days after backup completion"
                )
            rollout_required_through = rollout_start + timedelta(days=35 + 14)
            if retention_valid_until < rollout_required_through:
                raise RetentionError(
                    "selected set does not cover rollback plus safety margin"
                )
            packet.update(
                {
                    "status": "RETENTION_AUTHORIZED",
                    "authorization_status": "AUTHORIZED",
                    "authorization_checked_at_utc": checked_at.strftime(TIMESTAMP),
                    "actual_rollout_start_utc": rollout_start.strftime(TIMESTAMP),
                    "rollout_required_through_utc": rollout_required_through.strftime(
                        TIMESTAMP
                    ),
                }
            )
        elif args.rollout_start is not None:
            raise RetentionError("acceptance mode must not claim an actual rollout start")

        args.output.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        args.output.write_bytes(canonical_json(packet))
        os.chmod(args.output, 0o600)
        print(f"retention_packet_sha256={sha256_file(args.output)}")
        print(f"retention_valid_until_utc={packet['retention_valid_until_utc']}")
        print(f"status={packet['status']}")
        return 0
    except RetentionError as exc:
        print(f"STOP: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
