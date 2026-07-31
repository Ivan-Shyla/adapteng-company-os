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
        "host_scope",
        "repository_write_capability",
    }
    if (
        not isinstance(policy, dict)
        or set(policy) != required
        or policy.get("schema_version") != 3
    ):
        raise ExporterError("inventory exporter manifest is not exact v3")
    if policy.get("status") != "APPROVED":
        raise ExporterError("inventory exporter manifest is NOT_CONFIGURED")
    if (
        not SHA256.fullmatch(str(policy.get("artifact_sha256")))
        or sha256_bytes(Path(__file__).read_bytes()) != policy["artifact_sha256"]
        or policy.get("scheduler_output_schema_version") != 3
        or policy.get("repository_output_schema_version") != 1
    ):
        raise ExporterError("inventory exporter artifact/schema identity is not exact")
    validate_capability_policy(policy["repository_write_capability"])
    validate_host_scope_policy(policy["host_scope"])
    return policy, sha256_bytes(raw)


def validate_capability_policy(capability: Any) -> None:
    required = {
        "principal",
        "uid",
        "credential_id",
        "credential_path",
        "docker_gid",
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
        or capability["credential_path"]
        != "/run/secrets/pgbackrest-repository-write"
        or not isinstance(capability["docker_gid"], int)
        or capability["docker_gid"] <= 0
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


def validate_host_scope_policy(scope: Any) -> None:
    required = {
        "kind",
        "machine_id_sha256",
        "require_no_user_managers",
        "require_no_linger",
        "root_admin_trust",
        "docker_admin_allowed",
    }
    if (
        not isinstance(scope, dict)
        or set(scope) != required
        or scope["kind"] != "DEDICATED_POSTGRES_BACKUP_HOST"
        or not SHA256.fullmatch(str(scope["machine_id_sha256"]))
        or scope["require_no_user_managers"] is not True
        or scope["require_no_linger"] is not True
        or scope["root_admin_trust"]
        != "ALL_ROOT_PROCESSES_EXACTLY_INVENTORIED"
        or scope["docker_admin_allowed"] is not False
    ):
        raise ExporterError("dedicated host scope policy is not exact")


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


def writer_process_identity(record: dict[str, Any]) -> dict[str, Any]:
    runtime_only = {
        "mountinfo_sha256",
        "open_fd_target_sha256s",
        "task_security_inventory_sha256",
        "task_count",
    }
    if runtime_only.issubset(record):
        return {key: value for key, value in record.items() if key not in runtime_only}
    return record


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
            record_sha256(writer_process_identity(value))
            for value in writer_process_records
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
        "capability_inventory_sha256": sha256_bytes(
            canonical_json(
                {
                    "classified_identities": actual,
                    "runtime_writer_process_inventory_sha256": sha256_bytes(
                        canonical_json(writer_process_records)
                    ),
                }
            )
        ),
        "runtime_writer_process_inventory_sha256": sha256_bytes(
            canonical_json(writer_process_records)
        ),
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


def scheduler_file_record(path: Path) -> dict[str, Any]:
    if path.is_symlink() or path.resolve(strict=True) != path:
        raise ExporterError("scheduler source contains a symlink chain")
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o022:
            raise ExporterError("scheduler file ownership/mode permits replacement")
        payload = bytearray()
        while chunk := os.read(descriptor, 65536):
            payload.extend(chunk)
    finally:
        os.close(descriptor)
    return {
        "source_type": "scheduler-file",
        "path_sha256": sha256_bytes(path.as_posix().encode("utf-8")),
        "owner_uid": info.st_uid,
        "mode": stat.S_IMODE(info.st_mode),
        "content_sha256": sha256_bytes(payload),
    }


def user_unit_roots(
    account_homes: set[Path], run_user: Path = Path("/run/user")
) -> tuple[Path, ...]:
    return tuple(
        sorted(
            {home / ".config/systemd/user" for home in account_homes}
            | {home / ".config/systemd/user-generators" for home in account_homes}
            | {
                home / ".config/systemd/user-environment-generators"
                for home in account_homes
            }
            | {home / ".local/share/systemd/user" for home in account_homes}
            | {
                home / ".local/share/systemd/user-generators"
                for home in account_homes
            }
            | set(run_user.glob("*/systemd/user"))
            | set(run_user.glob("*/systemd/generator*"))
        )
    )


def host_scope_state(scope: dict[str, Any], capability: dict[str, Any]) -> dict[str, Any]:
    machine_id = secure_bytes(Path("/etc/machine-id"), "machine ID").strip()
    if sha256_bytes(machine_id) != scope["machine_id_sha256"]:
        raise ExporterError("inventory is not running on the approved dedicated host")
    linger = Path("/var/lib/systemd/linger")
    linger_accounts = (
        sorted(path.name for path in linger.iterdir()) if linger.is_dir() else []
    )
    run_user = Path("/run/user")
    user_managers = (
        sorted(
            path.name
            for path in run_user.iterdir()
            if path.is_dir() and path.name.isdecimal()
        )
        if run_user.is_dir()
        else []
    )
    if linger_accounts or user_managers:
        raise ExporterError("user systemd managers/linger are unsupported and not absent")
    if any(
        path.exists()
        for path in (
            Path("/data/coolify"),
            Path("/var/lib/coolify"),
            Path("/etc/coolify"),
        )
    ):
        raise ExporterError(
            "shared Coolify scheduler scope is unsupported; use the dedicated host contract"
        )
    credential = Path(capability["credential_path"])
    if credential.is_symlink():
        raise ExporterError("repository-write credential path is a symlink")
    info = credential.stat()
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_uid != capability["uid"]
        or stat.S_IMODE(info.st_mode) != 0o400
    ):
        raise ExporterError("repository-write credential is not exclusive to its principal")
    return {
        "host_scope_identity_sha256": sha256_bytes(canonical_json(scope)),
        "machine_id_sha256": scope["machine_id_sha256"],
        "user_systemd_managers_count": 0,
        "linger_accounts_count": 0,
        "credential_metadata_sha256": sha256_bytes(
            canonical_json(
                {
                    "path_sha256": sha256_bytes(
                        capability["credential_path"].encode("utf-8")
                    ),
                    "uid": info.st_uid,
                    "mode": stat.S_IMODE(info.st_mode),
                }
            )
        ),
    }


