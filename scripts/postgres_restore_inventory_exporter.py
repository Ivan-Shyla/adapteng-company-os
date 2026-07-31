#!/usr/bin/env python3
"""Export authentic weekly scheduler and pgBackRest full-set inventories."""

from __future__ import annotations

import argparse
import configparser
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


SCRIPT_DIR = Path(__file__).resolve().parent
MANIFEST = SCRIPT_DIR / "postgres_restore_inventory_exporter_manifest.json"
TIMER_UNIT = Path("/etc/systemd/system/adapteng-pgbackrest-full.timer")
SERVICE_UNIT = Path("/etc/systemd/system/adapteng-pgbackrest-full.service")
DIFF_TIMER_UNIT = Path("/etc/systemd/system/adapteng-pgbackrest-diff.timer")
DIFF_SERVICE_UNIT = Path("/etc/systemd/system/adapteng-pgbackrest-diff.service")
PGBACKREST_CONFIG = "/etc/pgbackrest/pgbackrest.conf"
TIMESTAMP = "%Y-%m-%dT%H:%M:%SZ"


class ExporterError(RuntimeError):
    """Fail-closed inventory exporter error."""


def canonical_json(value: Any) -> bytes:
    return (
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        .encode("ascii")
        + b"\n"
    )


def secure_bytes(path: Path, label: str, *, restricted: bool = False) -> bytes:
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        info = os.fstat(descriptor)
        forbidden_mode = 0o077 if restricted else 0o022
        if (
            os.geteuid() != 0
            or info.st_uid != 0
            or not stat.S_ISREG(info.st_mode)
            or info.st_mode & forbidden_mode
        ):
            raise ExporterError(f"{label} ownership/mode is not secure")
        chunks: list[bytes] = []
        while chunk := os.read(descriptor, 65536):
            chunks.append(chunk)
        return b"".join(chunks)
    finally:
        os.close(descriptor)


def write_exclusive(path: Path, payload: bytes) -> None:
    parent = path.parent.lstat()
    if (
        parent.st_uid != 0
        or not stat.S_ISDIR(parent.st_mode)
        or parent.st_mode & 0o077
    ):
        raise ExporterError("inventory output directory is not root-owned/private")
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


def next_weekly_slots(now: datetime) -> list[datetime]:
    current = now.astimezone(timezone.utc).replace(microsecond=0)
    days_until_sunday = (6 - current.weekday()) % 7
    first = (current + timedelta(days=days_until_sunday)).replace(
        hour=2, minute=0, second=0
    )
    if first <= current:
        first += timedelta(days=7)
    return [first + timedelta(days=7 * index) for index in range(12)]


def load_policy() -> tuple[dict[str, Any], str]:
    raw = secure_bytes(MANIFEST, "inventory exporter manifest")
    policy = json.loads(raw)
    required = {
        "schema_version",
        "status",
        "exporter_id",
        "exporter_version",
        "artifact_sha256",
        "timer_unit_sha256",
        "service_unit_sha256",
        "on_calendar",
        "diff_timer_unit_sha256",
        "diff_service_unit_sha256",
        "diff_on_calendar",
        "scheduler_output_schema_version",
        "repository_output_schema_version",
    }
    if not isinstance(policy, dict) or set(policy) != required:
        raise ExporterError("inventory exporter manifest fields are not exact")
    if policy["schema_version"] != 1 or policy["status"] != "APPROVED":
        raise ExporterError("inventory exporter manifest is NOT_CONFIGURED")
    if hashlib.sha256(Path(__file__).read_bytes()).hexdigest() != (
        policy["artifact_sha256"]
    ):
        raise ExporterError("inventory exporter artifact digest mismatch")
    return policy, hashlib.sha256(raw).hexdigest()


