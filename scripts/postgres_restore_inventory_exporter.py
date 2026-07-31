#!/usr/bin/env python3
"""Export capability-complete scheduler and pgBackRest repository inventories."""

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
PGBACKREST_CONFIG = Path("/etc/pgbackrest/pgbackrest.conf")
TIMESTAMP = "%Y-%m-%dT%H:%M:%SZ"
SHA256 = re.compile(r"^[0-9a-f]{64}$")
CLEAN_ENVIRONMENT = {
    "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
    "LANG": "C",
    "LC_ALL": "C",
}
SHELL_EXECUTABLES = {
    "/bin/bash",
    "/bin/dash",
    "/bin/sh",
    "/usr/bin/bash",
    "/usr/bin/dash",
    "/usr/bin/env",
    "/usr/bin/sh",
}
SENSITIVE_ENV_PREFIXES = ("AWS_", "B2_", "PGBACKREST_")


class ExporterError(RuntimeError):
    """Fail-closed inventory exporter error."""


def canonical_json(value: Any) -> bytes:
    return (
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        .encode("ascii")
        + b"\n"
    )


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


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
        payload = bytearray()
        while chunk := os.read(descriptor, 65536):
            payload.extend(chunk)
        return bytes(payload)
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
        "scheduler_output_schema_version",
        "repository_output_schema_version",
        "repository_write_capability",
    }
    if (
        not isinstance(policy, dict)
        or set(policy) != required
        or policy.get("schema_version") != 2
    ):
        raise ExporterError("inventory exporter manifest is not exact v2")
    if policy.get("status") != "APPROVED":
        raise ExporterError("inventory exporter manifest is NOT_CONFIGURED")
    if (
        not SHA256.fullmatch(str(policy.get("artifact_sha256")))
        or sha256_bytes(Path(__file__).read_bytes()) != policy["artifact_sha256"]
        or policy.get("scheduler_output_schema_version") != 2
        or policy.get("repository_output_schema_version") != 1
    ):
        raise ExporterError("inventory exporter artifact/schema identity is not exact")
    validate_capability_policy(policy["repository_write_capability"])
    return policy, sha256_bytes(raw)


def validate_capability_policy(capability: Any) -> None:
    required = {
        "principal",
        "uid",
        "credential_id",
        "config_path",
        "config_sha256",
        "pgbackrest_path",
        "pgbackrest_sha256",
        "full_job",
        "differential_job",
        "allowed_scheduler_sources",
        "allowed_containers",
        "allowed_writer_processes",
    }
    if not isinstance(capability, dict) or set(capability) != required:
        raise ExporterError("repository-write capability policy is not exact")
    if (
        not isinstance(capability["principal"], str)
        or not isinstance(capability["uid"], int)
        or capability["uid"] <= 0
        or capability["credential_id"] != "pgbackrest-repository-write"
        or capability["config_path"] != str(PGBACKREST_CONFIG)
        or not SHA256.fullmatch(str(capability["config_sha256"]))
        or capability["pgbackrest_path"] != "/usr/bin/pgbackrest"
        or not SHA256.fullmatch(str(capability["pgbackrest_sha256"]))
    ):
        raise ExporterError("repository-write capability identity is not pinned")
    for name, backup_type in (("full_job", "full"), ("differential_job", "diff")):
        validate_job_policy(capability[name], backup_type)
    for field in (
        "allowed_scheduler_sources",
        "allowed_containers",
        "allowed_writer_processes",
    ):
        values = capability[field]
        if not isinstance(values, list) or not all(
            isinstance(value, str) and SHA256.fullmatch(value) for value in values
        ):
            raise ExporterError(f"{field} is not an exact digest allowlist")
        if len(values) != len(set(values)):
            raise ExporterError(f"{field} contains duplicate identities")


