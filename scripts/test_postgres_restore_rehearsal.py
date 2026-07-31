#!/usr/bin/env python3
"""Focused fail-before-restore tests for the PostgreSQL rehearsal artifacts."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
GENERATION_SCRIPT = SCRIPTS / "postgres_restore_generation.sh"
STATUS_SCRIPT = SCRIPTS / "postgres_restore_status_gate.sh"
PROBE_SCRIPT = SCRIPTS / "postgres_restore_transaction_probe.sh"
PROCEDURE_MANIFEST = SCRIPTS / "postgres_restore_procedure_manifest.json"


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def bash_path() -> str:
    if os.name == "nt":
        candidate = Path(r"C:\Program Files\Git\bin\bash.exe")
        if candidate.is_file():
            return str(candidate)
    found = shutil.which("bash")
    if not found:
        raise unittest.SkipTest("Bash is unavailable")
    return found


def add_python3_shim(directory: Path, env: dict[str, str]) -> None:
    if os.name != "nt":
        return
    directory.mkdir(parents=True, exist_ok=True)
    shell_shim = directory / "python3"
    shell_shim.write_text(
        f"#!/usr/bin/env bash\nexec '{Path(sys.executable).as_posix()}' \"$@\"\n",
        encoding="utf-8",
        newline="\n",
    )
    shell_shim.chmod(0o755)
    (directory / "python3.cmd").write_text(
        f'@"{sys.executable}" %*\n',
        encoding="utf-8",
        newline="\r\n",
    )
    env["PATH"] = str(directory) + os.pathsep + env.get("PATH", "")


class RestoreFixture:
    def __init__(self, base: Path) -> None:
        self.base = base
        self.secure = base / "secure"
        self.system = base / "system"
        self.bin = base / "bin"
        self.evidence = base / "evidence"
        self.state = base / "state"
        self.volume_data = base / "volume-data"
        for path in (
            self.secure,
            self.system,
            self.bin,
            self.evidence,
            self.state,
            self.volume_data,
        ):
            path.mkdir(parents=True, exist_ok=True)

        self.generation = "A"
        self.selected_set = "20260801-000000F"
        self.repo_digest = (
            "registry.example/approved/postgres@sha256:" + "1" * 64
        )
        self.config_id = "sha256:" + "2" * 64
        self.log = base / "docker.log"
        self.fixture_path = base / "docker-fixture.json"
        self.guard_config = self.secure / "generation-a.json"
        self.selected_info = self.secure / "selected-set-info.json"
        self.approved_manifest = self.secure / "approved-image.json"
        self._create_files()
        self._create_fake_docker()

    def _system_path(self, value: str) -> Path:
        return self.system / value.lstrip("/")

    def _create_files(self) -> None:
        hostname = "pg-restore-a"
        machine_id = "machine-a"
        product_uuid = "product-a"
        instance_id = "instance-a"
        for path, value in (
            ("/etc/hostname", hostname),
            ("/etc/machine-id", machine_id),
            ("/sys/class/dmi/id/product_uuid", product_uuid),
        ):
            target = self._system_path(path)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(value + "\n", encoding="utf-8", newline="\n")
        cloud_path = self._system_path("/run/cloud-init/instance-data.json")
        write_json(cloud_path, {"instance_id": instance_id})
        purpose = {
            "schema_version": 1,
            "purpose": "postgres-restore-rehearsal",
            "generation": "A",
            "hostname": hostname,
            "machine_id_sha256": hashlib.sha256(machine_id.encode()).hexdigest(),
            "dmi_product_uuid_sha256": hashlib.sha256(
                product_uuid.encode()
            ).hexdigest(),
            "cloud_instance_id_sha256": hashlib.sha256(
                instance_id.encode()
            ).hexdigest(),
        }
        purpose_path = self._system_path(
            "/etc/adapteng/postgres-restore-purpose.json"
        )
        write_json(purpose_path, purpose)

        manifest = {
            "schema_version": 1,
            "status": "APPROVED",
            "image_reference": self.repo_digest,
            "repo_digest": self.repo_digest,
            "config_id": self.config_id,
            "os": "linux",
            "architecture": "amd64",
            "postgres_pgdata": "/var/lib/postgresql/data",
            "postgres_version": "16",
            "pgbackrest_version": "2.59.0",
            "pgbackrest_binary_sha256": "3" * 64,
            "build_artifact_sha256": "4" * 64,
            "reviewed_at_utc": "2026-08-01T00:00:00Z",
            "reviewed_by": "reviewer",
        }
        write_json(self.approved_manifest, manifest)

        completion = int(
            datetime(2026, 8, 1, tzinfo=timezone.utc).timestamp()
        )
        selected_info = [
            {
                "name": "adapteng-ops",
                "status": {"code": 0, "message": "ok"},
                "backup": [
                    {
                        "label": self.selected_set,
                        "type": "full",
                        "error": False,
                        "archive": {"start": "0001", "stop": "0002"},
                        "timestamp": {
                            "start": completion - 60,
                            "stop": completion,
                        },
                    }
                ],
            }
        ]
        write_json(self.selected_info, selected_info)

        pgbackrest = self.secure / "pgbackrest.conf"
        pgbackrest.write_text(
            "\n".join(
                (
                    "[global]",
                    "repo1-type=s3",
                    "repo1-path=/physical-restore",
                    "repo1-s3-bucket=restore-bucket",
                    "repo1-s3-endpoint=s3.eu-central.example",
                    "repo1-s3-region=eu-central",
                    "repo1-s3-uri-style=path",
                    "repo1-storage-verify-tls=y",
                    "repo1-cipher-type=aes-256-cbc",
                    "",
                    "[adapteng-ops]",
                    "pg1-path=/var/lib/postgresql/data",
                    "",
                )
            ),
            encoding="utf-8",
            newline="\n",
        )
        restore_env = self.secure / "restore.env"
        restore_env.write_text(
            "PGBACKREST_REPO1_S3_KEY=test\n"
            "PGBACKREST_REPO1_S3_KEY_SECRET=test\n"
            "PGBACKREST_REPO1_CIPHER_PASS=test\n",
            encoding="utf-8",
            newline="\n",
        )
        key_attestation = self.secure / "restore-key.json"
        write_json(
            key_attestation,
            {
                "schema_version": 1,
                "endpoint": "s3.eu-central.example",
                "bucket": "restore-bucket",
                "region": "eu-central",
                "capabilities": ["list", "read"],
                "can_write": False,
                "can_delete": False,
            },
        )
        network_attestation = self.secure / "network-attestation.json"
        write_json(
            network_attestation,
            {
                "schema_version": 1,
                "purpose": "postgres-restore-rehearsal",
                "generation": "A",
                "bootstrap_firewall_export_sha256": "5" * 64,
                "locked_firewall_export_sha256": "6" * 64,
                "bootstrap_outbound": ["dns", "https"],
                "locked_outbound": "deny",
                "observed_at_utc": "2026-08-01T01:00:00Z",
            },
        )
        forbidden = self.secure / "forbidden-identifiers.txt"
        forbidden.write_text(
            "adapteng-ops-db\npostgres-adapteng-ops\nproduction.example.internal\n",
            encoding="utf-8",
            newline="\n",
        )

        self.config: dict[str, Any] = {
            "schema_version": 1,
            "purpose": "postgres-restore-rehearsal",
            "generation": "A",
            "host": {
                "hostname": "pg-restore-a",
                "purpose_attestation_sha256": sha256_file(purpose_path),
            },
            "names": {
                "recovery_container": "adapteng-recover-a",
                "final_container": "adapteng-db-a",
                "volume": "adapteng-restore-a",
                "bootstrap_network": "pg-restore-bootstrap",
                "locked_network": "pg-rehearsal",
                "restore_pg1_path": "/restore/a/pgdata",
            },
            "repository": {
                "endpoint": "s3.eu-central.example",
                "bucket": "restore-bucket",
                "region": "eu-central",
                "config_path": str(pgbackrest),
                "config_sha256": sha256_file(pgbackrest),
                "restore_env_path": str(restore_env),
                "restore_env_sha256": sha256_file(restore_env),
                "restore_key_attestation_path": str(key_attestation),
                "restore_key_attestation_sha256": sha256_file(key_attestation),
                "stanza": "adapteng-ops",
                "repo": 1,
            },
            "selected_set": {
                "ref_sha256": hashlib.sha256(
                    self.selected_set.encode()
                ).hexdigest(),
                "info_sha256": sha256_file(self.selected_info),
            },
            "approved_image": {
                "manifest_sha256": sha256_file(self.approved_manifest),
                "platform": "linux/amd64",
            },
            "network_attestation": {
                "path": str(network_attestation),
                "sha256": sha256_file(network_attestation),
            },
            "forbidden_identifiers": {
                "path": str(forbidden),
                "sha256": sha256_file(forbidden),
            },
            "state_dir": str(self.state),
        }
        write_json(self.guard_config, self.config)

        mount = {
            "Type": "volume",
            "Name": "adapteng-restore-a",
            "Source": str(self.volume_data),
            "Destination": "/var/lib/postgresql/data",
            "RW": True,
        }
        recovery = {
            "Id": "5" * 64,
            "Name": "/adapteng-recover-a",
            "Image": self.config_id,
            "Platform": "linux",
            "Config": {
                "Image": self.repo_digest,
                "Env": [
                    "PGDATA=/var/lib/postgresql/data",
                    "PGBACKREST_REPO1_S3_KEY=test",
                ],
            },
            "State": {"Running": False},
            "HostConfig": {
                "NetworkMode": "pg-restore-bootstrap",
                "PortBindings": {},
            },
            "NetworkSettings": {
                "Networks": {"pg-restore-bootstrap": {}},
                "Ports": {},
            },
            "Mounts": [mount],
        }
        final = {
            "Id": "6" * 64,
            "Name": "/adapteng-db-a",
            "Image": self.config_id,
            "Platform": "linux",
            "Config": {
                "Image": self.repo_digest,
                "Env": [
                    "PGDATA=/var/lib/postgresql/data",
                    "POSTGRES_PASSWORD=scratch-only",
                ],
            },
            "State": {"Running": False},
            "HostConfig": {
                "NetworkMode": "pg-rehearsal",
                "PortBindings": {},
            },
            "NetworkSettings": {
                "Networks": {"pg-rehearsal": {}},
                "Ports": {},
            },
            "Mounts": [mount],
        }
        self.docker_fixture: dict[str, Any] = {
            "containers": {
                "adapteng-recover-a": recovery,
                "adapteng-db-a": final,
            },
            "image": {
                "Id": self.config_id,
                "RepoDigests": [self.repo_digest],
                "Os": "linux",
                "Architecture": "amd64",
                "Config": {"Env": ["PGDATA=/var/lib/postgresql/data"]},
            },
            "volume": {
                "Name": "adapteng-restore-a",
                "Driver": "local",
                "Scope": "local",
                "Options": {},
                "Labels": {
                    "adapteng.restore.generation": "A",
                    "adapteng.restore.new": "true",
                    "adapteng.restore.purpose": "postgres-restore-rehearsal",
                },
                "Mountpoint": str(self.volume_data),
            },
            "networks": {
                "pg-restore-bootstrap": {
                    "Name": "pg-restore-bootstrap",
                    "Internal": False,
                    "Driver": "bridge",
                    "Scope": "local",
                    "Options": {},
                },
                "pg-rehearsal": {
                    "Name": "pg-rehearsal",
                    "Internal": True,
                    "Driver": "bridge",
                    "Scope": "local",
                    "Options": {},
                },
            },
            "container_inventory": ["adapteng-recover-a", "adapteng-db-a"],
            "image_inventory": [
                self.config_id + " registry.example/approved/postgres@sha256:" + "1" * 64,
                "sha256:" + "7" * 64 + " registry.example/runner@sha256:" + "8" * 64,
            ],
            "volume_inventory": ["adapteng-restore-a"],
            "network_inventory": [
                "bridge",
                "host",
                "none",
                "pg-restore-bootstrap",
                "pg-rehearsal",
            ],
        }
        write_json(self.fixture_path, self.docker_fixture)

    def _create_fake_docker(self) -> None:
        shim_env = {"PATH": ""}
        add_python3_shim(self.bin, shim_env)
        fake = self.bin / "docker_fake.py"
        fake.write_text(
            """#!/usr/bin/env python3
