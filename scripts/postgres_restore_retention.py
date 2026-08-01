#!/usr/bin/env python3
"""Derive selected-set retention from verified metadata and fresh inventories."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


SHA256 = re.compile(r"^[0-9a-f]{64}$")
TIMESTAMP = "%Y-%m-%dT%H:%M:%SZ"
EXPORTER_MANIFEST = (
    Path(__file__).resolve().parent
    / "postgres_restore_inventory_exporter_manifest.json"
)
EXPORTER = (
    Path(__file__).resolve().parent / "postgres_restore_inventory_exporter.py"
)
STATE_ROOT = Path("/var/lib/adapteng/postgres-restore-rehearsal/retention")
ACCEPTED_PACKET = STATE_ROOT / "accepted.json"
ACCEPTED_SELECTED_INFO = STATE_ROOT / "accepted-selected-set-info.json"
ACCEPTED_SCHEDULER = STATE_ROOT / "accepted-scheduler-inventory.json"
ACCEPTED_REPOSITORY = STATE_ROOT / "accepted-repository-inventory.json"
AUTHORIZED_PACKET = STATE_ROOT / "authorized.json"


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


def checked_bytes(path: Path, expected: str, label: str) -> bytes:
    if not SHA256.fullmatch(expected):
        raise RetentionError(f"{label} expected SHA-256 is malformed")
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError as exc:
        raise RetentionError(f"{label} cannot be opened: {exc}") from exc
    try:
        info = os.fstat(descriptor)
        if (
            os.geteuid() != 0
            or info.st_uid != 0
            or not stat.S_ISREG(info.st_mode)
            or info.st_mode & 0o077
        ):
            raise RetentionError(f"{label} must be root-owned mode 0600")
        payload = bytearray()
        while chunk := os.read(descriptor, 1024 * 1024):
            payload.extend(chunk)
    finally:
        os.close(descriptor)
    raw = bytes(payload)
    if hashlib.sha256(raw).hexdigest() != expected:
        raise RetentionError(f"{label} digest mismatch")
    return raw


def decode_json(raw: bytes, label: str) -> Any:
    try:
        return json.loads(raw)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise RetentionError(f"{label} is not valid UTF-8 JSON: {exc}") from exc


def write_exclusive(path: Path, payload: bytes) -> None:
    descriptor = os.open(
        path,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
        0o600,
    )
    try:
        offset = 0
        while offset < len(payload):
            offset += os.write(descriptor, payload[offset:])
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


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


def require_exact_keys(value: dict[str, Any], keys: set[str], label: str) -> None:
    if set(value) != keys:
        raise RetentionError(f"{label} has missing or unknown fields")


def load_exporter_manifest() -> tuple[dict[str, Any], str]:
    if not EXPORTER_MANIFEST.is_file() or EXPORTER_MANIFEST.is_symlink():
        raise RetentionError("inventory exporter manifest is unavailable")
    raw = EXPORTER_MANIFEST.read_bytes()
    value = json.loads(raw)
    required = {
        "schema_version",
        "status",
        "exporter_id",
        "exporter_version",
        "artifact_sha256",
        "scheduler_output_schema_version",
        "repository_output_schema_version",
        "host_scope",
        "repository_write_capability",
    }
    if (
        not isinstance(value, dict)
        or set(value) != required
        or value.get("schema_version") != 3
    ):
        raise RetentionError("inventory exporter manifest is not exact v3")
    if value.get("status") != "APPROVED":
        raise RetentionError("inventory exporter manifest is NOT_CONFIGURED")
    for field in ("exporter_id", "exporter_version", "artifact_sha256"):
        if not value.get(field):
            raise RetentionError(f"inventory exporter {field} is not pinned")
    if not SHA256.fullmatch(str(value["artifact_sha256"])):
        raise RetentionError("inventory exporter artifact digest is malformed")
    if (
        hashlib.sha256(EXPORTER.read_bytes()).hexdigest()
        != value["artifact_sha256"]
        or value["scheduler_output_schema_version"] != 3
        or value["repository_output_schema_version"] != 1
        or not isinstance(value["repository_write_capability"], dict)
    ):
        raise RetentionError("inventory exporter policy identity is not exact")
    return value, hashlib.sha256(raw).hexdigest()


def validate_weekly_schedule(
    values: list[datetime], checked_at: datetime
) -> None:
    if len(values) != 12 or values != sorted(set(values)):
        raise RetentionError("future full schedule must contain 12 unique ordered slots")
    if values[0] <= checked_at or values[0] > checked_at + timedelta(days=7):
        raise RetentionError("first weekly full slot is not within one week")
    if any(
        current - previous != timedelta(days=7)
        for previous, current in zip(values, values[1:])
    ):
        raise RetentionError("full schedule is not an exact weekly cadence")


def load_canonical_packet(
    path: Path, expected_sha256: str, label: str
) -> tuple[dict[str, Any], bytes]:
    raw = checked_bytes(path, expected_sha256, label)
    return parse_canonical_packet(raw, expected_sha256, label)


def parse_canonical_packet(
    raw: bytes, expected_sha256: str, label: str
) -> tuple[dict[str, Any], bytes]:
    if hashlib.sha256(raw).hexdigest() != expected_sha256:
        raise RetentionError(f"{label} digest mismatch")
    value = decode_json(raw, label)
    if not isinstance(value, dict) or canonical_json(value) != raw:
        raise RetentionError(f"{label} is not canonical JSON")
    return value, raw


def validate_accepted_binding(
    accepted: dict[str, Any],
    current: dict[str, Any],
    *,
    accepted_scheduler_sha256: str,
    accepted_repository_sha256: str,
    exporter_manifest_sha256: str,
) -> None:
    expected = {
        "packet_kind": "ACCEPTED_RETENTION",
        "scheduler_inventory_sha256": accepted_scheduler_sha256,
        "repository_inventory_sha256": accepted_repository_sha256,
        "selected_set_ref_sha256": current["selected_set_ref_sha256"],
        "selected_set_info_sha256": current["selected_set_info_sha256"],
        "completed_at": current["completed_at"],
        "inventory_exporter_manifest_sha256": exporter_manifest_sha256,
        "weekly_cadence_seconds": 604800,
        "weekly_slot_count": 12,
        "repository_write_capability_sha256": current[
            "repository_write_capability_sha256"
        ],
        "capability_inventory_sha256": current["capability_inventory_sha256"],
        "full_job_identity_sha256": current["full_job_identity_sha256"],
        "differential_job_identity_sha256": current[
            "differential_job_identity_sha256"
        ],
    }
    for field, value in expected.items():
        if accepted.get(field) != value:
            raise RetentionError(f"accepted packet binding mismatch: {field}")


def sanitized_consumer_fields(packet: dict[str, Any]) -> dict[str, Any]:
    fields = (
        "completed_at",
        "selected_set_info_sha256",
        "scheduler_inventory_sha256",
        "scheduler_inventory_observed_at",
        "retention_valid_until",
    )
    if any(field not in packet for field in fields):
        raise RetentionError("consumer evidence fields are incomplete")
    return {field: packet[field] for field in fields}


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
    parser.add_argument("--accepted-packet-sha256")
    args = parser.parse_args()

    try:
        exporter, exporter_manifest_sha256 = load_exporter_manifest()
        accepted_packet: dict[str, Any] | None = None
        accepted_packet_sha256: str | None = None
        if args.mode == "authorization":
            if args.accepted_packet_sha256 is None:
                raise RetentionError(
                    "authorization requires the exact accepted packet reference"
                )
            accepted_packet, accepted_raw = load_canonical_packet(
                ACCEPTED_PACKET,
                args.accepted_packet_sha256,
                "accepted retention packet",
            )
            accepted_packet_sha256 = hashlib.sha256(accepted_raw).hexdigest()
            if accepted_packet.get("packet_kind") != "ACCEPTED_RETENTION":
                raise RetentionError("accepted packet kind is invalid")
            checked_bytes(
                ACCEPTED_SELECTED_INFO,
                str(accepted_packet.get("selected_set_info_sha256")),
                "accepted selected-set info",
            )
            checked_bytes(
                ACCEPTED_SCHEDULER,
                str(accepted_packet.get("scheduler_inventory_sha256")),
                "accepted scheduler inventory",
            )
            checked_bytes(
                ACCEPTED_REPOSITORY,
                str(accepted_packet.get("repository_inventory_sha256")),
                "accepted repository inventory",
            )
        elif args.accepted_packet_sha256 is not None:
            raise RetentionError("acceptance must not consume an accepted packet")
        selected_info_raw = checked_bytes(
            args.selected_info, args.selected_info_sha256, "selected-set info"
        )
        scheduler_raw = checked_bytes(
            args.scheduler_inventory,
            args.scheduler_inventory_sha256,
            "scheduler inventory",
        )
        repository_raw = checked_bytes(
            args.repository_inventory,
            args.repository_inventory_sha256,
            "repository inventory",
        )
        completed_at = selected_completion(
            decode_json(selected_info_raw, "selected-set info"), args.selected_set
        )
        scheduler = decode_json(scheduler_raw, "scheduler inventory")
        repository = decode_json(repository_raw, "repository inventory")
        if not isinstance(scheduler, dict) or not isinstance(repository, dict):
            raise RetentionError("scheduler/repository inventories must be objects")
        require_exact_keys(
            scheduler,
            {
                "schema_version",
                "generated_at_utc",
                "full_jobs_count",
                "differential_jobs_count",
                "timezone",
                "future_fulls_utc",
                "repository_write_capability_sha256",
                "full_job_identity_sha256",
                "differential_job_identity_sha256",
                "capability_inventory_sha256",
                "runtime_writer_process_inventory_sha256",
                "scheduler_sources_count",
                "containers_count",
                "writer_processes_count",
                "unclassified_capability_surfaces",
                "host_scope_identity_sha256",
                "machine_id_sha256",
                "user_systemd_managers_count",
                "linger_accounts_count",
                "credential_metadata_sha256",
                "exporter_id",
                "exporter_version",
                "exporter_artifact_sha256",
            },
            "scheduler inventory",
        )
        require_exact_keys(
            repository,
            {
                "schema_version",
                "generated_at_utc",
                "pgbackrest_config_sha256",
                "retention_full",
                "retention_full_type",
                "selected_set",
                "completed_fulls",
                "exporter_id",
                "exporter_version",
                "exporter_artifact_sha256",
            },
            "repository inventory",
        )
        if (
            scheduler["schema_version"] != 3
            or scheduler["full_jobs_count"] != 1
            or scheduler["differential_jobs_count"] != 1
            or scheduler["timezone"] != "UTC"
            or scheduler["unclassified_capability_surfaces"] != 0
            or scheduler["user_systemd_managers_count"] != 0
            or scheduler["linger_accounts_count"] != 0
            or not all(
                SHA256.fullmatch(str(scheduler[field]))
                for field in (
                    "repository_write_capability_sha256",
                    "full_job_identity_sha256",
                    "differential_job_identity_sha256",
                    "capability_inventory_sha256",
                    "runtime_writer_process_inventory_sha256",
                    "host_scope_identity_sha256",
                    "machine_id_sha256",
                    "credential_metadata_sha256",
                )
            )
            or repository["schema_version"] != 1
            or repository["retention_full"] != 12
            or repository["retention_full_type"] != "count"
            or not SHA256.fullmatch(str(repository["pgbackrest_config_sha256"]))
            or repository["selected_set"] != args.selected_set
            or scheduler["exporter_id"] != exporter["exporter_id"]
            or scheduler["exporter_version"] != exporter["exporter_version"]
            or scheduler["exporter_artifact_sha256"] != exporter["artifact_sha256"]
            or repository["exporter_id"] != exporter["exporter_id"]
            or repository["exporter_version"] != exporter["exporter_version"]
            or repository["exporter_artifact_sha256"] != exporter["artifact_sha256"]
        ):
            raise RetentionError("inventory policy or selected set is not exact")

        checked_at = datetime.now(timezone.utc).replace(microsecond=0)
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
        validate_weekly_schedule(future_fulls, checked_at)
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
            "packet_kind": "ACCEPTED_RETENTION",
            "mode": args.mode,
            "status": "RETENTION_ACCEPTED",
            "selected_set_ref_sha256": hashlib.sha256(
                args.selected_set.encode("utf-8")
            ).hexdigest(),
            "selected_set_info_sha256": args.selected_info_sha256,
            "completed_at": completed_at.strftime(TIMESTAMP),
            "scheduler_inventory_sha256": args.scheduler_inventory_sha256,
            "scheduler_inventory_observed_at": scheduler_at.strftime(TIMESTAMP),
            "repository_inventory_sha256": args.repository_inventory_sha256,
            "inventory_checked_at_utc": checked_at.strftime(TIMESTAMP),
            "completed_newer_fulls": newer_fulls,
            "retention_full_count": 12,
            "retention_valid_until": retention_valid_until.strftime(TIMESTAMP),
            "latest_rollout_start_utc": latest_rollout_start.strftime(TIMESTAMP),
            "required_from_completion_through_utc": required_from_completion.strftime(
                TIMESTAMP
            ),
            "authorization_status": "NOT_AUTHORIZED",
            "inventory_exporter_id": exporter["exporter_id"],
            "inventory_exporter_version": exporter["exporter_version"],
            "inventory_exporter_artifact_sha256": exporter["artifact_sha256"],
            "inventory_exporter_manifest_sha256": exporter_manifest_sha256,
            "weekly_cadence_seconds": 604800,
            "weekly_slot_count": 12,
            "repository_write_capability_sha256": scheduler[
                "repository_write_capability_sha256"
            ],
            "capability_inventory_sha256": scheduler[
                "capability_inventory_sha256"
            ],
            "full_job_identity_sha256": scheduler["full_job_identity_sha256"],
            "differential_job_identity_sha256": scheduler[
                "differential_job_identity_sha256"
            ],
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
            if accepted_packet is None or accepted_packet_sha256 is None:
                raise RetentionError("accepted packet was not bound")
            validate_accepted_binding(
                accepted_packet,
                packet,
                accepted_scheduler_sha256=str(
                    accepted_packet["scheduler_inventory_sha256"]
                ),
                accepted_repository_sha256=str(
                    accepted_packet["repository_inventory_sha256"]
                ),
                exporter_manifest_sha256=exporter_manifest_sha256,
            )
            packet.update(
                {
                    "packet_kind": "AUTHORIZED_RETENTION",
                    "status": "RETENTION_AUTHORIZED",
                    "authorization_status": "AUTHORIZED",
                    "authorization_checked_at": checked_at.strftime(TIMESTAMP),
                    "actual_rollout_start": rollout_start.strftime(TIMESTAMP),
                    "rollout_required_through": rollout_required_through.strftime(
                        TIMESTAMP
                    ),
                    "accepted_packet_sha256": accepted_packet_sha256,
                    "accepted_scheduler_inventory_sha256": accepted_packet[
                        "scheduler_inventory_sha256"
                    ],
                    "current_scheduler_inventory_sha256": args.scheduler_inventory_sha256,
                }
            )
        elif args.rollout_start is not None:
            raise RetentionError("acceptance mode must not claim an actual rollout start")

        if args.mode == "acceptance":
            parent = STATE_ROOT.parent
            parent_info = parent.lstat()
            if (
                os.geteuid() != 0
                or parent_info.st_uid != 0
                or not stat.S_ISDIR(parent_info.st_mode)
                or parent_info.st_mode & 0o077
            ):
                raise RetentionError("retention state parent must be root-owned mode 0700")
            os.mkdir(STATE_ROOT, 0o700)
            write_exclusive(ACCEPTED_SELECTED_INFO, selected_info_raw)
            write_exclusive(ACCEPTED_SCHEDULER, scheduler_raw)
            write_exclusive(ACCEPTED_REPOSITORY, repository_raw)
            output = ACCEPTED_PACKET
        else:
            output = AUTHORIZED_PACKET
        write_exclusive(output, canonical_json(packet))
        print(f"retention_packet_sha256={sha256_file(output)}")
        print(f"retention_valid_until={packet['retention_valid_until']}")
        print(f"status={packet['status']}")
        consumer_fields = sanitized_consumer_fields(packet)
        print(
            "automation_consumer_fields="
            + canonical_json(consumer_fields).decode("ascii").strip()
        )
        return 0
    except RetentionError as exc:
        print(f"STOP: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