def scheduler_records(
    approved_units: set[str], *, account_homes: set[Path] | None = None
) -> list[dict[str, Any]]:
    if account_homes is None:
        try:
            import pwd
        except ImportError as exc:
            raise ExporterError("account-database inventory is unavailable") from exc
        account_homes = {
            Path(account.pw_dir)
            for account in pwd.getpwall()
            if account.pw_dir.startswith("/")
        }
    records: list[dict[str, Any]] = []
    unit_files = command_bytes(
        ["systemctl", "list-unit-files", "--all", "--no-legend", "--no-pager"]
    ).decode("utf-8")
    loaded_units = command_bytes(
        ["systemctl", "list-units", "--all", "--no-legend", "--no-pager", "--plain"]
    ).decode("utf-8")
    unit_names = {
        line.split()[0]
        for payload in (unit_files, loaded_units)
        for line in payload.splitlines()
        if line.split() and "." in line.split()[0]
    }
    properties = (
        "Id",
        "LoadState",
        "ActiveState",
        "UnitFileState",
        "FragmentPath",
        "SourcePath",
        "DropInPaths",
        "Transient",
        "Triggers",
        "TriggeredBy",
        "ExecStart",
    )
    for unit in sorted(unit_names - approved_units):
        effective = command_bytes(
            [
                "systemctl",
                "show",
                unit,
                *[f"--property={name}" for name in properties],
            ]
        )
        records.append(
            {
                "source_type": "systemd-system-unit",
                "unit_name_sha256": sha256_bytes(unit.encode("utf-8")),
                "effective_properties_sha256": sha256_bytes(effective),
            }
        )
    account_user_unit_roots = user_unit_roots(account_homes)
    for root in (
        Path("/etc/crontab"),
        Path("/etc/anacrontab"),
        Path("/etc/cron.d"),
        Path("/etc/cron.hourly"),
        Path("/etc/cron.daily"),
        Path("/etc/cron.weekly"),
        Path("/etc/cron.monthly"),
        Path("/etc/cron.allow"),
        Path("/etc/cron.deny"),
        Path("/etc/at.allow"),
        Path("/etc/at.deny"),
        Path("/var/spool/cron/crontabs"),
        Path("/var/spool/cron"),
        Path("/var/spool/cron/atjobs"),
        Path("/var/spool/anacron"),
        Path("/var/spool/at"),
        Path("/var/spool/atjobs"),
        Path("/etc/systemd/user"),
        Path("/etc/xdg/systemd/user"),
        Path("/run/systemd/user"),
        Path("/usr/lib/systemd/user"),
        Path("/usr/share/systemd/user"),
        Path("/usr/local/lib/systemd/user"),
        Path("/usr/local/share/systemd/user"),
        Path("/run/systemd/user-generators"),
        Path("/etc/systemd/user-generators"),
        Path("/usr/local/lib/systemd/user-generators"),
        Path("/usr/lib/systemd/user-generators"),
        Path("/run/systemd/user-environment-generators"),
        Path("/etc/systemd/user-environment-generators"),
        Path("/usr/local/lib/systemd/user-environment-generators"),
        Path("/usr/lib/systemd/user-environment-generators"),
        *account_user_unit_roots,
    ):
        candidates = (
            [root]
            if root.is_file()
            else sorted(root.rglob("*"))
            if root.is_dir()
            else []
        )
        for path in candidates:
            if path.is_file():
                records.append(scheduler_file_record(path))
    return records