def validate_job_policy(job: Any, backup_type: str) -> None:
    required = {
        "timer_name",
        "service_name",
        "timer_path",
        "service_path",
        "timer_sha256",
        "service_sha256",
        "on_calendar",
        "exec_path",
        "exec_sha256",
        "argv",
        "systemd_properties",
    }
    if not isinstance(job, dict) or set(job) != required:
        raise ExporterError(f"{backup_type} job policy is not exact")
    expected_calendar = (
        "Sun *-*-* 02:00:00 UTC"
        if backup_type == "full"
        else "Mon..Sat *-*-* 02:00:00 UTC"
    )
    expected_argv = [
        "--config=/etc/pgbackrest/pgbackrest.conf",
        "--stanza=adapteng-ops",
        "--repo=1",
        f"--type={backup_type}",
        "backup",
    ]
    if (
        job["exec_path"] != "/usr/bin/pgbackrest"
        or job["exec_path"] in SHELL_EXECUTABLES
        or job["argv"] != expected_argv
        or job["on_calendar"] != expected_calendar
        or not SHA256.fullmatch(str(job["timer_sha256"]))
        or not SHA256.fullmatch(str(job["service_sha256"]))
        or not SHA256.fullmatch(str(job["exec_sha256"]))
        or not isinstance(job["systemd_properties"], dict)
    ):
        raise ExporterError(f"{backup_type} job is not a direct pinned pgBackRest command")


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


def validate_job_artifacts(
    job: dict[str, Any],
    capability: dict[str, Any],
    *,
    command: Any = subprocess.run,
) -> dict[str, Any]:
    timer = Path(job["timer_path"])
    service = Path(job["service_path"])
    executable = Path(job["exec_path"])
    for path, expected, label in (
        (timer, job["timer_sha256"], "timer"),
        (service, job["service_sha256"], "service"),
        (executable, job["exec_sha256"], "executable"),
    ):
        if path.is_symlink() or path.resolve(strict=True) != path:
            raise ExporterError(f"approved {label} path is a symlink/redirect")
        if sha256_bytes(secure_bytes(path, f"approved {label}")) != expected:
            raise ExporterError(f"approved {label} digest changed")
    if job["exec_sha256"] != capability["pgbackrest_sha256"]:
        raise ExporterError("job executable differs from the capability binary")
    timer_raw = secure_bytes(timer, "approved timer")
    parser = configparser.ConfigParser(interpolation=None, strict=True)
    parser.optionxform = str
    parser.read_string(timer_raw.decode("utf-8"))
    if parser.get("Timer", "OnCalendar", fallback=None) != job["on_calendar"]:
        raise ExporterError("approved timer cadence changed")
    for unit, path in ((job["timer_name"], timer), (job["service_name"], service)):
        properties = command(
            [
                "systemctl",
                "show",
                unit,
                "--property=FragmentPath",
                "--property=DropInPaths",
            ],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=CLEAN_ENVIRONMENT,
        ).stdout
        validate_effective_unit_properties(properties, path)
    service_properties = command(
        [
            "systemctl",
            "show",
            job["service_name"],
            *[
                f"--property={name}"
                for name in sorted(job["systemd_properties"])
            ],
        ],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=CLEAN_ENVIRONMENT,
    ).stdout
    measured = dict(
        line.split("=", 1) for line in service_properties.splitlines() if "=" in line
    )
    if measured != job["systemd_properties"]:
        raise ExporterError("approved service effective capability/command changed")
    for action in ("is-enabled", "is-active"):
        command(
            ["systemctl", action, "--quiet", job["timer_name"]],
            check=True,
            capture_output=True,
            env=CLEAN_ENVIRONMENT,
        )
    return {
        "timer_name_sha256": sha256_bytes(job["timer_name"].encode("utf-8")),
        "service_name_sha256": sha256_bytes(job["service_name"].encode("utf-8")),
        "timer_sha256": job["timer_sha256"],
        "service_sha256": job["service_sha256"],
        "exec_path_sha256": sha256_bytes(job["exec_path"].encode("utf-8")),
        "exec_sha256": job["exec_sha256"],
        "argv_sha256": sha256_bytes(canonical_json(job["argv"])),
        "properties_sha256": sha256_bytes(
            canonical_json(job["systemd_properties"])
        ),
    }


def record_sha256(record: dict[str, Any]) -> str:
    return sha256_bytes(canonical_json(record))


def validate_capability_inventory(
    *,
    scheduler_records: list[dict[str, Any]],
    container_records: list[dict[str, Any]],
    writer_process_records: list[dict[str, Any]],
    capability: dict[str, Any],
) -> dict[str, Any]:
    actual = {
        "scheduler": sorted(record_sha256(value) for value in scheduler_records),
        "containers": sorted(record_sha256(value) for value in container_records),
        "writer_processes": sorted(
            record_sha256(value) for value in writer_process_records
        ),
    }
    expected = {
        "scheduler": sorted(capability["allowed_scheduler_sources"]),
        "containers": sorted(capability["allowed_containers"]),
        "writer_processes": sorted(capability["allowed_writer_processes"]),
    }
    if actual != expected:
        raise ExporterError(
            "scheduler/container/process capability inventory is not fully classified"
        )
    return {
        "capability_inventory_sha256": sha256_bytes(canonical_json(actual)),
        "scheduler_sources_count": len(actual["scheduler"]),
        "containers_count": len(actual["containers"]),
        "writer_processes_count": len(actual["writer_processes"]),
        "unclassified_capability_surfaces": 0,
    }


