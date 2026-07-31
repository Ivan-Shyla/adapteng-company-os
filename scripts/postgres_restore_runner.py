#!/usr/bin/env python3
"""Create, measure and execute one sealed SQL runner container by exact ID."""

from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import os
import re
import stat
import subprocess
import sys
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCRIPT_DIR = Path(__file__).resolve().parent
MANIFEST_PATH = SCRIPT_DIR / "postgres_restore_runner_manifest.json"
PROCEDURE_MANIFEST = SCRIPT_DIR / "postgres_restore_procedure_manifest.json"
DATABASE_SECRET = Path("/run/secrets/postgres-restore-runner.json")
STATE_ROOT = Path("/var/lib/adapteng/postgres-restore-rehearsal")
DIGEST = re.compile(r"^[a-z0-9][a-z0-9._/-]*@sha256:[0-9a-f]{64}$")
CONFIG_ID = re.compile(r"^sha256:[0-9a-f]{64}$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")
SECRET_PATTERN = re.compile(r"^[A-Za-z0-9_./+=:@%-]{32,256}$")
CLEAN_ENVIRONMENT = {
    "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
    "LANG": "C",
    "LC_ALL": "C",
}
MANIFEST_KEYS = {
    "schema_version",
    "status",
    "repo_digest",
    "config_id",
    "os",
    "architecture",
    "image_environment",
    "image_labels",
    "runtime",
    "apparmor_profile",
    "masked_paths",
    "readonly_paths",
    "readonly_rootfs",
    "tmpfs",
    "execution_gate_entrypoint",
    "psql_entrypoint",
    "probe_argv",
    "database_environment",
    "migration_entrypoint",
    "collector_entrypoint",
    "bootstrap_entrypoint",
    "bootstrap_argv",
    "migration_commands",
    "collector_commands",
    "target",
}
RUNNER_LABELS = {
    "adapteng.restore.purpose": "postgres-restore-rehearsal",
    "adapteng.restore.component": "sealed-sql-runner",
}


class RunnerError(RuntimeError):
    """Fail-closed runner identity or command error."""


HostInventoryError = RunnerError


def canonical_json(value: Any) -> bytes:
    return (
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        .encode("ascii")
        + b"\n"
    )


def load_sealed_dependencies() -> None:
    prefix = "" if __package__ in (None, "") else "scripts."
    host = importlib.import_module(f"{prefix}postgres_restore_host_inventory")
    isolation = importlib.import_module(f"{prefix}postgres_restore_isolation_gate")
    globals().update(
        {
            "HostInventoryError": host.HostInventoryError,
            "collect_docker_inventory": host.collect_docker_inventory,
            "container_execution_identity": host.container_execution_identity,
            "validate_sealed_target": host.validate_sealed_target,
            "validate_host_inventory": host.validate_host_inventory,
            "collect_provider_operation": isolation.collect_provider_operation,
        }
    )


def secure_member_bytes(path: Path, label: str, *, private: bool = False) -> bytes:
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        info = os.fstat(descriptor)
        forbidden_mode = 0o077 if private else 0o022
        if (
            os.geteuid() != 0
            or info.st_uid != 0
            or not stat.S_ISREG(info.st_mode)
            or info.st_mode & forbidden_mode
        ):
            raise RunnerError(f"{label} ownership/mode is not secure")
        payload = bytearray()
        while chunk := os.read(descriptor, 65536):
            payload.extend(chunk)
        return bytes(payload)
    finally:
        os.close(descriptor)