def container_capability_record(
    container: dict[str, Any], image: dict[str, Any]
) -> dict[str, Any]:
    config = container.get("Config", {})
    host = container.get("HostConfig", {})
    state = container.get("State", {})
    network_settings = container.get("NetworkSettings", {})
    env = config.get("Env") or []
    mounts = container.get("Mounts") or []
    if (
        not isinstance(config, dict)
        or not isinstance(host, dict)
        or not isinstance(state, dict)
        or not isinstance(network_settings, dict)
        or not isinstance(env, list)
        or not isinstance(mounts, list)
        or not all(isinstance(item, str) and "=" in item for item in env)
        or not all(isinstance(item, dict) for item in mounts)
        or not isinstance(network_settings.get("Networks"), dict)
    ):
        raise ExporterError("Docker capability entry shape is malformed")
    if any(
        isinstance(item, str) and item.startswith(SENSITIVE_ENV_PREFIXES)
        for item in env
    ) or any(
        "pgbackrest" in str(item).lower()
        or "/run/secrets" in str(item).lower()
        or "docker.sock" in str(item).lower()
        or item.get("Type") == "bind"
        for item in mounts
    ):
        raise ExporterError("container has unapproved repository-write capability")
    if (
        host.get("Privileged") is not False
        or host.get("CapAdd") not in (None, [])
        or host.get("Devices") not in (None, [])
        or host.get("DeviceRequests") not in (None, [])
        or host.get("PidMode") not in (None, "")
        or host.get("IpcMode") not in (None, "private")
        or host.get("UTSMode") not in (None, "")
        or host.get("CgroupnsMode") not in (None, "", "private")
        or host.get("UsernsMode") not in (None, "")
    ):
        raise ExporterError("container has unapproved host-admin capability")
    return {
        "container_id_sha256": sha256_bytes(
            str(container.get("Id", "")).encode("utf-8")
        ),
        "image_config_id": container.get("Image"),
        "config_image": config.get("Image"),
        "repo_digests": image.get("RepoDigests"),
        "user": config.get("User"),
        "working_dir": config.get("WorkingDir"),
        "hostname": config.get("Hostname"),
        "entrypoint": config.get("Entrypoint"),
        "cmd": config.get("Cmd"),
        "labels": config.get("Labels") or {},
        "mounts": mounts,
        "environment_keys": sorted(
            item.split("=", 1)[0] for item in env if isinstance(item, str) and "=" in item
        ),
        "state": {
            "status": state.get("Status"),
            "running": state.get("Running"),
        },
        "host_security": {
            key: host.get(key)
            for key in (
                "Privileged",
                "CapAdd",
                "CapDrop",
                "SecurityOpt",
                "Devices",
                "DeviceRequests",
                "ReadonlyRootfs",
                "Runtime",
                "PidMode",
                "IpcMode",
                "UTSMode",
                "CgroupnsMode",
                "UsernsMode",
                "GroupAdd",
                "CgroupParent",
                "Binds",
                "NetworkMode",
                "RestartPolicy",
            )
        },
        "networks": network_settings["Networks"],
    }


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
        image = json.loads(
            command_bytes(
                ["docker", "image", "inspect", str(container.get("Image", ""))]
            )
        )
        if not isinstance(image, list) or len(image) != 1:
            raise ExporterError("container image identity is ambiguous")
        records.append(container_capability_record(container, image[0]))
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