def validate_unit_pair(
    timer: Path,
    service: Path,
    *,
    timer_sha256: str,
    service_sha256: str,
    on_calendar: str,
    label: str,
) -> None:
    for unit, expected_path in ((timer, timer), (service, service)):
        properties = subprocess.run(
            [
                "systemctl",
                "show",
                unit.name,
                "--property=FragmentPath",
                "--property=DropInPaths",
            ],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        ).stdout
        validate_effective_unit_properties(properties, expected_path)
    timer_raw = secure_bytes(timer, f"{label} timer unit")
    service_raw = secure_bytes(service, f"{label} service unit")
    if hashlib.sha256(timer_raw).hexdigest() != timer_sha256 or (
        hashlib.sha256(service_raw).hexdigest() != service_sha256
    ):
        raise ExporterError(f"{label} scheduler unit digest mismatch")
    parser = configparser.ConfigParser(interpolation=None, strict=True)
    parser.optionxform = str
    parser.read_string(timer_raw.decode("utf-8"))
    if parser.get("Timer", "OnCalendar", fallback=None) != on_calendar:
        raise ExporterError(f"{label} scheduler cadence differs from policy")
    for action in ("is-enabled", "is-active"):
        subprocess.run(
            ["systemctl", action, "--quiet", timer.name],
            check=True,
            capture_output=True,
        )


def validate_effective_unit_properties(value: str, expected_path: Path) -> None:
    properties = dict(
        line.split("=", 1) for line in value.splitlines() if "=" in line
    )
    if (
        set(properties) != {"FragmentPath", "DropInPaths"}
        or properties["FragmentPath"] != expected_path.as_posix()
        or properties["DropInPaths"] != ""
    ):
        raise ExporterError("systemd unit fragment/drop-in state is not exact")


def validate_scheduler(policy: dict[str, Any]) -> None:
    validate_unit_pair(
        TIMER_UNIT,
        SERVICE_UNIT,
        timer_sha256=policy["timer_unit_sha256"],
        service_sha256=policy["service_unit_sha256"],
        on_calendar=policy["on_calendar"],
        label="weekly full",
    )
    validate_unit_pair(
        DIFF_TIMER_UNIT,
        DIFF_SERVICE_UNIT,
        timer_sha256=policy["diff_timer_unit_sha256"],
        service_sha256=policy["diff_service_unit_sha256"],
        on_calendar=policy["diff_on_calendar"],
        label="daily differential",
    )


def is_full_backup_surface(payload: bytes) -> bool:
    text = payload.decode("utf-8", errors="replace").lower()
    return "pgbackrest" in text and bool(
        re.search(r"\bbackup\b|--type(?:=|\s+)full\b", text)
    )


def validate_no_additional_full_jobs(
    surfaces: list[tuple[str, bytes]],
) -> str:
    for source, payload in surfaces:
        if is_full_backup_surface(payload):
            raise ExporterError(f"additional pgBackRest backup schedule found: {source}")
    sanitized = [
        {
            "source_sha256": hashlib.sha256(source.encode("utf-8")).hexdigest(),
            "content_sha256": hashlib.sha256(payload).hexdigest(),
        }
        for source, payload in sorted(surfaces)
    ]
    return hashlib.sha256(canonical_json(sanitized)).hexdigest()


def command_bytes(command: list[str]) -> bytes:
    completed = subprocess.run(command, check=True, capture_output=True)
    return completed.stdout


def reconcile_timer_names(
    enabled: list[str], active: list[str], approved: set[str]
) -> list[str]:
    if any(enabled.count(timer) != 1 for timer in approved) or any(
        active.count(timer) != 1 for timer in approved
    ):
        raise ExporterError("approved backup timers are not uniquely enabled/active")
    return sorted(set(enabled) | set(active))