def strict_json_object(payload: bytes, label: str) -> dict[str, Any]:
    def pairs(values: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in values:
            if key in result:
                raise RunnerError(f"{label} contains a duplicate key")
            result[key] = value
        return result

    try:
        value = json.loads(payload, object_pairs_hook=pairs)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise RunnerError(f"{label} is invalid JSON") from exc
    if not isinstance(value, dict):
        raise RunnerError(f"{label} must be a JSON object")
    return value


def verify_procedure(expected_sha256: str) -> dict[str, Any]:
    if not SHA256.fullmatch(expected_sha256):
        raise RunnerError("procedure manifest SHA-256 is malformed")
    raw = secure_member_bytes(PROCEDURE_MANIFEST, "procedure manifest")
    if hashlib.sha256(raw).hexdigest() != expected_sha256:
        raise RunnerError("procedure manifest digest mismatch")
    value = json.loads(raw)
    artifacts = value.get("artifacts") if isinstance(value, dict) else None
    if not isinstance(artifacts, dict):
        raise RunnerError("procedure manifest artifacts are missing")
    root = SCRIPT_DIR.parent
    for member, expected in artifacts.items():
        if (
            not isinstance(member, str)
            or not member.startswith("scripts/")
            or Path(member).is_absolute()
            or ".." in Path(member).parts
            or not isinstance(expected, str)
            or not SHA256.fullmatch(expected)
            or hashlib.sha256(
                secure_member_bytes(root / member, member)
            ).hexdigest()
            != expected
        ):
            raise RunnerError(f"{member} is not the sealed procedure member")
    return value


def load_manifest() -> tuple[dict[str, Any], str]:
    raw = secure_member_bytes(MANIFEST_PATH, "runner manifest")
    manifest = json.loads(raw)
    if (
        not isinstance(manifest, dict)
        or set(manifest) != MANIFEST_KEYS
        or manifest.get("schema_version") != 3
    ):
        raise RunnerError("runner manifest is not exact v3")
    if manifest.get("status") != "APPROVED":
        raise RunnerError("runner manifest is NOT_CONFIGURED")
    for field in ("repo_digest",):
        if not DIGEST.fullmatch(str(manifest.get(field))):
            raise RunnerError(f"runner {field} is not approved")
    if not CONFIG_ID.fullmatch(str(manifest.get("config_id"))):
        raise RunnerError("runner config ID is not approved")
    if manifest.get("os") != "linux" or manifest.get("architecture") not in {
        "amd64",
        "arm64",
    }:
        raise RunnerError("runner platform is not approved")
    if (
        not isinstance(manifest.get("image_environment"), list)
        or not all(isinstance(item, str) and "=" in item for item in manifest["image_environment"])
        or not isinstance(manifest.get("image_labels"), dict)
        or manifest.get("runtime") != "runc"
        or not isinstance(manifest.get("apparmor_profile"), str)
        or not manifest["apparmor_profile"]
        or not isinstance(manifest.get("masked_paths"), list)
        or not isinstance(manifest.get("readonly_paths"), list)
        or manifest.get("readonly_rootfs") is not True
        or manifest.get("tmpfs") is not None
        or manifest.get("execution_gate_entrypoint")
        != "/usr/local/libexec/adapteng-postgres-restore-exec-gate"
    ):
        raise RunnerError("runner image environment/labels are not pinned")
    for environment in (
        manifest["image_environment"],
        manifest["target"]["image_environment"]
        if isinstance(manifest.get("target"), dict)
        else [],
    ):
        keys = {item.split("=", 1)[0] for item in environment if "=" in item}
        if any(
            key.startswith(("PGBACKREST_", "AWS_", "B2_"))
            or key in {"PGHOST", "PGPORT", "PGDATABASE", "PGUSER", "PGPASSWORD", "PGOPTIONS"}
            for key in keys
        ):
            raise RunnerError("approved image environment contains an override")
    target = manifest.get("target")
    if not isinstance(target, dict) or set(target) != {
        "repo_digest",
        "config_id",
        "path",
        "entrypoint",
        "cmd",
        "user",
        "working_dir",
        "image_environment",
        "labels",
        "hostname_template",
        "runtime",
        "apparmor_profile",
        "masked_paths",
        "readonly_paths",
        "readonly_rootfs",
        "tmpfs",
    }:
        raise RunnerError("target identity policy is not exact")
    if (
        not DIGEST.fullmatch(str(target["repo_digest"]))
        or not CONFIG_ID.fullmatch(str(target["config_id"]))
        or not isinstance(target["entrypoint"], list)
        or not isinstance(target["cmd"], list)
        or not isinstance(target["path"], str)
        or not target["path"].startswith("/")
        or not isinstance(target["user"], str)
        or not target["user"]
        or not isinstance(target["working_dir"], str)
        or not isinstance(target["image_environment"], list)
        or not isinstance(target["labels"], dict)
        or target["hostname_template"] != "{target_name}"
        or target["runtime"] != "runc"
        or not isinstance(target["apparmor_profile"], str)
        or not target["apparmor_profile"]
        or not isinstance(target["masked_paths"], list)
        or not isinstance(target["readonly_paths"], list)
        or target["readonly_rootfs"] is not True
        or not isinstance(target["tmpfs"], dict)
        or set(target["tmpfs"]) != {"/tmp", "/var/run/postgresql"}
    ):
        raise RunnerError("target image/command identity is not approved")
    return manifest, hashlib.sha256(raw).hexdigest()


def parse_database_secret(
    payload: bytes,
    manifest: dict[str, Any],
    generation: str,
    mode: str,
    target_kind: str = "final",
) -> tuple[dict[str, str], str]:
    value = strict_json_object(payload, "database secret capability")
    required = {
        "schema_version",
        "generation",
        "runner_password",
        "admin_password",
    }
    if set(value) != required:
        raise RunnerError("database secret capability fields are not exact")
    if value["schema_version"] != 1 or value["generation"] != generation:
        raise RunnerError("database secret capability generation is not exact")
    for field in ("runner_password", "admin_password"):
        if not isinstance(value[field], str) or not SECRET_PATTERN.fullmatch(value[field]):
            raise RunnerError("database secret capability value is malformed")
    policy = manifest.get("database_environment")
    if not isinstance(policy, dict) or set(policy) != {
        "runner_fixed",
        "bootstrap_fixed",
        "host_template",
    }:
        raise RunnerError("database environment policy is not exact")
    bootstrap = mode in {"assert-recovery", "bootstrap-role", "drop-role"}
    fixed = policy["bootstrap_fixed" if bootstrap else "runner_fixed"]
    if not isinstance(fixed, dict):
        raise RunnerError("database environment fixed map is malformed")
    values = {
        **{str(key): str(item) for key, item in fixed.items()},
        "PGHOST": (
            f"adapteng-recover-{generation.lower()}"
            if target_kind == "recovery"
            else policy["host_template"].format(generation_lower=generation.lower())
        ),
        "PGPASSWORD": value["admin_password" if bootstrap else "runner_password"],
    }
    if any(
        not key
        or "=" in key
        or any(character in item for character in "\x00\r\n")
        for key, item in values.items()
    ):
        raise RunnerError("database environment contains a malformed entry")
    public = {key: item for key, item in values.items() if key != "PGPASSWORD"}
    return values, hashlib.sha256(canonical_json(public)).hexdigest()


def read_database_secret(
    manifest: dict[str, Any], generation: str, mode: str, target_kind: str
) -> tuple[dict[str, str], str]:
    payload = secure_member_bytes(DATABASE_SECRET, "database secret capability", private=True)
    return parse_database_secret(payload, manifest, generation, mode, target_kind)


def create_environment_fd(values: dict[str, str]) -> int:
    if not hasattr(os, "memfd_create"):
        raise RunnerError("runner requires Linux memfd_create")
    descriptor = os.memfd_create("postgres-restore-runner", flags=0)
    payload = "".join(f"{key}={values[key]}\n" for key in sorted(values)).encode(
        "ascii"
    )
    os.write(descriptor, payload)
    os.lseek(descriptor, 0, os.SEEK_SET)
    return descriptor


def inspect_one(
    args: list[str],
    *,
    run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> dict[str, Any]:
    completed = run(
        ["docker", *args],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=CLEAN_ENVIRONMENT,
    )
    value = json.loads(completed.stdout)
    if not isinstance(value, list) or len(value) != 1 or not isinstance(value[0], dict):
        raise RunnerError("Docker inspection did not return one object")
    return value[0]


def exact_execution_identity(container: dict[str, Any]) -> dict[str, Any]:
    try:
        return container_execution_identity(container)
    except HostInventoryError as exc:
        raise RunnerError("container host isolation identity is not exact") from exc


def require_pristine_rootfs(
    container_id: str,
    *,
    run: Callable[..., subprocess.CompletedProcess[Any]] = subprocess.run,
) -> None:
    completed = run(
        ["docker", "diff", container_id],
        check=True,
        capture_output=True,
        env=CLEAN_ENVIRONMENT,
    )
    if completed.stdout not in (b"", ""):
        raise RunnerError("runner writable layer is not pristine")


def command_for_mode(
    manifest: dict[str, Any], mode: str
) -> tuple[str, list[str]]:
    if mode in {"probe", "assert-c-final", "assert-recovery"}:
        return str(manifest["psql_entrypoint"]), list(manifest["probe_argv"])
    if mode in {"bootstrap-role", "drop-role"}:
        return str(manifest["bootstrap_entrypoint"]), list(manifest["bootstrap_argv"])
    collectors = manifest.get("collector_commands")
    if mode in {"capture-runtime", "capture-catalog"}:
        if not isinstance(collectors, dict) or mode not in collectors:
            raise RunnerError("collector mode is not sealed")
        argv = collectors[mode]
        if not isinstance(argv, list) or not all(isinstance(item, str) for item in argv):
            raise RunnerError("sealed collector command is malformed")
        return str(manifest["collector_entrypoint"]), argv
    commands = manifest.get("migration_commands")
    if not isinstance(commands, dict) or mode not in commands:
        raise RunnerError("runner mode is not sealed")
    argv = commands[mode]
    if not isinstance(argv, list) or not all(isinstance(item, str) for item in argv):
        raise RunnerError("sealed runner command is malformed")
    return str(manifest["migration_entrypoint"]), argv


def validate_runner_inspection(
    container: dict[str, Any],
    image: dict[str, Any],
    manifest: dict[str, Any],
    *,
    expected_id: str,
    expected_name: str,
    entrypoint: str,
    argv: list[str],
    environment: dict[str, str],
    expected_running: bool = False,
) -> dict[str, Any]:
    config = container.get("Config", {})
    host = container.get("HostConfig", {})
    networks = container.get("NetworkSettings", {}).get("Networks")
    if (
        container.get("Id") != expected_id
        or container.get("Name") != f"/{expected_name}"
        or container.get("State", {}).get("Running") is not expected_running
        or container.get("Image") != manifest["config_id"]
        or config.get("Image") != manifest["repo_digest"]
        or config.get("Hostname") != expected_name
        or config.get("User") != image.get("Config", {}).get("User")
        or config.get("WorkingDir") != image.get("Config", {}).get("WorkingDir", "")
        or config.get("AttachStdin") is not True
        or config.get("AttachStdout") is not True
        or config.get("AttachStderr") is not True
        or config.get("Tty") is not False
        or config.get("OpenStdin") is not True
        or config.get("StdinOnce") is not False
        or config.get("Entrypoint") != [manifest["execution_gate_entrypoint"]]
        or config.get("Cmd") != [entrypoint, *argv]
        or config.get("Labels") != {
            **manifest["image_labels"],
            **RUNNER_LABELS,
            "adapteng.restore.generation": expected_name.split("-")[2].upper(),
        }
        or host.get("NetworkMode") != "pg-rehearsal"
        or host.get("PortBindings") not in (None, {})
        or host.get("Runtime") != manifest["runtime"]
        or container.get("AppArmorProfile") != manifest["apparmor_profile"]
        or host.get("MaskedPaths") != manifest["masked_paths"]
        or host.get("ReadonlyPaths") != manifest["readonly_paths"]
        or host.get("ReadonlyRootfs") is not manifest["readonly_rootfs"]
        or host.get("Tmpfs") != manifest["tmpfs"]
        or container.get("Mounts") not in (None, [])
        or not isinstance(networks, dict)
        or set(networks) != {"pg-rehearsal"}
    ):
        raise RunnerError("runner container identity/command/isolation is not exact")
    state = container.get("State", {})
    if expected_running:
        if (
            state.get("Status") != "running"
            or state.get("Restarting") is not False
            or state.get("Paused") is not False
            or state.get("Dead") is not False
            or not isinstance(state.get("Pid"), int)
            or state["Pid"] <= 0
        ):
            raise RunnerError("started runner state is not exact")
    elif (
        state.get("Status") != "created"
        or state.get("Restarting") is not False
        or state.get("Paused") is not False
        or state.get("Dead") is not False
        or state.get("Pid") != 0
        or container.get("RestartCount") != 0
    ):
        raise RunnerError("runner is not pristine/never-started")
    expected_env = {
        item.split("=", 1)[0]: item.split("=", 1)[1]
        for item in manifest["image_environment"]
    }
    expected_env.update(environment)
    actual_env = config.get("Env")
    if not isinstance(actual_env, list) or any("=" not in item for item in actual_env):
        raise RunnerError("runner environment inspection is malformed")
    actual_map = dict(item.split("=", 1) for item in actual_env)
    if actual_map != expected_env or len(actual_map) != len(actual_env):
        raise RunnerError("runner environment differs from the exact allowlist")
    if (
        image.get("Id") != manifest["config_id"]
        or image.get("RepoDigests") != [manifest["repo_digest"]]
        or image.get("Os") != manifest["os"]
        or image.get("Architecture") != manifest["architecture"]
    ):
        raise RunnerError("runner image identity/platform is not exact")
    execution = exact_execution_identity(container)
    return {
        "schema_version": 2,
        "status": "MEASURED_APPROVED",
        "container_id_sha256": hashlib.sha256(expected_id.encode("ascii")).hexdigest(),
        "container_execution_identity_sha256": hashlib.sha256(
            canonical_json(execution)
        ).hexdigest(),
        "config_id": manifest["config_id"],
        "repo_digest": manifest["repo_digest"],
        "entrypoint": entrypoint,
        "argv_sha256": hashlib.sha256(canonical_json(argv)).hexdigest(),
        "os": manifest["os"],
        "architecture": manifest["architecture"],
    }


def validate_target_container(
    container: dict[str, Any],
    image: dict[str, Any],
    manifest: dict[str, Any],
    generation: str,
    target_kind: str,
    *,
    expected_id: str,
    expected_name: str,
    expected_volume: str,
    expected_pgdata: str,
    expected_running: bool = True,
    expected_network: str = "pg-rehearsal",
) -> dict[str, Any]:
    if target_kind not in {"recovery", "final"}:
        raise RunnerError("database target kind is not exact")
    try:
        identity = validate_sealed_target(
            container=container,
            image=image,
            expected_id=expected_id,
            expected_name=expected_name,
            expected_network=expected_network,
            expected_host_network_mode="none",
            expected_volume=expected_volume,
            expected_pgdata=expected_pgdata,
            target_policy=manifest["target"],
            generation=generation,
            running=expected_running,
            forbidden_identifiers={"adapteng-ops-db", "postgres-adapteng-ops"},
        )
    except HostInventoryError as exc:
        raise RunnerError("database target identity/command/isolation is not exact") from exc
    execution = exact_execution_identity(container)
    return {
        "container_id": expected_id,
        "container_id_sha256": hashlib.sha256(
            expected_id.encode("utf-8")
        ).hexdigest(),
        "container_execution_identity": execution,
        "container_execution_identity_sha256": hashlib.sha256(
            canonical_json(execution)
        ).hexdigest(),
        "generation": generation,
        "target_kind": target_kind,
        "config_id": manifest["target"]["config_id"],
        "repo_digest": manifest["target"]["repo_digest"],
        "volume_ref_sha256": hashlib.sha256(
            expected_volume.encode("utf-8")
        ).hexdigest(),
        "sealed_target_identity_sha256": hashlib.sha256(
            canonical_json(identity)
        ).hexdigest(),
    }


def require_unchanged_execution_identity(
    before: dict[str, Any], after: dict[str, Any], label: str
) -> None:
    if exact_execution_identity(after) != exact_execution_identity(before):
        raise RunnerError(f"{label} identity changed after isolation measurement")


def load_generation_targets(
    generation: str, expected_procedure_sha256: str
) -> dict[str, str]:
    packet_path = STATE_ROOT / f"generation-{generation}" / "guard-packet.json"
    packet = strict_json_object(
        secure_member_bytes(packet_path, "fixed generation target state", private=True),
        "fixed generation target state",
    )
    required = {
        "generation",
        "procedure_manifest_sha256",
        "recovery_container",
        "recovery_container_id",
        "final_container",
        "final_container_id",
        "volume",
        "locked_network",
        "restore_pg1_path",
        "database_pgdata",
    }
    if (
        not required.issubset(packet)
        or packet.get("generation") != generation
        or packet.get("procedure_manifest_sha256") != expected_procedure_sha256
    ):
        raise RunnerError("fixed generation target state is not exact/bound")
    result = {key: str(packet[key]) for key in required}
    if (
        result["recovery_container"] != f"adapteng-recover-{generation.lower()}"
        or result["final_container"] != f"adapteng-db-{generation.lower()}"
        or result["volume"] != f"adapteng-restore-{generation.lower()}"
        or result["locked_network"] != "pg-rehearsal"
        or not all(
            re.fullmatch(r"[0-9a-f]{64}", result[key])
            for key in ("recovery_container_id", "final_container_id")
        )
    ):
        raise RunnerError("fixed generation target identity is malformed")
    return result


def role_lifecycle_sql(mode: str, scratch_password: str) -> bytes:
    if mode == "bootstrap-role":
        return (
            "CREATE ROLE postgres_restore_runner LOGIN SUPERUSER PASSWORD "
            f"'{scratch_password}';\n"
        ).encode("ascii")
    if mode == "drop-role":
        return b"DROP ROLE postgres_restore_runner;\n"
    raise RunnerError("scratch role lifecycle mode is not exact")


def run_sealed_container(
    *,
    manifest: dict[str, Any],
    generation: str,
    target_kind: str,
    mode: str,
    entrypoint: str,
    argv: list[str],
    environment: dict[str, str],
    sql_input: bytes | None,
    forbidden_identifiers: set[str],
    procedure_manifest_sha256: str,
    run: Callable[..., subprocess.CompletedProcess[Any]] = subprocess.run,
) -> dict[str, Any]:
    environment_fd = create_environment_fd(environment)
    container_id = ""
    runner_name = f"adapteng-runner-{generation.lower()}-{mode}"
    generation_targets = load_generation_targets(
        generation, procedure_manifest_sha256
    )
    target_prefix = "recovery" if target_kind == "recovery" else "final"
    target_name = generation_targets[f"{target_prefix}_container"]
    target_id = generation_targets[f"{target_prefix}_container_id"]
    consumed_provider_operations: set[str] = set()
    labels = [
        "--label",
        f"adapteng.restore.generation={generation}",
        "--label",
        "adapteng.restore.purpose=postgres-restore-rehearsal",
        "--label",
        "adapteng.restore.component=sealed-sql-runner",
    ]
    try:
        created = run(
            [
                "docker",
                "create",
                "--name",
                runner_name,
                "--hostname",
                runner_name,
                "--network",
                "pg-rehearsal",
                "--interactive",
                "--read-only",
                *labels,
                "--env-file",
                f"/proc/self/fd/{environment_fd}",
                "--entrypoint",
                manifest["execution_gate_entrypoint"],
                manifest["repo_digest"],
                entrypoint,
                *argv,
            ],
            check=True,
            capture_output=True,
            pass_fds=(environment_fd,),
            env=CLEAN_ENVIRONMENT,
        )
        container_id = created.stdout.decode("ascii").strip()
        if not container_id:
            raise RunnerError("Docker did not return one runner container ID")
        runner_container = inspect_one(
            ["container", "inspect", container_id], run=run
        )
        runner_image = inspect_one(
            ["image", "inspect", str(runner_container.get("Image", ""))], run=run
        )
        measured_runner = validate_runner_inspection(
            runner_container,
            runner_image,
            manifest,
            expected_id=container_id,
            expected_name=runner_name,
            entrypoint=entrypoint,
            argv=argv,
            environment=environment,
        )
        require_pristine_rootfs(container_id, run=run)
        target_container = inspect_one(["container", "inspect", target_id], run=run)
        target_image = inspect_one(
            ["image", "inspect", str(target_container.get("Image", ""))], run=run
        )
        target = validate_target_container(
            target_container,
            target_image,
            manifest,
            generation,
            target_kind,
            expected_id=target_id,
            expected_name=target_name,
            expected_volume=generation_targets["volume"],
            expected_pgdata=generation_targets["database_pgdata"],
            expected_network=generation_targets["locked_network"],
        )
        provider_pre = collect_provider_operation(
            generation=generation,
            phase="PRE_SQL",
            target_container_id=target_id,
            target_image_identity_sha256=target[
                "sealed_target_identity_sha256"
            ],
            consumed_operations=consumed_provider_operations,
            run=run,
        )
        containers, images, networks, volumes = collect_docker_inventory(run=run)
        expected = {
            target_name: target["container_execution_identity"],
            runner_name: exact_execution_identity(runner_container),
        }
        peer_name = (
            generation_targets["final_container"]
            if target_kind == "recovery"
            else None
        )
        if peer_name is not None:
            peer_id = generation_targets["final_container_id"]
            peer = inspect_one(["container", "inspect", peer_id], run=run)
            peer_image = inspect_one(
                ["image", "inspect", str(peer.get("Image", ""))], run=run
            )
            peer_identity = validate_target_container(
                peer,
                peer_image,
                manifest,
                generation,
                "final",
                expected_id=peer_id,
                expected_name=peer_name,
                expected_volume=generation_targets["volume"],
                expected_pgdata=generation_targets["database_pgdata"],
                expected_running=False,
                expected_network="none",
            )
            expected[peer_name] = peer_identity["container_execution_identity"]
        pre = validate_host_inventory(
            containers=containers,
            images=images,
            networks=networks,
            volumes=volumes,
            expected_containers=expected,
            expected_images={
                (manifest["config_id"], manifest["repo_digest"]),
                (manifest["target"]["config_id"], manifest["target"]["repo_digest"]),
            },
            expected_network="pg-rehearsal",
            expected_volume=f"adapteng-restore-{generation.lower()}",
            forbidden_identifiers=forbidden_identifiers,
            generation=generation,
            stage="PRE_SQL",
            observed_at=datetime.now(timezone.utc),
        )
        runner_again = inspect_one(["container", "inspect", container_id], run=run)
        target_again = inspect_one(["container", "inspect", target["container_id"]], run=run)
        require_unchanged_execution_identity(
            runner_container, runner_again, "runner container"
        )
        require_unchanged_execution_identity(
            target_container, target_again, "database target"
        )
        require_pristine_rootfs(container_id, run=run)
        run(
            ["docker", "start", container_id],
            check=True,
            capture_output=True,
            env=CLEAN_ENVIRONMENT,
        )
        runner_started = inspect_one(["container", "inspect", container_id], run=run)
        runner_image_started = inspect_one(
            ["image", "inspect", str(runner_started.get("Image", ""))], run=run
        )
        validate_runner_inspection(
            runner_started,
            runner_image_started,
            manifest,
            expected_id=container_id,
            expected_name=runner_name,
            entrypoint=entrypoint,
            argv=argv,
            environment=environment,
            expected_running=True,
        )
        target_started = inspect_one(["container", "inspect", target_id], run=run)
        target_image_started = inspect_one(
            ["image", "inspect", str(target_started.get("Image", ""))], run=run
        )
        validate_target_container(
            target_started,
            target_image_started,
            manifest,
            generation,
            target_kind,
            expected_id=target_id,
            expected_name=target_name,
            expected_volume=generation_targets["volume"],
            expected_pgdata=generation_targets["database_pgdata"],
            expected_network=generation_targets["locked_network"],
        )
        require_pristine_rootfs(container_id, run=run)
        completed = run(
            ["docker", "attach", container_id],
            input=b"ADAPTENG_EXECUTE_V1\n" + (sql_input or b""),
            env=CLEAN_ENVIRONMENT,
        )
        if completed.returncode != 0:
            raise RunnerError("sealed runner command failed")
        exited = inspect_one(["container", "inspect", container_id], run=run)
        if exited.get("Id") != container_id or exited.get("State", {}).get(
            "ExitCode"
        ) != 0:
            raise RunnerError("executed runner identity/exit state is not exact")
        run(
            ["docker", "rm", container_id],
            check=True,
            capture_output=True,
            env=CLEAN_ENVIRONMENT,
        )
        container_id = ""
        provider_post = collect_provider_operation(
            generation=generation,
            phase="POST_SQL",
            target_container_id=target_id,
            target_image_identity_sha256=target[
                "sealed_target_identity_sha256"
            ],
            consumed_operations=consumed_provider_operations,
            run=run,
        )
        containers, images, networks, volumes = collect_docker_inventory(run=run)
        post_expected = {target_name: target["container_execution_identity"]}
        if peer_name is not None:
            post_expected[peer_name] = expected[peer_name]
        post = validate_host_inventory(
            containers=containers,
            images=images,
            networks=networks,
            volumes=volumes,
            expected_containers=post_expected,
            expected_images={
                (manifest["config_id"], manifest["repo_digest"]),
                (manifest["target"]["config_id"], manifest["target"]["repo_digest"]),
            },
            expected_network="pg-rehearsal",
            expected_volume=f"adapteng-restore-{generation.lower()}",
            forbidden_identifiers=forbidden_identifiers,
            generation=generation,
            stage="POST_SQL",
            observed_at=datetime.now(timezone.utc),
        )
        return {
            "measured_runner_identity_sha256": hashlib.sha256(
                canonical_json(measured_runner)
            ).hexdigest(),
            "database_container_identity_sha256": target[
                "container_execution_identity_sha256"
            ],
            "pre_sql_host_inventory_sha256": hashlib.sha256(
                canonical_json(pre)
            ).hexdigest(),
            "post_sql_host_inventory_sha256": hashlib.sha256(
                canonical_json(post)
            ).hexdigest(),
            "pre_sql_provider_inventory_sha256": provider_pre["packet_sha256"],
            "post_sql_provider_inventory_sha256": provider_post["packet_sha256"],
            "runner_exit": 0,
        }
    finally:
        if container_id:
            run(
                ["docker", "rm", "--force", container_id],
                capture_output=True,
                env=CLEAN_ENVIRONMENT,
            )
        os.close(environment_fd)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "mode",
        choices=(
            "probe",
            "assert-c-final",
            "assert-recovery",
            "status",
            "apply-007",
            "apply-drive-008",
            "bootstrap-role",
            "drop-role",
            "capture-runtime",
            "capture-catalog",
        ),
    )
    parser.add_argument("--generation", choices=("A", "B", "C"), required=True)
    parser.add_argument(
        "--target-kind", choices=("recovery", "final"), default="final"
    )
    parser.add_argument("--procedure-manifest-sha256", required=True)
    parser.add_argument("--expect", action="append", default=[])
    args = parser.parse_args()
    try:
        if os.name != "posix" or os.geteuid() != 0:
            raise RunnerError("runner requires a POSIX root host")
        verify_procedure(args.procedure_manifest_sha256)
        load_sealed_dependencies()
        manifest, manifest_sha256 = load_manifest()
        environment, database_target_sha256 = read_database_secret(
            manifest, args.generation, args.mode, args.target_kind
        )
        entrypoint, argv = command_for_mode(manifest, args.mode)
        sql_input: bytes | None = None
        if args.mode == "status":
            if not args.expect:
                raise RunnerError("status mode requires expected states")
            argv = [*argv, *sum((["--expect", item] for item in args.expect), [])]
        elif args.expect:
            raise RunnerError("--expect is valid only for status")
        if args.mode in {"probe", "assert-c-final", "assert-recovery"}:
            sql_input = sys.stdin.buffer.read()
        elif args.mode in {"bootstrap-role", "drop-role"}:
            database_capability = json.loads(
                secure_member_bytes(
                    DATABASE_SECRET, "database secret capability", private=True
                )
            )
            sql_input = role_lifecycle_sql(
                args.mode, str(database_capability["runner_password"])
            )
        evidence = run_sealed_container(
            manifest=manifest,
            generation=args.generation,
            target_kind=args.target_kind,
            mode=args.mode,
            entrypoint=entrypoint,
            argv=argv,
            environment=environment,
            sql_input=sql_input,
            forbidden_identifiers={"adapteng-ops-db", "postgres-adapteng-ops"},
            procedure_manifest_sha256=args.procedure_manifest_sha256,
        )
        print(f"runner_manifest_sha256={manifest_sha256}", file=sys.stderr)
        print(
            f"database_target_identity_sha256={database_target_sha256}",
            file=sys.stderr,
        )
        for key, value in evidence.items():
            print(f"{key}={value}", file=sys.stderr)
        return 0
    except (
        HostInventoryError,
        OSError,
        UnicodeError,
        json.JSONDecodeError,
        subprocess.CalledProcessError,
        RunnerError,
    ) as exc:
        print(f"STOP: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