def process_environment_keys(path: Path) -> list[str]:
    entries = path.read_bytes().split(b"\x00")
    keys: list[str] = []
    for item in entries:
        if not item:
            continue
        if b"=" not in item:
            raise ExporterError("process environment entry is malformed")
        key = item.split(b"=", 1)[0].decode("ascii")
        keys.append(key)
    if len(keys) != len(set(keys)):
        raise ExporterError("process environment contains duplicate keys")
    return sorted(keys)


def process_fd_targets(path: Path) -> tuple[list[str], list[str]]:
    raw_targets: list[str] = []
    for descriptor in sorted(path.iterdir(), key=lambda item: item.name):
        try:
            raw_targets.append(os.readlink(descriptor))
        except FileNotFoundError:
            continue
    return raw_targets, sorted(
        sha256_bytes(value.encode("utf-8")) for value in raw_targets
    )


def process_security_state(
    status: str, *, writer_uid: int, docker_gid: int
) -> dict[str, Any]:
    def values(name: str) -> list[int]:
        line = next(
            item for item in status.splitlines() if item.startswith(f"{name}:")
        )
        return [int(value) for value in line.split()[1:]]

    uids = values("Uid")
    gids = values("Gid") + values("Groups")
    capability_sets = {
        name: int(
            next(
                line
                for line in status.splitlines()
                if line.startswith(f"{name}:")
            ).split()[1],
            16,
        )
        for name in ("CapInh", "CapPrm", "CapEff", "CapAmb")
    }
    return {
        "uids": uids,
        "gids": sorted(set(gids)),
        "capability_sets": capability_sets,
        "root_or_writer": 0 in uids or writer_uid in uids,
        "docker_admin": docker_gid in gids,
        "privileged_capability": any(capability_sets.values()),
    }


def aggregate_task_security(
    task_states: list[tuple[str, dict[str, Any]]],
) -> dict[str, Any]:
    if not task_states:
        raise ExporterError("process has no non-kernel task security inventory")
    shapes = sorted(
        {
            canonical_json(
                {
                    "uids": security["uids"],
                    "gids": security["gids"],
                    "capability_sets": {
                        key: f"{value:x}"
                        for key, value in sorted(
                            security["capability_sets"].items()
                        )
                    },
                }
            ).decode("ascii").strip()
            for _, security in task_states
        }
    )
    runtime = [
        {
            "task_ref_sha256": sha256_bytes(task_id.encode("ascii")),
            "security_shape_sha256": sha256_bytes(
                canonical_json(
                    {
                        "uids": security["uids"],
                        "gids": security["gids"],
                        "capability_sets": security["capability_sets"],
                    }
                )
            ),
        }
        for task_id, security in sorted(task_states)
    ]
    return {
        "security_shapes": shapes,
        "task_count": len(task_states),
        "task_security_inventory_sha256": sha256_bytes(canonical_json(runtime)),
        "root_or_writer": any(
            security["root_or_writer"] for _, security in task_states
        ),
        "docker_admin": any(
            security["docker_admin"] for _, security in task_states
        ),
        "privileged_capability": any(
            security["privileged_capability"] for _, security in task_states
        ),
    }