def scheduler_surfaces() -> list[tuple[str, bytes]]:
    surfaces: list[tuple[str, bytes]] = []
    timer_list = command_bytes(
        [
            "systemctl",
            "list-unit-files",
            "--type=timer",
            "--state=enabled",
            "--no-legend",
            "--no-pager",
        ]
    ).decode("utf-8")
    enabled_timers = [
        line.split()[0] for line in timer_list.splitlines() if line.split()
    ]
    active_list = command_bytes(
        [
            "systemctl",
            "list-units",
            "--type=timer",
            "--state=active",
            "--no-legend",
            "--no-pager",
            "--plain",
        ]
    ).decode("utf-8")
    active_timers = [
        line.split()[0] for line in active_list.splitlines() if line.split()
    ]
    approved_timers = {TIMER_UNIT.name, DIFF_TIMER_UNIT.name}
    timers = reconcile_timer_names(
        enabled_timers, active_timers, approved_timers
    )
    for timer in timers:
        if timer in approved_timers:
            continue
        service = command_bytes(
            ["systemctl", "show", timer, "--property=Unit", "--value"]
        ).decode("utf-8").strip()
        if not service:
            service = timer.removesuffix(".timer") + ".service"
        unit = command_bytes(["systemctl", "cat", service, "--no-pager"])
        surfaces.append((f"systemd:{timer}:{service}", unit))
        for executable in re.findall(rb"(?m)(/[A-Za-z0-9_./-]+)", unit):
            path = Path(executable.decode("ascii"))
            if path.is_file() and not path.is_symlink():
                surfaces.append(
                    (
                        f"systemd-exec:{timer}:{path}",
                        secure_bytes(path, "scheduled executable"),
                    )
                )

    for root in (
        Path("/etc/crontab"),
        Path("/etc/anacrontab"),
        Path("/etc/cron.d"),
        Path("/etc/cron.hourly"),
        Path("/etc/cron.daily"),
        Path("/etc/cron.weekly"),
        Path("/etc/cron.monthly"),
        Path("/var/spool/cron/crontabs"),
        Path("/var/spool/cron/atjobs"),
    ):
        candidates = (
            [root]
            if root.is_file()
            else list(root.rglob("*"))
            if root.is_dir()
            else []
        )
        for path in candidates:
            target = path.resolve() if path.is_symlink() else path
            if target.is_file():
                surfaces.append(
                    (
                        f"scheduler-file:{path}",
                        secure_bytes(target, "scheduler file"),
                    )
                )

    container_ids = command_bytes(
        ["docker", "container", "ls", "--all", "--quiet"]
    ).decode("ascii").split()
    if container_ids:
        inspected = json.loads(
            command_bytes(["docker", "container", "inspect", *container_ids])
        )
        if not isinstance(inspected, list):
            raise ExporterError("Docker scheduler inventory is malformed")
        for container in inspected:
            if not isinstance(container, dict):
                raise ExporterError("Docker scheduler entry is malformed")
            config = container.get("Config", {})
            scheduler_config = {
                "Entrypoint": config.get("Entrypoint"),
                "Cmd": config.get("Cmd"),
                "Labels": config.get("Labels"),
            }
            surfaces.append(
                (
                    "docker-scheduler:"
                    + hashlib.sha256(
                        str(container.get("Id", "")).encode("utf-8")
                    ).hexdigest(),
                    canonical_json(scheduler_config),
                )
            )
    return surfaces


def retention_policy(config_raw: bytes) -> tuple[int, str]:
    parser = configparser.ConfigParser(interpolation=None, strict=True)
    parser.optionxform = str
    parser.read_string(config_raw.decode("utf-8"))
    if "global" not in parser:
        raise ExporterError("pgBackRest config lacks a global section")
    full = parser["global"].get("repo1-retention-full")
    full_type = parser["global"].get("repo1-retention-full-type")
    if full != "12" or full_type != "count":
        raise ExporterError("pgBackRest full retention is not exact 12/count")
    return int(full), full_type


