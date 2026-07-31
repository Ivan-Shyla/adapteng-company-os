#!/usr/bin/env python3
"""Focused adversarial tests for PostgreSQL restore trust boundaries."""

from __future__ import annotations

import hashlib
import json
import stat
import subprocess
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import scripts.postgres_restore_inventory_exporter as inventory_exporter
from scripts.postgres_restore_generation import (
    CLEAN_ENVIRONMENT,
    GenerationError,
    GenerationState,
    ProviderPolicy,
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
    validate_host_inventory,
)
from scripts.postgres_restore_image_identity import IdentityError, measure_container
from scripts.postgres_restore_inventory_exporter import (
    ExporterError,
    canonical_executable_target,
    next_weekly_slots,
    record_sha256,
    retention_policy,
    validate_capability_inventory,
    validate_effective_unit_properties,
    validate_job_policy,
)
from scripts.postgres_restore_isolation_gate import (
    IsolationGateError,
    require_measurement_after,
)
from scripts.postgres_restore_provider_inventory import (
    ProviderInventoryError,
    evaluate_provider_state,
    expected_locked_rules,
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
        "schema_version": 2,
        "status": "APPROVED",
        "repo_digest": runner_repo,
        "config_id": "sha256:" + "6" * 64,
        "os": "linux",
        "architecture": "amd64",
        "image_environment": ["PATH=/usr/bin"],
        "image_labels": {"org.opencontainers.image.revision": "sealed"},
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
            "entrypoint": ["/usr/local/bin/docker-entrypoint.sh"],
            "cmd": ["postgres"],
            "image_environment": [
                "PGDATA=/var/lib/postgresql/data",
                "PATH=/usr/bin",
            ],
            "labels": {
                "adapteng.restore.purpose": "postgres-restore-rehearsal"
            },
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
        final_container="adapteng-db-a",
        volume="adapteng-restore-a",
        bootstrap_network="pg-restore-bootstrap",
        locked_network="pg-rehearsal",
        restore_pg1_path="/restore/a/pgdata",
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


def safe_host_config(network: str) -> dict[str, object]:
    return {
        "NetworkMode": network,
        "PortBindings": {},
        "Privileged": False,
        "PublishAllPorts": False,
        "ReadonlyRootfs": False,
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
        "State": {"Running": False, "ExitCode": 0},
        "Config": {
            "Image": manifest["repo_digest"],
            "Entrypoint": [entrypoint],
            "Cmd": manifest["probe_argv"],
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
        },
        "HostConfig": safe_host_config("pg-rehearsal"),
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
    *, running: bool = True, target_kind: str = "final", container_id: str = "db-id"
) -> dict[str, object]:
    manifest = approved_runner_manifest()
    name = "adapteng-db-a" if target_kind == "final" else "adapteng-recover-a"
    return {
        "Id": container_id,
        "Name": f"/{name}",
        "Image": manifest["target"]["config_id"],
        "State": {"Running": running},
        "Config": {
            "Image": manifest["target"]["repo_digest"],
            "Entrypoint": manifest["target"]["entrypoint"],
            "Cmd": manifest["target"]["cmd"],
            "Env": manifest["target"]["image_environment"],
            "Labels": manifest["target"]["labels"],
            "Hostname": name,
            "User": "postgres",
        },
        "HostConfig": safe_host_config("none"),
        "NetworkSettings": {
            "Networks": {
                "pg-rehearsal": {
                    "NetworkID": "locked-id",
                    "EndpointID": f"{target_kind}-endpoint",
                    "Aliases": [name],
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
        container = {
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
        image = {
            "Id": self.state.image_config_id,
            "RepoDigests": [self.state.image_repo_digest],
            "Config": {"Env": image_environment, "User": "postgres"},
        }
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
        container = {
            "Name": "/adapteng-recover-a",
            "State": {"Running": False},
            "HostConfig": {"NetworkMode": "none", "PortBindings": {}},
            "NetworkSettings": {"Networks": {"none": {}}, "Ports": {}},
            "Mounts": [
                {
                    "Type": "volume",
                    "Name": "adapteng-restore-a",
                    "Destination": "/var/lib/postgresql/data",
                    "RW": True,
                }
            ],
            "Config": {"Env": ["PGBACKREST_REPO1_S3_KEY_SECRET=forbidden"]},
        }
        with self.assertRaises(GuardError):
            validate_container(
                container,
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
                            "Config": {"Image": manifest["repo_digest"]},
                        }
                    ]
                return [
                    {
                        "Id": manifest["config_id"],
                        "RepoDigests": [manifest["repo_digest"]],
                        "Os": "linux",
                        "Architecture": "amd64",
                        "Config": {"Env": manifest["image_environment"]},
                    }
                ]

            packet = measure_container(
                "candidate", path, sha256_bytes(raw), inspect=inspect
            )
            self.assertEqual(packet["status"], "MEASURED_APPROVED")

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
            "labels": {
                "purpose": "postgres-restore-rehearsal",
                "generation": "A",
            },
            "public_net": {"firewalls": [{"id": 456, "status": "applied"}]},
        }
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
        )
        self.assertEqual(packet["status"], "LOCKED_CURRENT")
        self.assertNotIn("id", packet)
        validate_locked_measurement(packet, "A", self.now)

    def test_missing_extra_or_pending_firewall_is_rejected(self) -> None:
        with self.assertRaises(ProviderInventoryError):
            evaluate_provider_state(
                self.server,
                [],
                generation="A",
                observed_at=self.now,
                owner_ssh_cidr=self.owner_cidr,
            )
        server = {
            **self.server,
            "public_net": {
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
            )

    def test_stale_wrong_generation_or_host_measurement_is_rejected(self) -> None:
        packet = evaluate_provider_state(
            self.server,
            [self.firewall],
            generation="A",
            observed_at=self.now,
            owner_ssh_cidr=self.owner_cidr,
        )
        with self.assertRaises(GenerationError):
            validate_locked_measurement(packet, "B", self.now)
        with self.assertRaises(GenerationError):
            validate_locked_measurement(packet, "A", self.now + timedelta(minutes=3))
        policy = ProviderPolicy(
            collector_id="company-os-hetzner-locked-inventory",
            collector_version=1,
            collector_sha256=str(packet["collector_sha256"]),
            public_key_pem="unused",
            public_key_pem_sha256="0" * 64,
            owner_ssh_cidr_sha256=sha256_bytes(self.owner_cidr.encode()),
            max_age_seconds=120,
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

    def test_post_sql_measurement_must_be_newer_than_completion_boundary(self) -> None:
        with self.assertRaises(IsolationGateError):
            require_measurement_after(self.now, self.now)
        with self.assertRaises(IsolationGateError):
            require_measurement_after(self.now, self.now + timedelta(seconds=1))
        require_measurement_after(
            self.now + timedelta(seconds=1),
            self.now,
        )


class RunnerLifecycleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.manifest = approved_runner_manifest()
        self.environment = runner_environment()
        self.container = runner_container()
        self.runner_image = image_objects()[0]
        self.target = target_container()
        self.target_image = image_objects()[1]

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
                "runner_password": "A" * 32,
                "admin_password": "B" * 32,
            }
        )
        values, identity = parse_database_secret(
            payload, self.manifest, "A", "probe", "final"
        )
        self.assertEqual(values["PGHOST"], "adapteng-db-a")
        self.assertNotEqual(identity, sha256_bytes(values["PGPASSWORD"].encode()))
        recovery_values, _ = parse_database_secret(
            payload, self.manifest, "A", "assert-recovery", "recovery"
        )
        self.assertEqual(recovery_values["PGUSER"], "postgres")
        self.assertEqual(recovery_values["PGPASSWORD"], "B" * 32)
        for attack in (
            payload.replace(b'"generation":"A"', b'"generation":"B"'),
            payload[:-2] + b',"PGHOST":"adapteng-ops-db"}\n',
            payload.replace(b"A" * 32, b"short", 1),
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

    def test_target_validation_binds_image_command_and_container_id(self) -> None:
        measured = validate_target_container(
            self.target, self.target_image, self.manifest, "A", "final"
        )
        self.assertEqual(measured["container_id"], "db-id")
        attacks = [
            {**self.target, "Image": "sha256:" + "9" * 64},
            {
                **self.target,
                "Config": {**self.target["Config"], "Entrypoint": ["/bin/sh"]},
            },
            {**self.target, "Id": ""},
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
                validate_target_container(
                    attack, self.target_image, self.manifest, "A", "final"
                )

    def test_stopped_final_peer_remains_on_none_during_recovery_assertion(self) -> None:
        peer = json.loads(json.dumps(self.target))
        peer["State"] = {"Running": False}
        peer["NetworkSettings"]["Networks"] = {
            "none": {
                "NetworkID": "",
                "EndpointID": "",
                "Aliases": None,
            }
        }
        measured = validate_target_container(
            peer,
            self.target_image,
            self.manifest,
            "A",
            "final",
            expected_running=False,
            expected_network="none",
        )
        self.assertEqual(measured["container_id"], "db-id")
        with self.assertRaises(RunnerError):
            validate_target_container(
                peer,
                self.target_image,
                self.manifest,
                "A",
                "final",
                expected_running=False,
            )

    def test_runner_source_never_uses_docker_run_or_second_create(self) -> None:
        text = (SCRIPTS / "postgres_restore_runner.py").read_text(encoding="utf-8")
        self.assertNotIn('"run",', text)
        self.assertEqual(text.count('"create",'), 1)
        self.assertIn('"start", "--attach", "--interactive"', text)

    def test_role_lifecycle_and_command_allowlist_are_exact(self) -> None:
        self.assertIn(
            b"CREATE ROLE postgres_restore_runner",
            role_lifecycle_sql("bootstrap-role", "A" * 32),
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
            if "list-unit-files" in arguments and "--type=path" in arguments:
                return b"unapproved-backup.path enabled\n"
            if "list-unit-files" in arguments or "list-units" in arguments:
                return b""
            if "--property=Triggers" in arguments:
                return b"unapproved-backup.service\n"
            if "cat" in arguments:
                return f"[Unit]\nDescription={arguments[2]}\n".encode("utf-8")
            raise AssertionError(joined)

        with patch.object(inventory_exporter, "command_bytes", fake_command):
            records = inventory_exporter.scheduler_records(set())
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["unit_type"], "path")
        self.assertEqual(records[0]["source_type"], "systemd-activation")
        self.assertEqual(len(records[0]["trigger_units"]), 1)

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