def writer_process_records(capability: dict[str, Any]) -> list[dict[str, Any]]:
    records = []
    for entry in sorted(Path("/proc").iterdir(), key=lambda path: path.name):
        if not entry.name.isdigit():
            continue
        try:
            task_paths = sorted(
                (entry / "task").iterdir(), key=lambda path: path.name
            )
        except (FileNotFoundError, ProcessLookupError):
            continue
        try:
            task_states: list[tuple[str, dict[str, Any]]] = []
            for task in task_paths:
                status = (task / "status").read_text(encoding="utf-8")
                if "Kthread:\t1" in status:
                    continue
                task_states.append(
                    (
                        task.name,
                        process_security_state(
                            status,
                            writer_uid=capability["uid"],
                            docker_gid=capability["docker_gid"],
                        ),
                    )
                )
            if not task_states:
                continue
            security = aggregate_task_security(task_states)
            environment_keys = process_environment_keys(entry / "environ")
            fd_targets, fd_target_hashes = process_fd_targets(entry / "fd")
            mountinfo = (entry / "mountinfo").read_bytes()
            cgroup = (entry / "cgroup").read_bytes()
            capability_paths = {
                capability["credential_path"],
                capability["config_path"],
                "/var/run/docker.sock",
                "/run/docker.sock",
            }
            sensitive_environment = any(
                key.startswith(SENSITIVE_ENV_PREFIXES)
                or key in {"PGSERVICE", "PGPASSFILE"}
                for key in environment_keys
            )
            path_capability = any(
                any(target.startswith(path) for path in capability_paths)
                for target in fd_targets
            ) or any(
                path.encode("utf-8") in mountinfo for path in capability_paths
            )
            root_or_writer = security["root_or_writer"]
            docker_admin = security["docker_admin"]
            privileged_capability = security["privileged_capability"]
            if not (
                root_or_writer
                or docker_admin
                or privileged_capability
                or sensitive_environment
                or path_capability
            ):
                continue
            executable_link = entry / "exe"
            executable = canonical_executable_target(os.readlink(executable_link))
            executable_payload = descriptor_payload(executable_link)
            argv = [
                item.decode("utf-8")
                for item in (entry / "cmdline").read_bytes().split(b"\x00")
                if item
            ]
            if executable.as_posix() in SHELL_EXECUTABLES:
                raise ExporterError(
                    "write-capable generic shell process is not permitted"
                )
            records.append(
                {
                    "executable_path_sha256": sha256_bytes(
                        executable.as_posix().encode("utf-8")
                    ),
                    "executable_sha256": sha256_bytes(
                        executable_payload
                    ),
                    "argv_sha256": sha256_bytes(canonical_json(argv)),
                    "task_security_shapes": security["security_shapes"],
                    "task_count": security["task_count"],
                    "task_security_inventory_sha256": security[
                        "task_security_inventory_sha256"
                    ],
                    "cgroup_sha256": sha256_bytes(cgroup),
                    "environment_keys": environment_keys,
                    "mountinfo_sha256": sha256_bytes(mountinfo),
                    "open_fd_target_sha256s": fd_target_hashes,
                    "capability_reasons": {
                        "root_or_writer_uid": root_or_writer,
                        "docker_admin_group": docker_admin,
                        "privileged_kernel_capability": privileged_capability,
                        "sensitive_environment_keys": sensitive_environment,
                        "credential_or_admin_path": path_capability,
                    },
                }
            )
        except (
            FileNotFoundError,
            ProcessLookupError,
            StopIteration,
            UnicodeError,
            ValueError,
        ) as exc:
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
        host_scope = host_scope_state(policy["host_scope"], capability)
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
            writer_process_records=writer_process_records(capability),
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
            "schema_version": 3,
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
            **host_scope,
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