import json
import os
import sys
from pathlib import Path

args = sys.argv[1:]
fixture = json.loads(Path(os.environ["FAKE_DOCKER_FIXTURE"]).read_text())
with Path(os.environ["FAKE_DOCKER_LOG"]).open("a", encoding="utf-8") as handle:
    handle.write(json.dumps(args) + "\\n")

if args[:2] == ["container", "inspect"]:
    print(json.dumps([fixture["containers"][args[2]]]))
elif args[:2] == ["image", "inspect"]:
    print(json.dumps([fixture["image"]]))
elif args[:2] == ["image", "ls"]:
    print("\\n".join(fixture["image_inventory"]))
elif args[:2] == ["volume", "inspect"]:
    print(json.dumps([fixture["volume"]]))
elif args[:2] == ["network", "inspect"]:
    print(json.dumps([fixture["networks"][args[2]]]))
elif args[:2] == ["ps", "-a"]:
    print("\\n".join(fixture["container_inventory"]))
elif args[:2] == ["volume", "ls"]:
    print("\\n".join(fixture["volume_inventory"]))
elif args[:2] == ["network", "ls"]:
    print("\\n".join(fixture["network_inventory"]))
elif args and args[0] == "run":
    if "restore" in args:
        sys.exit(91)
    sys.exit(0)
