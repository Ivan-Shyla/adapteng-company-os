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

from scripts.postgres_restore_generation import (
    GenerationError,
    ProviderPolicy,
    load_provider_policy,
    parse_descriptor_owned_bytes,
    project_state,
    require_approved_manifest,
    validate_exclusive_target,
    validate_locked_measurement,
    validate_owned_metadata,
    validate_restore_acceptance,
)
from scripts.postgres_restore_guard import (
    GuardError,
    parse_selected_info_value,
    scan_forbidden_identifiers,
    stable_image_identity,
    validate_generation_names,
    validate_container,
    validate_network,
    validate_repository_endpoint,
    validate_volume,
)
from scripts.postgres_restore_image_identity import (
    IdentityError,
    measure_container,
)
from scripts.postgres_restore_inventory_exporter import (
    ExporterError,
    next_weekly_slots,
    reconcile_timer_names,
    retention_policy,
    validate_effective_unit_properties,
    validate_no_additional_full_jobs,
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
    role_lifecycle_sql,
    validate_database_env,
    validate_runner_inspection,
    validate_target_container,
)
from scripts.postgres_restore_status_gate import (
    StatusGateError,
    execute_status_gate,
)
from scripts.postgres_restore_transaction_probe import (
    ProbeError,
    verify_probe_payload,
)


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"


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
        "postgres_pgdata": "/var/lib/postgresql/data",
        "postgres_version": "16",
        "pgbackrest_version": "2.59.0",
        "pgbackrest_binary_sha256": "3" * 64,
        "build_artifact_sha256": "4" * 64,
        "reviewed_at_utc": "2026-07-31T08:00:00Z",
        "reviewed_by": "reviewer",
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