def repository_inventory(value: Any, selected_set: str) -> list[dict[str, str]]:
    if not isinstance(value, list) or len(value) != 1 or not isinstance(value[0], dict):
        raise ExporterError("pgBackRest info must contain one stanza")
    stanza = value[0]
    if stanza.get("status", {}).get("code") != 0:
        raise ExporterError("pgBackRest repository status is not ok")
    backups = stanza.get("backup")
    if not isinstance(backups, list):
        raise ExporterError("pgBackRest backup inventory is missing")
    fulls: list[dict[str, str]] = []
    for backup in backups:
        if not isinstance(backup, dict) or backup.get("type") != "full":
            continue
        timestamp = backup.get("timestamp", {})
        if backup.get("error") is not False or not isinstance(timestamp.get("stop"), int):
            raise ExporterError("completed full inventory contains an unhealthy set")
        fulls.append(
            {
                "label": str(backup.get("label", "")),
                "completed_at_utc": datetime.fromtimestamp(
                    timestamp["stop"], timezone.utc
                ).strftime(TIMESTAMP),
                "type": "full",
                "status": "complete",
            }
        )
    fulls.sort(key=lambda item: item["completed_at_utc"])
    if sum(item["label"] == selected_set for item in fulls) != 1:
        raise ExporterError("selected full is not unique in repository inventory")
    return fulls


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--selected-set", required=True)
    parser.add_argument("--scheduler-output", required=True, type=Path)
    parser.add_argument("--repository-output", required=True, type=Path)
    args = parser.parse_args()
    try:
        if os.name != "posix" or os.geteuid() != 0:
            raise ExporterError("inventory exporter requires a POSIX root host")
        policy, policy_sha256 = load_policy()
        validate_scheduler(policy)
        scheduler_surface_sha256 = validate_no_additional_full_jobs(
            scheduler_surfaces()
        )
        pgbackrest_config_raw = secure_bytes(
            Path(PGBACKREST_CONFIG), "pgBackRest config"
        )
        retention_full, retention_full_type = retention_policy(
            pgbackrest_config_raw
        )
        completed = subprocess.run(
            [
                "pgbackrest",
                f"--config={PGBACKREST_CONFIG}",
                "--stanza=adapteng-ops",
                "--repo=1",
                "--output=json",
                "info",
            ],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        now = datetime.now(timezone.utc).replace(microsecond=0)
        identity = {
            "exporter_id": policy["exporter_id"],
            "exporter_version": policy["exporter_version"],
            "exporter_artifact_sha256": policy["artifact_sha256"],
        }
        scheduler = {
            "schema_version": policy["scheduler_output_schema_version"],
            "generated_at_utc": now.strftime(TIMESTAMP),
            "full_jobs_count": 1,
            "differential_jobs_count": 1,
            "scheduler_surface_sha256": scheduler_surface_sha256,
            "timezone": "UTC",
            "future_fulls_utc": [
                value.strftime(TIMESTAMP) for value in next_weekly_slots(now)
            ],
            **identity,
        }
        repository = {
            "schema_version": policy["repository_output_schema_version"],
            "generated_at_utc": now.strftime(TIMESTAMP),
            "pgbackrest_config_sha256": hashlib.sha256(
                pgbackrest_config_raw
            ).hexdigest(),
            "retention_full": retention_full,
            "retention_full_type": retention_full_type,
            "selected_set": args.selected_set,
            "completed_fulls": repository_inventory(
                json.loads(completed.stdout), args.selected_set
            ),
            **identity,
        }
        write_exclusive(args.scheduler_output, canonical_json(scheduler))
        write_exclusive(args.repository_output, canonical_json(repository))
        print(f"inventory_exporter_manifest_sha256={policy_sha256}")
        print("scheduler_inventory_status=exported")
        print("repository_inventory_status=exported")
        return 0
    except (
        OSError,
        UnicodeError,
        configparser.Error,
        json.JSONDecodeError,
        subprocess.CalledProcessError,
        ExporterError,
    ) as exc:
        print(f"STOP: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