def command_bytes(arguments: list[str]) -> bytes:
    return subprocess.run(
        arguments,
        check=True,
        capture_output=True,
        env=CLEAN_ENVIRONMENT,
    ).stdout


def scheduler_records(approved_units: set[str]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for unit_type in ("timer", "path", "socket", "automount", "service"):
        enabled = command_bytes(
            [
                "systemctl",
                "list-unit-files",
                f"--type={unit_type}",
                "--state=enabled",
                "--no-legend",
                "--no-pager",
            ]
        ).decode("utf-8")
        active = command_bytes(
            [
                "systemctl",
                "list-units",
                f"--type={unit_type}",
                "--state=active",
                "--no-legend",
                "--no-pager",
                "--plain",
            ]
        ).decode("utf-8")
        unit_names = {
            line.split()[0]
            for payload in (enabled, active)
            for line in payload.splitlines()
            if line.split()
        }
        for unit in sorted(unit_names - approved_units):
            triggers = sorted(
                command_bytes(
                    ["systemctl", "show", unit, "--property=Triggers", "--value"]
                )
                .decode("utf-8")
                .split()
            )
            trigger_units = [
                {
                    "name_sha256": sha256_bytes(value.encode("utf-8")),
                    "content_sha256": sha256_bytes(
                        command_bytes(["systemctl", "cat", value, "--no-pager"])
                    ),
                }
                for value in triggers
            ]
            records.append(
                {
                    "source_type": "systemd-activation",
                    "unit_type": unit_type,
                    "unit_name_sha256": sha256_bytes(unit.encode("utf-8")),
                    "unit_content_sha256": sha256_bytes(
                        command_bytes(["systemctl", "cat", unit, "--no-pager"])
                    ),
                    "trigger_units": trigger_units,
                }
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
            else sorted(root.rglob("*"))
            if root.is_dir()
            else []
        )
        for path in candidates:
            if path.is_symlink():
                raise ExporterError("scheduler source contains a symlink chain")
            if path.is_file():
                records.append(
                    {
                        "source_type": "scheduler-file",
                        "path_sha256": sha256_bytes(
                            path.as_posix().encode("utf-8")
                        ),
                        "content_sha256": sha256_bytes(
                            secure_bytes(path, "scheduler file")
                        ),
                    }
                )
    return records


def container_records() -> list[dict[str, Any]]:
    ids = command_bytes(
        ["docker", "container", "ls", "--all", "--quiet", "--no-trunc"]
    ).decode("ascii").split()
    if not ids:
        return []
    inspected = json.loads(
        command_bytes(["docker", "container", "inspect", *ids])
    )
    if not isinstance(inspected, list):
        raise ExporterError("Docker capability inventory is malformed")
    records = []
    for container in inspected:
        if not isinstance(container, dict):
            raise ExporterError("Docker capability entry is malformed")
        config = container.get("Config", {})
        host = container.get("HostConfig", {})
        env = config.get("Env") or []
        mounts = container.get("Mounts") or []
        if any(
            isinstance(item, str) and item.startswith(SENSITIVE_ENV_PREFIXES)
            for item in env
        ) or any(
            "pgbackrest" in str(item).lower()
            or "/run/secrets" in str(item).lower()
            or "docker.sock" in str(item).lower()
            or str(item.get("Source", "")) in {"/", "/etc", "/proc", "/sys"}
            or str(item.get("Source", "")).startswith("/var/lib/docker")
            for item in mounts
        ):
            raise ExporterError("container has unapproved repository-write capability")
        image = json.loads(
            command_bytes(
                ["docker", "image", "inspect", str(container.get("Image", ""))]
            )
        )
        if not isinstance(image, list) or len(image) != 1:
            raise ExporterError("container image identity is ambiguous")
        records.append(
            {
                "container_id_sha256": sha256_bytes(
                    str(container.get("Id", "")).encode("utf-8")
                ),
                "image_config_id": container.get("Image"),
                "repo_digests": image[0].get("RepoDigests"),
                "entrypoint": config.get("Entrypoint"),
                "cmd": config.get("Cmd"),
                "labels": config.get("Labels") or {},
                "mounts": mounts,
                "network_mode": host.get("NetworkMode"),
                "environment_keys": sorted(
                    item.split("=", 1)[0] for item in env if "=" in item
                ),
            }
        )
    return records


def canonical_executable_target(value: str) -> Path:
    if (
        not value.startswith("/")
        or value.endswith(" (deleted)")
        or value.startswith("/memfd:")
        or "\x00" in value
    ):
        raise ExporterError("writer process executable is deleted/opaque")
    executable = Path(value).resolve(strict=True)
    if not executable.is_file():
        raise ExporterError("writer process executable is not a regular file")
    return executable


def descriptor_payload(path: Path) -> bytes:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode):
            raise ExporterError("writer process executable descriptor is not regular")
        payload = bytearray()
        while chunk := os.read(descriptor, 1024 * 1024):
            payload.extend(chunk)
        return bytes(payload)
    finally:
        os.close(descriptor)