class NoProductionTestHookTests(unittest.TestCase):
    def test_production_modules_have_no_test_bypass_names(self) -> None:
        forbidden = (
            "POSTGRES_RESTORE_TEST",
            "TEST_MODE",
            "TEST_ROOT",
            "TEST_DOCKER",
            "--now",
        )
        modules = (
            "postgres_restore_guard.py",
            "postgres_restore_image_identity.py",
            "postgres_restore_retention.py",
            "postgres_restore_generation.py",
            "postgres_restore_provider_inventory.py",
            "postgres_restore_runner.py",
            "postgres_restore_status_gate.py",
            "postgres_restore_transaction_probe.py",
            "postgres_restore_c_final_assert.py",
            "postgres_restore_inventory_exporter.py",
        )
        for module in modules:
            text = (SCRIPTS / module).read_text(encoding="utf-8")
            for token in forbidden:
                self.assertNotIn(token, text, f"{module} contains {token}")
        guard = (SCRIPTS / "postgres_restore_guard.py").read_text(encoding="utf-8")
        for forbidden_name in ("--root", "verify-procedure-only", "packet-stdout"):
            self.assertNotIn(forbidden_name, guard)

    def test_exporter_measures_exact_retention_policy(self) -> None:
        valid = b"""[global]
repo1-retention-full=12
repo1-retention-full-type=count
"""
        self.assertEqual(retention_policy(valid), (12, "count"))
        with self.assertRaises(ExporterError):
            retention_policy(valid.replace(b"12", b"8"))
        digest = validate_no_additional_full_jobs(
            [("apt.timer", b"/usr/bin/apt update\n")]
        )
        self.assertEqual(len(digest), 64)
        with self.assertRaises(ExporterError):
            validate_no_additional_full_jobs(
                [("hidden.timer", b"/usr/bin/pgbackrest --type=full backup\n")]
            )
        with self.assertRaises(ExporterError):
            validate_no_additional_full_jobs(
                [("extra-diff.timer", b"/usr/bin/pgbackrest --type=diff backup\n")]
            )
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
        timers = reconcile_timer_names(
            ["full.timer", "diff.timer"],
            ["full.timer", "diff.timer", "transient.timer"],
            {"full.timer", "diff.timer"},
        )
        self.assertIn("transient.timer", timers)


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
                ["repo endpoint=adapteng-ops-db"],
            )

    def test_recovery_sql_container_rejects_repository_credentials(self) -> None:
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
        with self.assertRaises(GuardError):
            validate_repository_endpoint(
                {
                    "endpoint": "s3.eu-central-003.backblazeb2.com",
                    "bucket": "rehearsal",
                    "region": "eu-central-003",
                },
                {
                    "repo1-s3-endpoint": "wrong.example",
                    "repo1-s3-bucket": "rehearsal",
                    "repo1-s3-region": "eu-central-003",
                },
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
            calls: list[tuple[str, ...]] = []

            def inspect(*args: str) -> object:
                calls.append(args)
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
                        "Config": {"Env": ["PGDATA=/var/lib/postgresql/data"]},
                    }
                ]

            packet = measure_container(
                "candidate",
                path,
                sha256_bytes(raw),
                inspect=inspect,
            )
            self.assertEqual(packet["status"], "MEASURED_APPROVED")
            self.assertEqual(len(calls), 2)

    def test_multiple_or_wrong_image_digest_is_rejected(self) -> None:
        manifest = approved_image_manifest()
        container = {
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
            validate_runner_inspection(container, image, manifest)


class DescriptorOwnershipTests(unittest.TestCase):
    def test_symlink_owner_and_mode_attacks_are_rejected(self) -> None:
        with self.assertRaises(GenerationError):
            validate_owned_metadata(
                uid=0, mode=stat.S_IFREG | 0o600, expected_kind="file", is_symlink=True
            )
        with self.assertRaises(GenerationError):
            validate_owned_metadata(
                uid=1000,
                mode=stat.S_IFREG | 0o600,
                expected_kind="file",
                is_symlink=False,
            )
        with self.assertRaises(GenerationError):
            validate_owned_metadata(
                uid=0,
                mode=stat.S_IFREG | 0o644,
                expected_kind="file",
                is_symlink=False,
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

    def test_post_parse_path_replacement_cannot_change_frozen_state(self) -> None:
        packet = {
            "generation": "A",
            "procedure_manifest_sha256": "9" * 64,
            "image_config_id": "sha256:" + "1" * 64,
            "recovery_container": "adapteng-recover-a",
            "final_container": "adapteng-db-a",
            "volume": "adapteng-restore-a",
            "bootstrap_network": "pg-restore-bootstrap",
            "locked_network": "pg-rehearsal",
            "restore_pg1_path": "/restore/a/pgdata",
            "repository_config_path": "/secure/pgbackrest.conf",
            "repository_config_sha256": "6" * 64,
            "restore_env_path": "/secure/restore.env",
            "restore_env_sha256": "7" * 64,
            "stanza": "adapteng-ops",
            "repo": "1",
            "selected_set_ref_sha256": "2" * 64,
            "selected_set_info_sha256": "3" * 64,
            "completed_at": "2026-07-31T08:00:00Z",
            "inventory_sha256": "4" * 64,
            "measured_image_identity_sha256": "5" * 64,
            "cloud_instance_id_sha256": "8" * 64,
        }
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

    def test_missing_or_extra_firewall_is_rejected(self) -> None:
        with self.assertRaises(ProviderInventoryError):
            evaluate_provider_state(
                self.server,
                [],
                generation="A",
                observed_at=self.now,
                owner_ssh_cidr=self.owner_cidr,
            )
        extra = {**self.firewall, "id": 789, "name": "other"}
        server = {
            **self.server,
            "public_net": {
                "firewalls": [
                    {"id": 456, "status": "applied"},
                    {"id": 789, "status": "applied"},
                ]
            },
        }
        with self.assertRaises(ProviderInventoryError):
            evaluate_provider_state(
                server,
                [self.firewall, extra],
                generation="A",
                observed_at=self.now,
                owner_ssh_cidr=self.owner_cidr,
            )

    def test_pending_firewall_attachment_is_rejected(self) -> None:
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

    def test_stale_or_wrong_generation_measurement_is_rejected(self) -> None:
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

    def test_unapproved_provider_manifest_stops_before_restore(self) -> None:
        manifest = json.loads(
            (SCRIPTS / "postgres_restore_provider_manifest.json").read_text(
                encoding="utf-8"
            )
        )
        with self.assertRaises(GenerationError):
            load_provider_policy(manifest)

    def test_signed_measurement_must_match_host_instance(self) -> None:
        packet = evaluate_provider_state(
            self.server,
            [self.firewall],
            generation="A",
            observed_at=self.now,
            owner_ssh_cidr=self.owner_cidr,
        )
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
            validate_locked_measurement(
                packet,
                "A",
                self.now,
                "f" * 64,
                policy,
            )


class RunnerAndProbeTests(unittest.TestCase):
    def test_runner_manifest_is_fail_closed_until_configured(self) -> None:
        manifest = json.loads(
            (SCRIPTS / "postgres_restore_runner_manifest.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(manifest["status"], "NOT_CONFIGURED")
        with self.assertRaises(RunnerError):
            command_for_mode(manifest, "unknown")
        with self.assertRaises(GenerationError):
            require_approved_manifest(manifest, "runner manifest")

    def test_runner_database_target_is_exact_and_secret_is_not_emitted(self) -> None:
        manifest = json.loads(
            (SCRIPTS / "postgres_restore_runner_manifest.json").read_text(
                encoding="utf-8"
            )
        )
        payload = (
            b"PGDATABASE=adapteng_ops\n"
            b"PGPORT=5432\n"
            b"PGOPTIONS=-c role=postgres\n"
            b"PGSSLMODE=disable\n"
            b"PGUSER=postgres_restore_runner\n"
            b"PGHOST=adapteng-db-b\n"
            b"PGPASSWORD=abcdefghijklmnopqrstuvwxyzABCDEF\n"
        )
        target = validate_database_env(payload, manifest, "B")
        self.assertNotIn("PGPASSWORD", target)
        with self.assertRaises(RunnerError):
            validate_database_env(
                payload.replace(b"adapteng-db-b", b"adapteng-ops-db"),
                manifest,
                "B",
            )

    def test_runner_role_lifecycle_and_target_are_exact(self) -> None:
        target = {
            "Id": "container-b",
            "Name": "/adapteng-db-b",
            "State": {"Running": True},
            "HostConfig": {"PortBindings": {}},
            "NetworkSettings": {"Networks": {"pg-rehearsal": {}}},
            "Mounts": [{"Type": "volume", "Name": "adapteng-restore-b"}],
        }
        self.assertEqual(validate_target_container(target, "B")["generation"], "B")
        with self.assertRaises(RunnerError):
            validate_target_container(
                {**target, "NetworkSettings": {"Networks": {"bridge": {}}}},
                "B",
            )
        create = role_lifecycle_sql(
            "bootstrap-role", "abcdefghijklmnopqrstuvwxyzABCDEF"
        )
        self.assertIn(b"CREATE ROLE postgres_restore_runner", create)
        self.assertEqual(
            role_lifecycle_sql("drop-role", "unused"),
            b"DROP ROLE postgres_restore_runner;\n",
        )

    def test_runner_platform_mismatch_is_rejected(self) -> None:
        manifest = approved_image_manifest()
        container = {
            "State": {"Running": False},
            "Image": manifest["config_id"],
            "Config": {"Image": manifest["repo_digest"]},
        }
        image = {
            "Id": manifest["config_id"],
            "RepoDigests": [manifest["repo_digest"]],
            "Os": "linux",
            "Architecture": "arm64",
        }
        with self.assertRaises(RunnerError):
            validate_runner_inspection(container, image, manifest)

    def test_common_image_identity_excludes_unique_container_binding(self) -> None:
        first = {"config_id": "same", "container_ref_sha256": "a" * 64}
        second = {"config_id": "same", "container_ref_sha256": "b" * 64}
        self.assertEqual(stable_image_identity(first), stable_image_identity(second))

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
            execute_status_gate(
                "exact", ["007=exact"], "B", "0" * 64, run=fake_run
            )

    def test_status_without_measured_runner_evidence_is_rejected(self) -> None:
        def fake_run(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
            return subprocess.CompletedProcess([], 0, stdout="exact\n", stderr="")

        with self.assertRaises(StatusGateError):
            execute_status_gate(
                "exact", ["007=exact"], "B", "0" * 64, run=fake_run
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

    def test_exporter_derives_exact_next_twelve_weekly_slots(self) -> None:
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

    def test_authorization_requires_exact_accepted_sources(self) -> None:
        validate_accepted_binding(
            self.accepted,
            self.current,
            accepted_scheduler_sha256="3" * 64,
            accepted_repository_sha256="4" * 64,
            exporter_manifest_sha256="5" * 64,
        )
        substituted = {**self.accepted, "scheduler_inventory_sha256": "9" * 64}
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
            parse_canonical_packet(
                noncanonical, sha256_bytes(noncanonical), "packet"
            )

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


class ReadinessEnumTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