else:
    sys.exit(92)
""",
            encoding="utf-8",
            newline="\n",
        )
        if os.name == "nt":
            (self.bin / "docker.cmd").write_text(
                '@python "%~dp0docker_fake.py" %*\n',
                encoding="utf-8",
                newline="\r\n",
            )
            docker = self.bin / "docker"
            docker.write_text(
                f"#!/usr/bin/env bash\nexec '{Path(sys.executable).as_posix()}' "
                f"'{fake.as_posix()}' \"$@\"\n",
                encoding="utf-8",
                newline="\n",
            )
            docker.chmod(0o755)
        else:
            docker = self.bin / "docker"
            shutil.copy(fake, docker)
            docker.chmod(0o755)

    def save_config(self) -> None:
        write_json(self.guard_config, self.config)

    def save_docker_fixture(self) -> None:
        write_json(self.fixture_path, self.docker_fixture)

    def command(self, *, generation: str = "A", selected_set: str | None = None) -> list[str]:
        return [
            bash_path(),
            str(GENERATION_SCRIPT),
            "--generation",
            generation,
            "--guard-config",
            str(self.guard_config),
            "--guard-config-sha256",
            sha256_file(self.guard_config),
            "--selected-set",
            selected_set or self.selected_set,
            "--selected-info",
            str(self.selected_info),
            "--selected-info-sha256",
            sha256_file(self.selected_info),
            "--approved-image-manifest",
            str(self.approved_manifest),
            "--approved-image-manifest-sha256",
            sha256_file(self.approved_manifest),
            "--procedure-manifest-sha256",
            sha256_file(PROCEDURE_MANIFEST),
            "--evidence-dir",
            str(self.evidence),
        ]

    def environment(self) -> dict[str, str]:
        env = os.environ.copy()
        env.update(
            {
                "PATH": str(self.bin) + os.pathsep + env.get("PATH", ""),
                "POSTGRES_RESTORE_TEST_MODE": "1",
                "POSTGRES_RESTORE_TEST_ROOT": str(self.system),
                "POSTGRES_RESTORE_TEST_DOCKER": str(self.bin / "docker_fake.py"),
                "FAKE_DOCKER_FIXTURE": str(self.fixture_path),
                "FAKE_DOCKER_LOG": str(self.log),
                "MSYS2_ARG_CONV_EXCL": (
                    "--config=;--pg1-path=;type=volume,;type=bind,"
                ),
            }
        )
        return env

    def calls(self) -> list[list[str]]:
        if not self.log.exists():
            return []
        return [
            json.loads(line)
            for line in self.log.read_text(encoding="utf-8").splitlines()
        ]


class RestoreGenerationGuardTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.fixture = RestoreFixture(Path(self.temp.name))

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _assert_fails_before_restore(
        self,
        mutate: Callable[[RestoreFixture], None],
        *,
        generation: str = "A",
        selected_set: str | None = None,
    ) -> None:
        mutate(self.fixture)
        completed = subprocess.run(
            self.fixture.command(
                generation=generation, selected_set=selected_set
            ),
            env=self.fixture.environment(),
            capture_output=True,
            text=True,
        )
        self.assertNotEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        self.assertFalse(
            any("restore" in call for call in self.fixture.calls()),
            self.fixture.calls(),
        )

    def test_wrong_generation_fails_before_restore(self) -> None:
        self._assert_fails_before_restore(lambda _: None, generation="B")

    def test_wrong_host_fails_before_restore(self) -> None:
        def mutate(fixture: RestoreFixture) -> None:
            fixture._system_path("/etc/hostname").write_text(
                "wrong-host\n", encoding="utf-8"
            )

        self._assert_fails_before_restore(mutate)

    def test_wrong_volume_fails_before_restore(self) -> None:
        def mutate(fixture: RestoreFixture) -> None:
            fixture.docker_fixture["volume"]["Labels"][
                "adapteng.restore.generation"
            ] = "B"
            fixture.save_docker_fixture()

        self._assert_fails_before_restore(mutate)

    def test_wrong_image_fails_before_restore(self) -> None:
        def mutate(fixture: RestoreFixture) -> None:
            fixture.docker_fixture["image"]["RepoDigests"].append(
                "registry.example/other@sha256:" + "9" * 64
            )
            fixture.save_docker_fixture()

        self._assert_fails_before_restore(mutate)

    def test_wrong_platform_fails_before_restore(self) -> None:
        def mutate(fixture: RestoreFixture) -> None:
            fixture.config["approved_image"]["platform"] = "linux/arm64"
            fixture.save_config()

        self._assert_fails_before_restore(mutate)

    def test_wrong_set_fails_before_restore(self) -> None:
        self._assert_fails_before_restore(
            lambda _: None, selected_set="20260802-000000F"
        )

    def test_wrong_endpoint_fails_before_restore(self) -> None:
        def mutate(fixture: RestoreFixture) -> None:
            fixture.config["repository"]["endpoint"] = "wrong.example"
            fixture.save_config()

        self._assert_fails_before_restore(mutate)

    def test_wrong_network_fails_before_restore(self) -> None:
        def mutate(fixture: RestoreFixture) -> None:
            fixture.docker_fixture["networks"]["pg-rehearsal"]["Internal"] = False
            fixture.save_docker_fixture()

        self._assert_fails_before_restore(mutate)

    def test_production_identifier_fails_before_restore(self) -> None:
        def mutate(fixture: RestoreFixture) -> None:
            fixture.docker_fixture["container_inventory"].append("adapteng-ops-db")
            fixture.save_docker_fixture()

        self._assert_fails_before_restore(mutate)

    def test_valid_guard_reaches_only_mocked_restore(self) -> None:
        completed = subprocess.run(
            self.fixture.command(),
            env=self.fixture.environment(),
            capture_output=True,
            text=True,
        )
        self.assertEqual(completed.returncode, 91, completed.stdout + completed.stderr)
        restore_calls = [
            call for call in self.fixture.calls() if "restore" in call
        ]
        self.assertEqual(len(restore_calls), 1)
        restore_call = restore_calls[0]
        self.assertIn("--config=/etc/pgbackrest/pgbackrest.conf", restore_call)
        self.assertIn("--stanza=adapteng-ops", restore_call)
        self.assertIn("--repo=1", restore_call)
        self.assertIn("--pg1-path=/restore/a/pgdata", restore_call)
        self.assertIn("--set=20260801-000000F", restore_call)


class StatusAndProbeIntegrityTests(unittest.TestCase):
    def test_exact_output_with_nonzero_exit_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            fake = Path(temp) / "fake-status.sh"
            fake.write_text(
                "#!/usr/bin/env bash\nprintf 'exact\\n'\nexit 9\n",
                encoding="utf-8",
                newline="\n",
            )
            completed = subprocess.run(
                [
                    bash_path(),
                    str(STATUS_SCRIPT),
                    "--expect-output",
                    "exact",
                    "--",
                    bash_path(),
                    str(fake),
                ],
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(completed.returncode, 0)
            self.assertIn("status command failed", completed.stderr)

    def _copied_probe_command(
        self, base: Path, expected_manifest_sha256: str
    ) -> tuple[list[str], dict[str, str]]:
        copied_scripts = base / "scripts"
        copied_scripts.mkdir()
        manifest = json.loads(PROCEDURE_MANIFEST.read_text(encoding="utf-8"))
        for relative in manifest["artifacts"]:
            source = ROOT / relative
            target = base / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
        shutil.copyfile(PROCEDURE_MANIFEST, copied_scripts / PROCEDURE_MANIFEST.name)
        pgpass = base / "pgpass"
        pgpass.write_text("*:*:*:*:scratch\n", encoding="utf-8")
        command = [
            bash_path(),
            str(copied_scripts / PROBE_SCRIPT.name),
            "--procedure-manifest-sha256",
            expected_manifest_sha256,
            "--runner-image",
            "registry.example/runner@sha256:" + "a" * 64,
            "--pgpass-file",
            str(pgpass),
            "--evidence-dir",
            str(base / "evidence"),
        ]
        env = os.environ.copy()
        add_python3_shim(base / "bin", env)
        env["POSTGRES_RESTORE_TEST_MODE"] = "1"
        return command, env

    def test_substituted_noop_probe_is_rejected_before_docker(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            expected = sha256_file(PROCEDURE_MANIFEST)
            command, env = self._copied_probe_command(base, expected)
            copied_probe = (
                base / "scripts" / "postgres_restore_transaction_probe.sql"
            )
            copied_probe.write_text("SELECT 1;\n", encoding="utf-8")
            completed = subprocess.run(
                command, env=env, capture_output=True, text=True
            )
            self.assertNotEqual(completed.returncode, 0)
            self.assertIn("digest mismatch", completed.stderr)

    def test_wrong_procedure_digest_is_rejected_before_docker(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            command, env = self._copied_probe_command(base, "0" * 64)
            completed = subprocess.run(
                command, env=env, capture_output=True, text=True
            )
            self.assertNotEqual(completed.returncode, 0)
            self.assertIn("procedure manifest digest mismatch", completed.stderr)


class RetentionGateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.selected_set = "20260801-000000F"
        self.completed = datetime(2026, 8, 1, tzinfo=timezone.utc)
        self.info = self.base / "info.json"
        write_json(
            self.info,
            [
                {
                    "status": {"code": 0, "message": "ok"},
                    "backup": [
                        {
                            "label": self.selected_set,
                            "type": "full",
                            "error": False,
                            "timestamp": {
                                "stop": int(self.completed.timestamp())
                            },
                        }
                    ],
                }
            ],
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    @staticmethod
    def timestamp(value: datetime) -> str:
        return value.strftime("%Y-%m-%dT%H:%M:%SZ")

    def run_gate(
        self,
        *,
        mode: str,
        now: datetime,
        newer_fulls: int = 0,
        rollout_start: datetime | None = None,
    ) -> subprocess.CompletedProcess[str]:
        scheduler_path = self.base / f"scheduler-{mode}.json"
        repository_path = self.base / f"repository-{mode}.json"
        output = self.base / f"output-{mode}.json"
        future = [
            now + timedelta(days=7 * index)
            for index in range(1, 13)
        ]
        write_json(
            scheduler_path,
            {
                "schema_version": 1,
                "generated_at_utc": self.timestamp(now),
                "full_jobs_count": 1,
                "timezone": "UTC",
                "future_fulls_utc": [self.timestamp(item) for item in future],
            },
        )
        completed_fulls = [
            {
                "label": self.selected_set,
                "completed_at_utc": self.timestamp(self.completed),
                "type": "full",
                "status": "complete",
            }
        ]
        for index in range(newer_fulls):
            completed_fulls.append(
                {
                    "label": f"newer-{index}",
                    "completed_at_utc": self.timestamp(
                        self.completed + timedelta(hours=12 * (index + 1))
                    ),
                    "type": "full",
                    "status": "complete",
                }
            )
        write_json(
            repository_path,
            {
                "schema_version": 1,
                "generated_at_utc": self.timestamp(now),
                "retention_full": 12,
                "retention_full_type": "count",
                "selected_set": self.selected_set,
                "completed_fulls": completed_fulls,
            },
        )
        command = [
            sys.executable,
            str(SCRIPTS / "postgres_restore_retention.py"),
            "--mode",
            mode,
            "--selected-set",
            self.selected_set,
            "--selected-info",
            str(self.info),
            "--selected-info-sha256",
            sha256_file(self.info),
            "--scheduler-inventory",
            str(scheduler_path),
            "--scheduler-inventory-sha256",
            sha256_file(scheduler_path),
            "--repository-inventory",
            str(repository_path),
            "--repository-inventory-sha256",
            sha256_file(repository_path),
            "--now",
            self.timestamp(now),
            "--output",
            str(output),
        ]
        if rollout_start is not None:
            command.extend(["--rollout-start", self.timestamp(rollout_start)])
        env = os.environ.copy()
        env["POSTGRES_RESTORE_TEST_MODE"] = "1"
        return subprocess.run(command, env=env, capture_output=True, text=True)

    def test_acceptance_derives_completion_and_retention(self) -> None:
        now = self.completed + timedelta(hours=1)
        completed = self.run_gate(mode="acceptance", now=now)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        packet = json.loads(
            (self.base / "output-acceptance.json").read_text(encoding="utf-8")
        )
        self.assertEqual(packet["completed_at"], "2026-08-01T00:00:00Z")
        self.assertEqual(packet["selected_set_info_sha256"], sha256_file(self.info))
        self.assertEqual(
            packet["scheduler_inventory_observed_at"], self.timestamp(now)
        )
        self.assertIn("scheduler_inventory_sha256", packet)
        self.assertIn("retention_valid_until", packet)
        self.assertEqual(packet["authorization_status"], "NOT_AUTHORIZED")
        self.assertNotIn("actual_rollout_start", packet)

    def test_authorization_binds_actual_rollout_and_fresh_inventories(self) -> None:
        now = self.completed + timedelta(days=4)
        completed = self.run_gate(
            mode="authorization", now=now, rollout_start=now
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        packet = json.loads(
            (self.base / "output-authorization.json").read_text(encoding="utf-8")
        )
        self.assertEqual(packet["authorization_status"], "AUTHORIZED")
        self.assertEqual(packet["actual_rollout_start"], self.timestamp(now))
        self.assertEqual(packet["authorization_checked_at"], self.timestamp(now))
        self.assertIn("rollout_required_through", packet)
        self.assertEqual(packet["completed_at"], "2026-08-01T00:00:00Z")
        self.assertEqual(packet["selected_set_info_sha256"], sha256_file(self.info))
        self.assertIn("scheduler_inventory_sha256", packet)
        self.assertEqual(
            packet["scheduler_inventory_observed_at"], self.timestamp(now)
        )
        self.assertIn("repository_inventory_sha256", packet)
        self.assertIn("retention_valid_until", packet)

    def test_extra_fulls_shorten_horizon_and_fail_closed(self) -> None:
        now = self.completed + timedelta(days=4)
        completed = self.run_gate(
            mode="authorization",
            now=now,
            newer_fulls=5,
            rollout_start=now,
        )
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("does not cover", completed.stderr)


if __name__ == "__main__":
    unittest.main()