def writer_process_records(writer_uid: int) -> list[dict[str, Any]]:
    records = []
    for entry in sorted(Path("/proc").iterdir(), key=lambda path: path.name):
        if not entry.name.isdigit():
            continue
        try:
            status = (entry / "status").read_text(encoding="utf-8")
            uid_line = next(
                line for line in status.splitlines() if line.startswith("Uid:")
            )
            real_uid = int(uid_line.split()[1])
        except (FileNotFoundError, ProcessLookupError, StopIteration):
            continue
        if real_uid != writer_uid:
            continue
        try:
            executable_link = entry / "exe"
            executable = canonical_executable_target(os.readlink(executable_link))
            executable_payload = descriptor_payload(executable_link)
            argv = [
                item.decode("utf-8")
                for item in (entry / "cmdline").read_bytes().split(b"\x00")
                if item
            ]
            records.append(
                {
                    "executable_path_sha256": sha256_bytes(
                        executable.as_posix().encode("utf-8")
                    ),
                    "executable_sha256": sha256_bytes(
                        executable_payload
                    ),
                    "argv_sha256": sha256_bytes(canonical_json(argv)),
                    "uid": real_uid,
                }
            )
        except (FileNotFoundError, ProcessLookupError) as exc:
            raise ExporterError(
                "writer process identity disappeared during capability inventory"
            ) from exc
    return records


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
        capability = policy["repository_write_capability"]
        config_raw = secure_bytes(PGBACKREST_CONFIG, "pgBackRest config")
        if sha256_bytes(config_raw) != capability["config_sha256"]:
            raise ExporterError("pgBackRest capability config digest changed")
        retention_full, retention_full_type = retention_policy(config_raw)
        full_job = validate_job_artifacts(capability["full_job"], capability)
        diff_job = validate_job_artifacts(
            capability["differential_job"], capability
        )
        capability_inventory = validate_capability_inventory(
            scheduler_records=scheduler_records(
                {
                    capability["full_job"]["timer_name"],
                    capability["full_job"]["service_name"],
                    capability["differential_job"]["timer_name"],
                    capability["differential_job"]["service_name"],
                }
            ),
            container_records=container_records(),
            writer_process_records=writer_process_records(capability["uid"]),
            capability=capability,
        )
        completed = subprocess.run(
            [
                capability["pgbackrest_path"],
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
            env=CLEAN_ENVIRONMENT,
        )
        now = datetime.now(timezone.utc).replace(microsecond=0)
        identity = {
            "exporter_id": policy["exporter_id"],
            "exporter_version": policy["exporter_version"],
            "exporter_artifact_sha256": policy["artifact_sha256"],
        }
        scheduler = {
            "schema_version": 2,
            "generated_at_utc": now.strftime(TIMESTAMP),
            "full_jobs_count": 1,
            "differential_jobs_count": 1,
            "timezone": "UTC",
            "future_fulls_utc": [
                value.strftime(TIMESTAMP) for value in next_weekly_slots(now)
            ],
            "repository_write_capability_sha256": sha256_bytes(
                canonical_json(capability)
            ),
            "full_job_identity_sha256": sha256_bytes(canonical_json(full_job)),
            "differential_job_identity_sha256": sha256_bytes(
                canonical_json(diff_job)
            ),
            **capability_inventory,
            **identity,
        }
        repository = {
            "schema_version": 1,
            "generated_at_utc": now.strftime(TIMESTAMP),
            "pgbackrest_config_sha256": sha256_bytes(config_raw),
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
