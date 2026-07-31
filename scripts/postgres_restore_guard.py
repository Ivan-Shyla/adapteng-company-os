#!/usr/bin/env python3
"""Fail-closed pre-destructive guards for a restore rehearsal generation."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import socket
import stat
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from postgres_restore_image_identity import (
        IdentityError,
        canonical_json,
        measure_container,
        sha256_file,
        validate_manifest,
    )
except ModuleNotFoundError:  # pragma: no cover - package import in unit tests
    from scripts.postgres_restore_image_identity import (
        IdentityError,
        canonical_json,
        measure_container,
        sha256_file,
        validate_manifest,
    )


SHA256 = re.compile(r"^[0-9a-f]{64}$")
SAFE_NAME = re.compile(r"^[a-z0-9][a-z0-9._-]{0,62}$")
CONFIG_KEYS = {
    "schema_version",
    "purpose",
    "generation",
    "host",
    "names",
    "repository",
    "selected_set",
    "approved_image",
    "network_attestation",
    "forbidden_identifiers",
}
ARTIFACT_PATHS = {
    "scripts/postgres_restore_c_final_assert.py",
    "scripts/postgres_restore_c_final_assert.sql",
    "scripts/postgres_restore_generation.sh",
    "scripts/postgres_restore_generation.py",
    "scripts/postgres_restore_guard.py",
    "scripts/postgres_restore_host_inventory.py",
    "scripts/postgres_restore_image_identity.py",
    "scripts/postgres_restore_inventory_exporter.py",
    "scripts/postgres_restore_inventory_exporter_manifest.json",
    "scripts/postgres_restore_isolation_gate.py",
    "scripts/postgres_restore_provider_inventory.py",
    "scripts/postgres_restore_provider_manifest.json",
    "scripts/postgres_restore_retention.py",
    "scripts/postgres_restore_runner.py",
    "scripts/postgres_restore_runner_manifest.json",
    "scripts/postgres_restore_status_gate.sh",
    "scripts/postgres_restore_status_gate.py",
    "scripts/postgres_restore_transaction_probe.sh",
    "scripts/postgres_restore_transaction_probe.py",
    "scripts/postgres_restore_transaction_probe.sql",
}
KNOWN_FORBIDDEN = {"adapteng-ops-db", "postgres-adapteng-ops"}
SECRET_ENV_PREFIXES = (
    "AWS_ACCESS_KEY_ID=",
    "AWS_SECRET_ACCESS_KEY=",
    "PGBACKREST_REPO1_S3_KEY=",
    "PGBACKREST_REPO1_S3_KEY_SECRET=",
    "PGBACKREST_REPO1_CIPHER_PASS=",
)
REPOSITORY_POLICY_KEYS = {
    "endpoint",
    "bucket",
    "region",
    "repo_path",
    "restore_key_attestation_path",
    "restore_key_attestation_sha256",
    "stanza",
    "repo",
}


class GuardError(RuntimeError):
    """Fail-closed restore guard error."""


def require_keys(value: dict[str, Any], expected: set[str], label: str) -> None:
    if set(value) != expected:
        raise GuardError(f"{label} has missing or unknown fields")


def load_json_object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise GuardError(f"{label} is not valid UTF-8 JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise GuardError(f"{label} must be a JSON object")
    return value


def secure_regular_file(
    path: Path, label: str, *, restricted: bool = True
) -> None:
    try:
        info = path.lstat()
    except OSError as exc:
        raise GuardError(f"{label} is unavailable: {exc}") from exc
    if not stat.S_ISREG(info.st_mode) or path.is_symlink():
        raise GuardError(f"{label} must be a regular non-symlink file")
    if os.geteuid() != 0:
        raise GuardError("restore guard must run as root")
    if info.st_uid != 0:
        raise GuardError(f"{label} must be root-owned")
    forbidden_mode = 0o077 if restricted else 0o022
    if info.st_mode & forbidden_mode:
        requirement = "0600 or stricter" if restricted else "not group/world writable"
        raise GuardError(f"{label} must be {requirement}")


def read_secure_bytes(
    path: Path, label: str, *, restricted: bool = True
) -> bytes:
    if os.geteuid() != 0:
        raise GuardError("restore guard must run as root")
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError as exc:
        raise GuardError(f"{label} cannot be opened: {exc}") from exc
    try:
        info = os.fstat(descriptor)
        forbidden_mode = 0o077 if restricted else 0o022
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != 0
            or info.st_mode & forbidden_mode
        ):
            raise GuardError(f"{label} ownership/mode is not secure")
        chunks: list[bytes] = []
        while chunk := os.read(descriptor, 1024 * 1024):
            chunks.append(chunk)
        return b"".join(chunks)
    finally:
        os.close(descriptor)


def checked_bytes(
    path: Path, expected: str, label: str, *, restricted: bool = True
) -> bytes:
    if not SHA256.fullmatch(expected):
        raise GuardError(f"{label} expected SHA-256 is malformed")
    payload = read_secure_bytes(path, label, restricted=restricted)
    if hashlib.sha256(payload).hexdigest() != expected:
        raise GuardError(f"{label} digest mismatch")
    return payload


def checked_sha256(
    path: Path, expected: str, label: str, *, restricted: bool = True
) -> str:
    checked_bytes(path, expected, label, restricted=restricted)
    return expected


def read_trimmed(path: Path, label: str) -> str:
    value = read_secure_bytes(path, label, restricted=False).decode("utf-8").strip()
    if not value:
        raise GuardError(f"{label} is empty")
    return value


def docker_json(*args: str) -> Any:
    try:
        completed = subprocess.run(
            ["docker", *args],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            env={
                "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
                "LANG": "C",
                "LC_ALL": "C",
            },
        )
        return json.loads(completed.stdout)
    except (OSError, subprocess.CalledProcessError, json.JSONDecodeError) as exc:
        raise GuardError(f"docker {' '.join(args)} failed: {exc}") from exc


def docker_text(*args: str) -> str:
    try:
        return subprocess.run(
            ["docker", *args],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            env={
                "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
                "LANG": "C",
                "LC_ALL": "C",
            },
        ).stdout
    except (OSError, subprocess.CalledProcessError) as exc:
        raise GuardError(f"docker {' '.join(args)} failed: {exc}") from exc


def one_inspect(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, list) or len(value) != 1 or not isinstance(value[0], dict):
        raise GuardError(f"{label} must return exactly one inspect object")
    return value[0]


def verify_procedure_manifest(
    manifest_path: Path, expected_sha256: str, root: Path
) -> tuple[str, str]:
    manifest_raw = checked_bytes(
        manifest_path,
        expected_sha256,
        "procedure manifest",
        restricted=False,
    )
    try:
        manifest = json.loads(manifest_raw)
    except json.JSONDecodeError as exc:
        raise GuardError("procedure manifest is invalid JSON") from exc
    if not isinstance(manifest, dict):
        raise GuardError("procedure manifest must be an object")
    require_keys(manifest, {"schema_version", "artifacts"}, "procedure manifest")
    if manifest["schema_version"] != 1 or not isinstance(
        manifest["artifacts"], dict
    ):
        raise GuardError("procedure manifest is not v1")
    if set(manifest["artifacts"]) != ARTIFACT_PATHS:
        raise GuardError("procedure manifest artifact set is incomplete")
    for relative_path, expected in manifest["artifacts"].items():
        checked_sha256(
            root / relative_path,
            str(expected),
            relative_path,
            restricted=False,
        )
    return (
        expected_sha256,
        str(manifest["artifacts"]["scripts/postgres_restore_transaction_probe.sql"]),
    )


def validate_host(config: dict[str, Any], generation: str) -> dict[str, str]:
    require_keys(
        config,
        {"hostname", "purpose_attestation_sha256"},
        "host config",
    )
    expected_hostname = f"pg-restore-{generation.lower()}"
    if config["hostname"] != expected_hostname:
        raise GuardError("generation-specific hostname is not exact")
    actual_hostname = read_trimmed(Path("/etc/hostname"), "host name")
    if actual_hostname != expected_hostname:
        raise GuardError("actual host name does not match generation")
    if socket.gethostname().split(".", 1)[0] != expected_hostname:
        raise GuardError("kernel hostname does not match generation")

    machine_id = read_trimmed(Path("/etc/machine-id"), "machine ID")
    product_uuid = read_trimmed(
        Path("/sys/class/dmi/id/product_uuid"), "DMI product UUID"
    )
    cloud_path = Path("/run/cloud-init/instance-data.json")
    cloud_raw = read_secure_bytes(
        cloud_path, "cloud-init instance metadata", restricted=False
    )
    cloud = json.loads(cloud_raw)
    if not isinstance(cloud, dict):
        raise GuardError("cloud-init instance metadata must be an object")
    instance_id = str(cloud.get("instance_id", "")).strip()
    if not instance_id:
        raise GuardError("cloud-init instance ID is missing")

    attestation_path = Path("/etc/adapteng/postgres-restore-purpose.json")
    attestation_raw = checked_bytes(
        attestation_path,
        str(config["purpose_attestation_sha256"]),
        "host purpose attestation",
    )
    attestation = json.loads(attestation_raw)
    if not isinstance(attestation, dict):
        raise GuardError("host purpose attestation must be an object")
    require_keys(
        attestation,
        {
            "schema_version",
            "purpose",
            "generation",
            "hostname",
            "machine_id_sha256",
            "dmi_product_uuid_sha256",
            "cloud_instance_id_sha256",
        },
        "host purpose attestation",
    )
    expected = {
        "schema_version": 1,
        "purpose": "postgres-restore-rehearsal",
        "generation": generation,
        "hostname": expected_hostname,
        "machine_id_sha256": hashlib.sha256(machine_id.encode()).hexdigest(),
        "dmi_product_uuid_sha256": hashlib.sha256(product_uuid.encode()).hexdigest(),
        "cloud_instance_id_sha256": hashlib.sha256(instance_id.encode()).hexdigest(),
    }
    if attestation != expected:
        raise GuardError("host purpose/instance attestation does not match this host")
    return {
        "hostname_sha256": hashlib.sha256(actual_hostname.encode()).hexdigest(),
        "machine_id_sha256": expected["machine_id_sha256"],
        "dmi_product_uuid_sha256": expected["dmi_product_uuid_sha256"],
        "cloud_instance_id_sha256": expected["cloud_instance_id_sha256"],
        "purpose_attestation_sha256": str(config["purpose_attestation_sha256"]),
    }


def validate_selected_info(
    path: Path, expected_sha256: str, raw_set: str
) -> tuple[str, str]:
    raw = checked_bytes(path, expected_sha256, "selected-set info")
    try:
        value = json.loads(raw)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise GuardError(f"selected-set info is invalid: {exc}") from exc
    return parse_selected_info_value(value, raw_set)


def parse_selected_info_value(
    value: Any, raw_set: str
) -> tuple[str, str]:
    if not isinstance(value, list) or len(value) != 1 or not isinstance(value[0], dict):
        raise GuardError("selected-set info must contain exactly one stanza")
    stanza = value[0]
    status = stanza.get("status")
    backups = stanza.get("backup")
    if not isinstance(status, dict) or status.get("code") != 0:
        raise GuardError("selected-set repository status is not ok")
    if not isinstance(backups, list) or len(backups) != 1:
        raise GuardError("selected-set info must contain exactly one backup")
    backup = backups[0]
    if (
        not isinstance(backup, dict)
        or backup.get("label") != raw_set
        or backup.get("type") != "full"
        or backup.get("error") is not False
    ):
        raise GuardError("selected-set info does not identify the exact healthy full")
    archive = backup.get("archive")
    if not isinstance(archive, dict) or not archive.get("start") or not archive.get(
        "stop"
    ):
        raise GuardError("selected full lacks archive start/stop")
    timestamp = backup.get("timestamp")
    if not isinstance(timestamp, dict) or not isinstance(timestamp.get("stop"), int):
        raise GuardError("selected full completion timestamp is missing")
    completed = datetime.fromtimestamp(timestamp["stop"], timezone.utc)
    return (
        hashlib.sha256(raw_set.encode("utf-8")).hexdigest(),
        completed.strftime("%Y-%m-%dT%H:%M:%SZ"),
    )


def validate_repository(config: dict[str, Any]) -> dict[str, str]:
    require_keys(config, REPOSITORY_POLICY_KEYS, "repository config")
    for field in ("endpoint", "bucket", "region"):
        if not SAFE_NAME.fullmatch(str(config[field])):
            raise GuardError(f"repository {field} is malformed")
    if (
        config["stanza"] != "adapteng-ops"
        or config["repo"] != 1
        or config["repo_path"] != "/adapteng-ops"
    ):
        raise GuardError("repository stanza/repo is not exact")
    key_path = Path(str(config["restore_key_attestation_path"]))
    key_raw = checked_bytes(
        key_path,
        str(config["restore_key_attestation_sha256"]),
        "restore key attestation",
    )
    try:
        key = json.loads(key_raw)
    except json.JSONDecodeError as exc:
        raise GuardError("restore key attestation is invalid JSON") from exc
    if not isinstance(key, dict):
        raise GuardError("restore key attestation must be an object")
    require_keys(
        key,
        {
            "schema_version",
            "endpoint",
            "bucket",
            "region",
            "capabilities",
            "can_write",
            "can_delete",
        },
        "restore key attestation",
    )
    if (
        key["schema_version"] != 1
        or key["endpoint"] != config["endpoint"]
        or key["bucket"] != config["bucket"]
        or key["region"] != config["region"]
        or key["capabilities"] != ["list", "read"]
        or key["can_write"] is not False
        or key["can_delete"] is not False
    ):
        raise GuardError("restore key is not exact read/list-only repository scope")
    return {
        "endpoint": str(config["endpoint"]),
        "bucket": str(config["bucket"]),
        "region": str(config["region"]),
        "repo_path": str(config["repo_path"]),
        "restore_key_attestation_sha256": str(
            config["restore_key_attestation_sha256"]
        ),
        "stanza": str(config["stanza"]),
        "repo": "1",
        "_key_text": key_raw.decode("utf-8"),
    }


def validate_forbidden(
    config: dict[str, Any], scan_values: list[str]
) -> tuple[set[str], str]:
    require_keys(config, {"path", "sha256"}, "forbidden-identifiers config")
    path = Path(str(config["path"]))
    raw = checked_bytes(
        path, str(config["sha256"]), "forbidden-identifiers file"
    )
    identifiers = {
        line.strip()
        for line in raw.decode("utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }
    scan_forbidden_identifiers(identifiers, scan_values)
    return identifiers, str(config["sha256"])


def scan_forbidden_identifiers(
    identifiers: set[str], scan_values: list[str]
) -> None:
    if not KNOWN_FORBIDDEN.issubset(identifiers):
        raise GuardError("forbidden-identifiers file omits canonical production IDs")
    if any(len(value) < 4 for value in identifiers):
        raise GuardError("forbidden identifier is too short")
    joined = "\n".join(scan_values).lower()
    for identifier in identifiers:
        if identifier.lower() in joined:
            raise GuardError("production identifier found in restore configuration/inventory")


def validate_container(
    container: dict[str, Any],
    expected_name: str,
    expected_network: str,
    expected_volume: str,
    expected_pgdata: str,
) -> None:
    if container.get("Name") != f"/{expected_name}":
        raise GuardError("container name does not match generation")
    if container.get("State", {}).get("Running") is not False:
        raise GuardError("expected generation container is running")
    host = container.get("HostConfig", {})
    if host.get("NetworkMode") != expected_network:
        raise GuardError("container network mode is not exact")
    port_bindings = host.get("PortBindings")
    if port_bindings not in (None, {}):
        raise GuardError("container has a published port")
    networks = container.get("NetworkSettings", {}).get("Networks")
    if not isinstance(networks, dict) or set(networks) != {expected_network}:
        raise GuardError("container is attached to an unexpected network")
    ports = container.get("NetworkSettings", {}).get("Ports")
    if ports not in (None, {}):
        for bindings in ports.values():
            if bindings not in (None, []):
                raise GuardError("container exposes a host port")
    mounts = container.get("Mounts")
    if not isinstance(mounts, list):
        raise GuardError("container mount inventory is missing")
    volume_mounts = [
        item
        for item in mounts
        if item.get("Type") == "volume"
        and item.get("Name") == expected_volume
        and item.get("Destination") == expected_pgdata
        and item.get("RW") is True
    ]
    if len(volume_mounts) != 1:
        raise GuardError("container does not mount the exact generation volume/PGDATA")
    if len(mounts) != 1:
        raise GuardError("SQL container has a non-PGDATA mount")
    for item in mounts:
        source = str(item.get("Source", ""))
        destination = str(item.get("Destination", ""))
        if "docker.sock" in source or "docker.sock" in destination:
            raise GuardError("Docker socket mount is forbidden")
    env = container.get("Config", {}).get("Env", [])
    if not isinstance(env, list):
        raise GuardError("container environment inventory is missing")
    if any(
        isinstance(item, str) and item.startswith(SECRET_ENV_PREFIXES) for item in env
    ):
        raise GuardError("SQL container contains repository credentials")


def stable_image_identity(identity: dict[str, Any]) -> dict[str, Any]:
    value = {key: item for key, item in identity.items() if key != "container_ref_sha256"}
    if len(value) != len(identity) - 1:
        raise GuardError("measured image identity lacks a container binding")
    return value


def validate_volume(
    volume: dict[str, Any], expected_name: str, generation: str
) -> str:
    if (
        volume.get("Name") != expected_name
        or volume.get("Driver") != "local"
        or volume.get("Scope") != "local"
        or volume.get("Options") not in (None, {})
    ):
        raise GuardError("generation volume is not a normal local Docker volume")
    labels = volume.get("Labels")
    if labels != {
        "adapteng.restore.generation": generation,
        "adapteng.restore.new": "true",
        "adapteng.restore.purpose": "postgres-restore-rehearsal",
    }:
        raise GuardError("generation volume labels are not exact")
    mountpoint = Path(str(volume.get("Mountpoint", "")))
    if not mountpoint.is_absolute() or mountpoint.is_symlink():
        raise GuardError("volume mountpoint is not an absolute normal path")
    try:
        resolved = mountpoint.resolve(strict=True)
    except OSError as exc:
        raise GuardError(f"volume mountpoint cannot be resolved: {exc}") from exc
    expected_parent = Path("/var/lib/docker/volumes") / expected_name / "_data"
    if resolved != expected_parent:
        raise GuardError("volume mountpoint escapes the expected Docker path")
    current = resolved
    while current != current.parent:
        info = current.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise GuardError("volume path contains a symlink/reparse escape")
        current = current.parent
    if any(resolved.iterdir()):
        raise GuardError("generation volume/PGDATA is not newly empty")
    return str(resolved)


def validate_network(network: dict[str, Any], name: str, internal: bool) -> None:
    if network.get("Name") != name or network.get("Internal") is not internal:
        raise GuardError("Docker network identity/internal state is not exact")
    if network.get("Driver") != "bridge" or network.get("Scope") != "local":
        raise GuardError("Docker network must be a local bridge")
    if network.get("Options") not in (None, {}):
        raise GuardError("Docker network has unexpected options")


def validate_network_attestation(
    config: dict[str, Any], generation: str
) -> str:
    require_keys(config, {"path", "sha256"}, "network-attestation config")
    path = Path(str(config["path"]))
    raw = checked_bytes(
        path, str(config["sha256"]), "network/firewall attestation"
    )
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise GuardError("network/firewall attestation is invalid JSON") from exc
    if not isinstance(value, dict):
        raise GuardError("network/firewall attestation must be an object")
    require_keys(
        value,
        {
            "schema_version",
            "purpose",
            "generation",
            "bootstrap_firewall_export_sha256",
            "locked_firewall_export_sha256",
            "bootstrap_outbound",
            "locked_outbound",
            "observed_at_utc",
        },
        "network/firewall attestation",
    )
    if (
        value["schema_version"] != 1
        or value["purpose"] != "postgres-restore-rehearsal"
        or value["generation"] != generation
        or value["bootstrap_outbound"] != ["dns", "https"]
        or value["locked_outbound"] != "deny"
        or not SHA256.fullmatch(str(value["bootstrap_firewall_export_sha256"]))
        or not SHA256.fullmatch(str(value["locked_firewall_export_sha256"]))
    ):
        raise GuardError("network/firewall attestation is not exact")
    return str(config["sha256"])


def validate_generation_names(
    names: dict[str, Any], generation: str
) -> dict[str, str]:
    require_keys(
        names,
        {
            "recovery_container",
            "final_container",
            "volume",
            "bootstrap_network",
            "locked_network",
            "restore_pg1_path",
        },
        "names config",
    )
    suffix = generation.lower()
    expected = {
        "recovery_container": f"adapteng-recover-{suffix}",
        "final_container": f"adapteng-db-{suffix}",
        "volume": f"adapteng-restore-{suffix}",
        "bootstrap_network": "pg-restore-bootstrap",
        "locked_network": "pg-rehearsal",
        "restore_pg1_path": f"/restore/{suffix}/pgdata",
    }
    if names != expected:
        raise GuardError("generation-specific container/volume/network/PGDATA names differ")
    return expected


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--generation", choices=("A", "B", "C"), required=True)
    parser.add_argument("--guard-config", type=Path, required=True)
    parser.add_argument("--guard-config-sha256", required=True)
    parser.add_argument("--selected-set", required=True)
    parser.add_argument("--selected-info", type=Path, required=True)
    parser.add_argument("--selected-info-sha256", required=True)
    parser.add_argument("--approved-image-manifest", type=Path, required=True)
    parser.add_argument("--approved-image-manifest-sha256", required=True)
    parser.add_argument("--procedure-manifest-sha256", required=True)
    args = parser.parse_args()

    try:
        root = Path(__file__).resolve().parents[1]
        procedure_manifest = (
            Path(__file__).resolve().parent
            / "postgres_restore_procedure_manifest.json"
        )
        procedure_sha256, probe_sha256 = verify_procedure_manifest(
            procedure_manifest, args.procedure_manifest_sha256, root
        )
        config_raw = checked_bytes(
            args.guard_config, args.guard_config_sha256, "generation guard config"
        )
        try:
            config = json.loads(config_raw)
        except json.JSONDecodeError as exc:
            raise GuardError("generation guard config is invalid JSON") from exc
        if not isinstance(config, dict):
            raise GuardError("generation guard config must be an object")
        require_keys(config, CONFIG_KEYS, "generation guard config")
        if (
            config["schema_version"] != 1
            or config["purpose"] != "postgres-restore-rehearsal"
            or config["generation"] != args.generation
        ):
            raise GuardError("guard config purpose/generation is not exact")

        generation = args.generation
        names = config["names"]
        if not isinstance(names, dict):
            raise GuardError("names config must be an object")
        validate_generation_names(names, generation)

        approved_image = config["approved_image"]
        if not isinstance(approved_image, dict):
            raise GuardError("approved-image config must be an object")
        require_keys(
            approved_image, {"manifest_sha256", "platform"}, "approved-image config"
        )
        if (
            approved_image["manifest_sha256"]
            != args.approved_image_manifest_sha256
            or approved_image["platform"] not in {"linux/amd64", "linux/arm64"}
        ):
            raise GuardError("approved-image config does not match invocation")
        approved_image_manifest, _ = validate_manifest(
            args.approved_image_manifest,
            args.approved_image_manifest_sha256,
        )

        host = validate_host(config["host"], generation)
        repository = validate_repository(config["repository"])
        selected = config["selected_set"]
        if not isinstance(selected, dict):
            raise GuardError("selected-set config must be an object")
        require_keys(
            selected, {"ref_sha256", "info_sha256"}, "selected-set config"
        )
        if selected["info_sha256"] != args.selected_info_sha256:
            raise GuardError("selected-set info digest differs from guard config")
        set_ref_sha256, completed_at = validate_selected_info(
            args.selected_info, args.selected_info_sha256, args.selected_set
        )
        if selected["ref_sha256"] != set_ref_sha256:
            raise GuardError("selected backup set differs from guard config")

        recovery_identity = measure_container(
            names["recovery_container"],
            args.approved_image_manifest,
            args.approved_image_manifest_sha256,
        )
        final_identity = measure_container(
            names["final_container"],
            args.approved_image_manifest,
            args.approved_image_manifest_sha256,
        )
        recovery = one_inspect(
            docker_json("container", "inspect", names["recovery_container"]),
            "recovery container inspection",
        )
        final = one_inspect(
            docker_json("container", "inspect", names["final_container"]),
            "final container inspection",
        )
        measured_platform = (
            f"{recovery_identity['os']}/{recovery_identity['architecture']}"
        )
        if approved_image["platform"] != measured_platform:
            raise GuardError("measured image platform differs from guard config")
        if (
            recovery_identity["pgbackrest_version"] != "2.59.0"
            or not SHA256.fullmatch(
                str(recovery_identity["pgbackrest_binary_sha256"])
            )
        ):
            raise GuardError("restore image does not contain pinned pgBackRest 2.59.0")
        if recovery_identity != {
            **final_identity,
            "container_ref_sha256": recovery_identity["container_ref_sha256"],
        }:
            comparable_recovery = {
                key: value
                for key, value in recovery_identity.items()
                if key != "container_ref_sha256"
            }
            comparable_final = {
                key: value
                for key, value in final_identity.items()
                if key != "container_ref_sha256"
            }
            if comparable_recovery != comparable_final:
                raise GuardError("recovery and final containers use different images")
        expected_pgdata = str(recovery_identity["postgres_pgdata"])
        validate_container(
            recovery,
            names["recovery_container"],
            "none",
            names["volume"],
            expected_pgdata,
        )
        validate_container(
            final,
            names["final_container"],
            "none",
            names["volume"],
            expected_pgdata,
        )
        volume = one_inspect(
            docker_json("volume", "inspect", names["volume"]),
            "volume inspection",
        )
        volume_mountpoint = validate_volume(volume, names["volume"], generation)
        bootstrap = one_inspect(
            docker_json("network", "inspect", names["bootstrap_network"]),
            "bootstrap network inspection",
        )
        locked = one_inspect(
            docker_json("network", "inspect", names["locked_network"]),
            "locked network inspection",
        )
        validate_network(bootstrap, names["bootstrap_network"], False)
        validate_network(locked, names["locked_network"], True)
        network_attestation_sha256 = validate_network_attestation(
            config["network_attestation"], generation
        )

        container_inventory = docker_text("ps", "-a", "--no-trunc", "--format", "{{.Names}}")
        image_inventory = docker_text(
            "image",
            "ls",
            "--no-trunc",
            "--digests",
            "--format",
            "{{.ID}} {{.Repository}}@{{.Digest}}",
        )
        volume_inventory = docker_text("volume", "ls", "--format", "{{.Name}}")
        network_inventory = docker_text("network", "ls", "--format", "{{.Name}}")
        if set(container_inventory.split()) != {
            names["recovery_container"],
            names["final_container"],
        }:
            raise GuardError("scratch host contains an unexpected container/application")
        if set(volume_inventory.split()) != {names["volume"]}:
            raise GuardError("scratch host contains an unexpected Docker volume")
        if set(network_inventory.split()) != {
            "bridge",
            "host",
            "none",
            names["bootstrap_network"],
            names["locked_network"],
        }:
            raise GuardError("scratch host contains an unexpected Docker network")

        scan_values = [
            json.dumps(config, sort_keys=True),
            json.dumps(recovery, sort_keys=True),
            json.dumps(final, sort_keys=True),
            json.dumps(volume, sort_keys=True),
            json.dumps(bootstrap, sort_keys=True),
            json.dumps(locked, sort_keys=True),
            container_inventory,
            image_inventory,
            volume_inventory,
            network_inventory,
            repository["_key_text"],
            checked_bytes(
                args.selected_info,
                args.selected_info_sha256,
                "selected-set info",
            ).decode("utf-8"),
            checked_bytes(
                args.approved_image_manifest,
                args.approved_image_manifest_sha256,
                "approved image manifest",
            ).decode("utf-8"),
            checked_bytes(
                Path(config["network_attestation"]["path"]),
                str(config["network_attestation"]["sha256"]),
                "network/firewall attestation",
            ).decode("utf-8"),
            checked_bytes(
                Path("/etc/adapteng/postgres-restore-purpose.json"),
                str(config["host"]["purpose_attestation_sha256"]),
                "host purpose attestation",
            ).decode("utf-8"),
        ]
        _, forbidden_sha256 = validate_forbidden(
            config["forbidden_identifiers"], scan_values
        )

        inventory = {
            "schema_version": 1,
            "status": "GUARDED",
            "purpose": "postgres-restore-rehearsal",
            "generation": generation,
            "containers_exact": True,
            "containers_stopped": True,
            "volume_exact_new_empty": True,
            "volume_mountpoint_sha256": hashlib.sha256(
                volume_mountpoint.encode()
            ).hexdigest(),
            "bootstrap_network_exact": True,
            "locked_internal_network_exact": True,
            "published_db_ports": 0,
            "docker_socket_mounts": 0,
            "unexpected_containers": 0,
            "image_inventory_sha256": hashlib.sha256(
                image_inventory.encode()
            ).hexdigest(),
            "unexpected_volumes": 0,
            "unexpected_networks": 0,
            "production_identifiers_found": 0,
            "host_identity": host,
            "network_attestation_sha256": network_attestation_sha256,
            "forbidden_identifiers_sha256": forbidden_sha256,
            "approved_image_identity_sha256": hashlib.sha256(
                canonical_json(stable_image_identity(recovery_identity))
            ).hexdigest(),
            "recovery_container_ref_sha256": recovery_identity[
                "container_ref_sha256"
            ],
            "final_container_ref_sha256": final_identity["container_ref_sha256"],
        }
        inventory_sha256 = hashlib.sha256(canonical_json(inventory)).hexdigest()
        packet = {
            "schema_version": 1,
            "status": "RESTORE_GUARDS_PASSED",
            "generation": generation,
            "procedure_manifest_sha256": procedure_sha256,
            "transaction_probe_sha256": probe_sha256,
            "guard_config_sha256": args.guard_config_sha256,
            "selected_set_ref_sha256": set_ref_sha256,
            "selected_set_info_sha256": args.selected_info_sha256,
            "completed_at": completed_at,
            "inventory_sha256": inventory_sha256,
            "approved_image_manifest_sha256": args.approved_image_manifest_sha256,
            "measured_image_identity_sha256": inventory[
                "approved_image_identity_sha256"
            ],
            "cloud_instance_id_sha256": host["cloud_instance_id_sha256"],
            "image_config_id": recovery_identity["image_config_id"],
            "image_repo_digest": recovery_identity["repo_digest"],
            "image_environment_sha256": hashlib.sha256(
                canonical_json(approved_image_manifest["image_environment"])
            ).hexdigest(),
            "recovery_container": names["recovery_container"],
            "final_container": names["final_container"],
            "volume": names["volume"],
            "bootstrap_network": names["bootstrap_network"],
            "locked_network": names["locked_network"],
            "restore_pg1_path": names["restore_pg1_path"],
            "database_pgdata": expected_pgdata,
            "repository_endpoint": repository["endpoint"],
            "repository_bucket": repository["bucket"],
            "repository_region": repository["region"],
            "repository_path": repository["repo_path"],
            "restore_key_attestation_sha256": repository[
                "restore_key_attestation_sha256"
            ],
            "stanza": repository["stanza"],
            "repo": repository["repo"],
        }
        packet_bytes = canonical_json(packet)
        sys.stdout.buffer.write(packet_bytes)
        return 0
    except (
        OSError,
        UnicodeError,
        json.JSONDecodeError,
        GuardError,
        IdentityError,
    ) as exc:
        print(f"STOP: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
