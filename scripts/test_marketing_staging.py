#!/usr/bin/env python3
"""Controls for the fixed marketing-staging operations.

The risks this file exists to catch: SQL or commands that did not come from a
reviewed constant, an operation that can reach `public` or `adapteng_ops`, a
write that proceeds without a fresh backup or the confirmation phrase, a
migration that is not the pinned one, a password that reaches a log, and a
workflow bound to the wrong credential.

The Postgres tests at the end run only when a disposable container is named and
confirmed (MARKETING_STAGING_PG_CONTAINER and FIXED_MIGRATION_TEST_CONFIRM_DISPOSABLE=yes);
they are skipped in CI.
"""

from __future__ import annotations

import io
import json
import os
import re
import subprocess
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

import coolify_deploy as driver  # noqa: E402
import marketing_staging as ms  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
WORKFLOW = ROOT / ".github" / "workflows" / "marketing-staging-provision.yml"

MIGRATION_009 = """-- header
BEGIN;
CREATE TABLE IF NOT EXISTS marketing_publication_approvals (event_id BIGINT PRIMARY KEY);
CREATE OR REPLACE VIEW marketing_publication_current_approval AS SELECT 1 AS x;
COMMIT;
"""
MIGRATION_010 = """-- header with search_path mentioned only in a comment: SET search_path = x;
BEGIN;
CREATE TABLE IF NOT EXISTS marketing_publication_radar (dedupe_key TEXT PRIMARY KEY);
COMMIT;
"""


def fake_pins(extra_workflow_change: bool = False):
    """Return a fetch function and the pin tables patched to match a tiny fixture set."""

    files: dict[str, bytes] = {}
    migration_pins, workflow_pins = {}, {}
    for path, text in zip(ms.MIGRATION_PINS, (MIGRATION_009, MIGRATION_010)):
        files[path] = text.encode()
        migration_pins[path] = ms.sha256_hex(files[path])
    for path, (name, _) in ms.WORKFLOW_PINS.items():
        definition = {"name": name, "active": False, "nodes": [
            {"name": "Manual Trigger", "type": "n8n-nodes-base.manualTrigger", "parameters": {}},
            {"name": "Q", "type": "n8n-nodes-base.postgres", "parameters": {"query": "select 1"},
             "credentials": {"postgres": {"id": "REPLACE_WITH_POSTGRES_ID", "name": ms.CREDENTIAL_NAME}}, "disabled": True},
        ], "connections": {}, "settings": {"executionOrder": "v1"}}
        files[path] = json.dumps(definition).encode()
        workflow_pins[path] = (name, ms.sha256_hex(files[path]))
    return files, migration_pins, workflow_pins


class PinnedSourceTests(unittest.TestCase):
    def test_pins_are_exact_commit_and_digests(self) -> None:
        self.assertRegex(ms.PIN_COMMIT, r"^[0-9a-f]{40}$")
        for digest in list(ms.MIGRATION_PINS.values()) + [d for _, d in ms.WORKFLOW_PINS.values()]:
            self.assertRegex(digest, r"^[0-9a-f]{64}$")
        self.assertEqual(len(ms.MIGRATION_PINS), 2)
        self.assertEqual(len(ms.WORKFLOW_PINS), 6)

    def test_load_accepts_matching_files(self) -> None:
        files, mpins, wpins = fake_pins()
        with mock.patch.object(ms, "MIGRATION_PINS", mpins), mock.patch.object(ms, "WORKFLOW_PINS", wpins):
            migrations, workflows = ms.load_pinned(lambda path: files[path])
        self.assertEqual(len(migrations), 2)
        self.assertEqual(len(workflows), 6)

    def test_load_refuses_a_changed_migration(self) -> None:
        files, mpins, wpins = fake_pins()
        first = next(iter(mpins))
        files[first] = files[first] + b"\n-- changed\n"
        with mock.patch.object(ms, "MIGRATION_PINS", mpins), mock.patch.object(ms, "WORKFLOW_PINS", wpins):
            with self.assertRaisesRegex(ms.Refused, "pinned sha256"):
                ms.load_pinned(lambda path: files[path])

    def test_load_refuses_a_changed_workflow_and_a_renamed_one(self) -> None:
        files, mpins, wpins = fake_pins()
        path = next(iter(wpins))
        files[path] = files[path].replace(b'"active": false', b'"active": true')
        with mock.patch.object(ms, "MIGRATION_PINS", mpins), mock.patch.object(ms, "WORKFLOW_PINS", wpins):
            with self.assertRaises(ms.Refused):
                ms.load_pinned(lambda p: files[p])
        files, mpins, wpins = fake_pins()
        path = next(iter(wpins))
        wpins[path] = ("Another name", wpins[path][1])
        with mock.patch.object(ms, "MIGRATION_PINS", mpins), mock.patch.object(ms, "WORKFLOW_PINS", wpins):
            with self.assertRaisesRegex(ms.Refused, "expected inactive workflow"):
                ms.load_pinned(lambda p: files[p])

    def test_load_refuses_carriage_returns(self) -> None:
        files, mpins, wpins = fake_pins()
        first = next(iter(mpins))
        files[first] = files[first].replace(b"\n", b"\r\n")
        mpins[first] = ms.sha256_hex(files[first])
        with mock.patch.object(ms, "MIGRATION_PINS", mpins), mock.patch.object(ms, "WORKFLOW_PINS", wpins):
            with self.assertRaisesRegex(ms.Refused, "carriage"):
                ms.load_pinned(lambda p: files[p])

    @unittest.skipUnless((ROOT.parent.parent / "adapteng-automation-platform").exists() or Path("C:/GitHub/adapteng-automation-platform").exists(), "no platform checkout")
    def test_pins_match_the_platform_checkout_when_it_is_at_the_pinned_commit(self) -> None:
        checkout = Path("C:/GitHub/adapteng-automation-platform")
        if not checkout.exists():
            self.skipTest("no platform checkout")
        for path, digest in ms.MIGRATION_PINS.items():
            blob = subprocess.run(["git", "cat-file", "blob", f"{ms.PIN_COMMIT}:{path}"], cwd=checkout, capture_output=True)
            if blob.returncode != 0:
                self.skipTest("the pinned commit is not in this checkout")
            self.assertEqual(ms.sha256_hex(blob.stdout), digest, path)
            ms.migration_body(path, blob.stdout.decode())


class MigrationScanTests(unittest.TestCase):
    def test_wrapper_is_removed_and_comments_are_ignored(self) -> None:
        body = ms.migration_body("010", MIGRATION_010)
        self.assertNotIn("BEGIN;", body)
        self.assertNotIn("COMMIT;", body)
        self.assertIn("CREATE TABLE IF NOT EXISTS marketing_publication_radar", body)

    def test_statements_that_could_leave_the_schema_are_refused(self) -> None:
        bad = [
            "SET search_path = public;", "SET ROLE postgres;", "CREATE SCHEMA other;", "DROP TABLE x;", "GRANT ALL ON x TO y;",
            "CREATE ROLE evil;", "ALTER ROLE r SUPERUSER;", "CREATE TABLE public.leak (i int);", "INSERT INTO adapteng_ops.agent_run VALUES (1);",
            "CREATE EXTENSION pgcrypto;", "COPY x FROM PROGRAM 'id';", "TRUNCATE x;", "\\i other.sql", "SET SESSION AUTHORIZATION x;",
        ]
        for statement in bad:
            text = f"BEGIN;\n{statement}\nCOMMIT;\n"
            with self.subTest(statement=statement), self.assertRaises(ms.Refused):
                ms.migration_body("x", text)

    def test_exactly_one_transaction_wrapper_is_required(self) -> None:
        for text in ("CREATE TABLE a (i int);\n", "BEGIN;\nCREATE TABLE a (i int);\n", "BEGIN;\nBEGIN;\nCOMMIT;\nCOMMIT;\n", "COMMIT;\nBEGIN;\n"):
            with self.subTest(text=text), self.assertRaises(ms.Refused):
                ms.migration_body("x", text)


class ProvisionSqlTests(unittest.TestCase):
    def setUp(self) -> None:
        self.sql = ms.provision_sql({path: text for path, text in zip(ms.MIGRATION_PINS, (MIGRATION_009, MIGRATION_010))})

    def test_one_transaction_with_the_schema_pinned_before_the_migrations(self) -> None:
        self.assertEqual(self.sql.count("BEGIN;"), 1)
        self.assertEqual(self.sql.count("COMMIT;"), 1)
        pinned = self.sql.index("SET LOCAL search_path TO marketing_staging, pg_temp;")
        self.assertLess(pinned, self.sql.index("CREATE TABLE IF NOT EXISTS marketing_publication_approvals"))
        self.assertLess(self.sql.index("marketing_publication_approvals"), self.sql.index("marketing_publication_radar"))

    def test_nothing_is_created_outside_the_one_schema_and_role(self) -> None:
        self.assertEqual(self.sql.count("CREATE SCHEMA"), 1)
        self.assertEqual(self.sql.count("CREATE ROLE"), 1)
        self.assertNotRegex(self.sql, r"(?i)\bpublic\s*\.")
        self.assertNotRegex(self.sql, r"(?i)adapteng_ops\s*\.")
        self.assertNotRegex(self.sql, r"(?i)\bDROP\b.*\b(SCHEMA|DATABASE|ROLE)\b")
        self.assertNotIn("SUPERUSER;", self.sql.replace("NOSUPERUSER", ""))

    def test_the_role_is_least_privileged_and_has_no_password_yet(self) -> None:
        self.assertIn("LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS NOINHERIT CONNECTION LIMIT 5", self.sql)
        self.assertNotRegex(self.sql, r"(?i)\bPASSWORD\b")
        self.assertIn("GRANT SELECT, INSERT, UPDATE ON ALL TABLES IN SCHEMA marketing_staging", self.sql)
        self.assertNotRegex(self.sql, r"(?i)GRANT\s+(ALL\b|[^;]*?\b(DELETE|TRUNCATE)\b)")

    def test_the_isolation_proof_runs_before_the_commit(self) -> None:
        for fragment in ("isolation: % changed", "can reach", "can use sequence", "can create objects in schema", "SECURITY DEFINER", "can create schemas", "member of another role"):
            self.assertIn(fragment, self.sql)
        self.assertLess(self.sql.index("$isolation$;"), self.sql.index("COMMIT;"))

    def test_password_statement_keeps_the_value_out_of_sql_after_the_first_line(self) -> None:
        sql = ms.SET_PASSWORD_SQL.replace("{pw}", "ABCDEFGHIJKLMNOPQRSTUVWXYZ012345")
        self.assertEqual(sql.count("ABCDEFGHIJKLMNOPQRSTUVWXYZ012345"), 1)
        self.assertTrue(sql.splitlines()[1].startswith("\\set pw "))
        self.assertIn("scram-sha-256", sql)
        self.assertIn("current_setting('marketing_staging.pw')", sql)

    def test_owner_sql_generates_the_password_on_the_server_and_shows_it_once(self) -> None:
        text = ms.owner_sql({path: text for path, text in zip(ms.MIGRATION_PINS, (MIGRATION_009, MIGRATION_010))})
        self.assertIn("gen_random_uuid()", text)
        self.assertEqual(text.count("\\echo :pw"), 1)
        self.assertTrue(text.startswith("\\set ON_ERROR_STOP on"))


class StateTests(unittest.TestCase):
    base = {"schema": "present", "role": "present", "schema_tables": "7", "schema_views": "1", "role_attrs_ok": "true",
            "role_memberships": "0", "role_has_password": "false", "role_search_path": "search_path=marketing_staging, pg_temp"}

    def test_states(self) -> None:
        self.assertEqual(ms.classify({"schema": "absent", "role": "absent"}), "absent")
        self.assertEqual(ms.classify(self.base), "provisioned-no-password")
        self.assertEqual(ms.classify({**self.base, "role_has_password": "true"}), "exact")

    def test_anything_else_is_drift_and_is_not_repaired(self) -> None:
        for change in ({"schema": "absent"}, {"role": "absent"}, {"schema_tables": "6"}, {"role_attrs_ok": "false"}, {"role_memberships": "1"}, {"role_search_path": "search_path=public"}):
            self.assertEqual(ms.classify({**self.base, **change}), "drifted", change)

    def test_identity_gate(self) -> None:
        good = {"database": "adapteng_ops", "server_version_num": "160004", "is_superuser": "on"}
        ms.identity_gate(good)
        for change in ({"database": "postgres"}, {"server_version_num": "150000"}, {"is_superuser": "off"}):
            with self.assertRaises(ms.Refused):
                ms.identity_gate({**good, **change})


PROVISIONED = (
    "schema|present\nrole|present\nschema_tables|7\nschema_views|1\nrole_attrs_ok|true\n"
    "role_memberships|0\nrole_has_password|false\nrole_search_path|search_path=marketing_staging, pg_temp\n"
)


class FakeHost:
    """Scripted stand-in for prod_ssh.Host: records every stdin it was given."""

    def __init__(self, containers: str, answers: dict[str, str] | None = None, fail_on: str | None = None) -> None:
        self.containers = containers
        self.answers = answers or {}
        self.fail_on = fail_on
        self.stdins: list[str] = []

    def run(self, remote: str, stdin: str | None = None, timeout: int = 120):
        if remote.startswith("docker ps"):
            return SimpleNamespace(returncode=0, stdout=self.containers, stderr="")
        self.stdins.append(stdin or "")
        if self.fail_on and self.fail_on in (stdin or ""):
            return SimpleNamespace(returncode=3, stdout="", stderr="ERROR: boom")
        if "CREATE SCHEMA" in (stdin or ""):
            self.wrote = True
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        if "database|" in (stdin or "") or "current_database()" in (stdin or ""):
            return SimpleNamespace(returncode=0, stdout="database|adapteng_ops\nserver_version_num|160004\nis_superuser|on\ncurrent_user|postgres\n", stderr="")
        if "schema|" in (stdin or "") and "role|" in (stdin or ""):
            if getattr(self, "wrote", False):
                return SimpleNamespace(returncode=0, stdout=self.answers.get("after", PROVISIONED), stderr="")
            return SimpleNamespace(returncode=0, stdout=self.answers.get("state", "schema|absent\nrole|absent\nobjects_public|10\n"), stderr="")
        return SimpleNamespace(returncode=0, stdout="", stderr="")


GOOD_CONTAINERS = f"{ms.DB_CONTAINER}\tpostgres:16\nother1\tghcr.io/x/app:1\n"


class ContainerTests(unittest.TestCase):
    def test_exactly_one_postgres_container_with_the_declared_name(self) -> None:
        self.assertEqual(ms.resolve_container(FakeHost(GOOD_CONTAINERS)), ms.DB_CONTAINER)

    def test_refuses_none_two_or_the_wrong_image(self) -> None:
        for listing in ("a\tpostgres:16\n", f"{ms.DB_CONTAINER}\tnginx:1\n", GOOD_CONTAINERS + GOOD_CONTAINERS, ""):
            with self.subTest(listing=listing), self.assertRaises(ms.Refused):
                ms.resolve_container(FakeHost(listing))

    def test_a_timed_out_channel_is_named_with_the_recovery_pointer(self) -> None:
        host = SimpleNamespace(run=lambda *a, **k: SimpleNamespace(returncode=255, stdout="", stderr="ssh: connect to host x port 22: Connection timed out"))
        with self.assertRaisesRegex(ms.Refused, "TCP port 22 timed out.*runbooks/marketing-staging-provision.md"):
            ms.resolve_container(host)


class OperationTests(unittest.TestCase):
    def fetch(self):
        files, mpins, wpins = fake_pins()
        patches = (mock.patch.object(ms, "MIGRATION_PINS", mpins), mock.patch.object(ms, "WORKFLOW_PINS", wpins))
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        return lambda path: files[path]

    def test_writes_need_the_exact_confirmation_phrase(self) -> None:
        for operation, phrase in ms.CONFIRMATION.items():
            with self.assertRaises(ms.Refused):
                ms.require_confirmation(operation, "")
            with self.assertRaises(ms.Refused):
                ms.require_confirmation(operation, phrase.lower())
            ms.require_confirmation(operation, phrase)
        ms.require_confirmation("preflight", "")
        ms.require_confirmation("verify", "")

    def test_provision_stops_before_any_write_when_the_backup_is_stale(self) -> None:
        fetch = self.fetch()
        host = FakeHost(GOOD_CONTAINERS)
        with mock.patch.object(driver, "operate_backup_check", return_value=driver.EXIT_FAILED), redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(ms.Refused, "backup"):
                ms.op_provision_database(host, fetch=fetch, backup_client=object())
        self.assertFalse(any("CREATE SCHEMA" in s for s in host.stdins))

    def test_provision_refuses_without_a_backup_client(self) -> None:
        fetch = self.fetch()
        host = FakeHost(GOOD_CONTAINERS)
        with redirect_stdout(io.StringIO()), self.assertRaises(ms.Refused):
            ms.op_provision_database(host, fetch=fetch, backup_client=None)
        self.assertFalse(any("CREATE SCHEMA" in s for s in host.stdins))

    def test_provision_runs_one_transaction_and_checks_the_result(self) -> None:
        fetch = self.fetch()
        host = FakeHost(GOOD_CONTAINERS)
        with mock.patch.object(driver, "operate_backup_check", return_value=driver.EXIT_OK), redirect_stdout(io.StringIO()):
            code = ms.op_provision_database(host, fetch=fetch, backup_client=object())
        self.assertEqual(code, driver.EXIT_OK)
        writes = [s for s in host.stdins if "CREATE SCHEMA" in s]
        self.assertEqual(len(writes), 1)
        self.assertEqual(writes[0].count("COMMIT;"), 1)

    def test_provision_does_nothing_when_already_provisioned_and_never_repairs_drift(self) -> None:
        fetch = self.fetch()
        done = "schema|present\nrole|present\nschema_tables|7\nschema_views|1\nrole_attrs_ok|true\nrole_memberships|0\nrole_has_password|false\nrole_search_path|search_path=marketing_staging, pg_temp\n"
        host = FakeHost(GOOD_CONTAINERS, {"state": done})
        with redirect_stdout(io.StringIO()):
            self.assertEqual(ms.op_provision_database(host, fetch=fetch, backup_client=object()), driver.EXIT_OK)
        self.assertFalse(any("CREATE SCHEMA" in s for s in host.stdins))
        host = FakeHost(GOOD_CONTAINERS, {"state": done.replace("schema_tables|7", "schema_tables|3")})
        with redirect_stdout(io.StringIO()), self.assertRaisesRegex(ms.Refused, "unexpected shape"):
            ms.op_provision_database(host, fetch=fetch, backup_client=object())

    def test_a_failed_transaction_is_reported_as_rolled_back(self) -> None:
        fetch = self.fetch()
        host = FakeHost(GOOD_CONTAINERS, fail_on="CREATE SCHEMA")
        with mock.patch.object(driver, "operate_backup_check", return_value=driver.EXIT_OK), redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(ms.Refused, "rolled back"):
                ms.op_provision_database(host, fetch=fetch, backup_client=object())

    def test_the_password_never_reaches_the_output(self) -> None:
        fetch = self.fetch()
        provisioned = "schema|present\nrole|present\nschema_tables|7\nschema_views|1\nrole_attrs_ok|true\nrole_memberships|0\nrole_has_password|false\nrole_search_path|search_path=marketing_staging, pg_temp\n"
        host = FakeHost(GOOD_CONTAINERS, {"state": provisioned}, fail_on="ALTER ROLE")
        secret_value = "Zz0123456789abcdefghijklmnopqrstuvwxyzAB"
        client = mock.Mock()
        out = io.StringIO()
        with mock.patch.object(driver, "operate_backup_check", return_value=driver.EXIT_OK), \
                mock.patch.object(ms.n8n_deploy, "list_all_workflows", return_value=[{"id": "NsWG1hD8VmIRRwCv", "name": "x"}]), \
                redirect_stdout(out), self.assertRaises(ms.Refused):
            ms.op_configure_n8n(host, fetch=fetch, backup_client=object(), n8n_client=client, password_factory=lambda: secret_value)
        self.assertNotIn(secret_value, out.getvalue())
        client.request.assert_not_called()  # the credential is not created when the role step fails

    def test_generated_password_has_the_expected_alphabet_and_is_masked(self) -> None:
        out = io.StringIO()
        with redirect_stdout(out):
            pw, other = ms.new_password(), ms.new_password()
        self.assertRegex(pw, ms.PASSWORD_ALPHABET)
        self.assertIn(f"::add-mask::{pw}", out.getvalue())
        self.assertNotEqual(pw, other)
        self.assertEqual(driver.redact(f"value {pw} end"), "value [redacted] end")


class WorkflowBindingTests(unittest.TestCase):
    def definition(self) -> dict:
        files, mpins, wpins = fake_pins()
        return json.loads(files[next(iter(wpins))])

    def test_every_postgres_node_is_bound_to_the_staging_credential_and_enabled(self) -> None:
        body = ms.bind_postgres(self.definition(), "CRED123")
        node = next(n for n in body["nodes"] if n["type"] == "n8n-nodes-base.postgres")
        self.assertEqual(node["credentials"], {"postgres": {"id": "CRED123", "name": ms.CREDENTIAL_NAME}})
        self.assertNotIn("disabled", node)
        self.assertEqual(set(body), {"name", "nodes", "connections", "settings"})
        self.assertNotIn("active", body)


class FakeN8n:
    """Just enough of the n8n public API for import and readback."""

    def __init__(self, workflows: list[dict]) -> None:
        self.workflows = {w["id"]: w for w in workflows}
        self.executions: dict[str, list] = {}
        self.calls: list[tuple[str, str]] = []

    def request(self, method, path, body=None, query=None):
        self.calls.append((method, path))
        if method == "GET" and path == "/workflows":
            return 200, {"data": [{"id": i, "name": w["name"], "active": w.get("active", False)} for i, w in self.workflows.items()], "nextCursor": None}
        if method == "GET" and path.startswith("/workflows/"):
            return 200, self.workflows[path.rsplit("/", 1)[1]]
        if method == "GET" and path == "/executions":
            return 200, {"data": self.executions.get(query["workflowId"], [])}
        if method == "POST" and path == "/workflows":
            workflow_id = f"NEW{len(self.workflows):04d}"
            self.workflows[workflow_id] = {**body, "id": workflow_id, "active": False}
            return 200, self.workflows[workflow_id]
        if method == "PUT" and path.startswith("/workflows/"):
            workflow_id = path.rsplit("/", 1)[1]
            self.workflows[workflow_id] = {**body, "id": workflow_id, "active": self.workflows[workflow_id].get("active", False)}
            return 200, self.workflows[workflow_id]
        raise AssertionError(f"unscripted {method} {path}")


class ImportTests(unittest.TestCase):
    def setUp(self) -> None:
        files, mpins, wpins = fake_pins()
        self.workflows = {path: json.loads(files[path]) for path in wpins}

    def test_creates_inactive_workflows_bound_to_the_credential(self) -> None:
        client = FakeN8n([])
        with redirect_stdout(io.StringIO()):
            ids = ms.import_workflows(client, self.workflows, "CRED1")
        self.assertEqual(len(ids), 6)
        for workflow in client.workflows.values():
            self.assertFalse(workflow["active"])
            for node in workflow["nodes"]:
                if node["type"] == "n8n-nodes-base.postgres":
                    self.assertEqual(node["credentials"]["postgres"]["id"], "CRED1")

    def test_replaces_the_one_that_was_imported_with_the_wrong_credential(self) -> None:
        first = next(iter(self.workflows.values()))
        wrong = {"id": "91uUwiZrnky3ArZf", "name": first["name"], "active": False, "nodes": [
            {"name": "Q", "type": "n8n-nodes-base.postgres", "disabled": True, "credentials": {"postgres": {"id": "PRODUCTION", "name": "adapteng_ops Postgres (internal)"}}}]}
        client = FakeN8n([wrong])
        with redirect_stdout(io.StringIO()):
            ms.import_workflows(client, self.workflows, "CRED1")
        fixed = client.workflows["91uUwiZrnky3ArZf"]
        self.assertEqual(fixed["nodes"][1]["credentials"]["postgres"]["id"], "CRED1")
        self.assertNotIn("PRODUCTION", json.dumps(fixed))
        self.assertEqual(len(client.workflows), 6)

    def test_refuses_active_workflows_executed_workflows_and_duplicate_names(self) -> None:
        first = next(iter(self.workflows.values()))
        for existing, executions, message in (
            ([{"id": "A1", "name": first["name"], "active": True, "nodes": []}], None, "is active"),
            ([{"id": "A1", "name": first["name"], "active": False, "nodes": []}], [{"id": 1}], "has executions"),
            ([{"id": "A1", "name": first["name"], "active": False, "nodes": []}, {"id": "A2", "name": first["name"], "active": False, "nodes": []}], None, "refusing to guess"),
        ):
            client = FakeN8n(existing)
            if executions:
                client.executions["A1"] = executions
            with self.subTest(message=message), redirect_stdout(io.StringIO()), self.assertRaisesRegex(ms.Refused, message):
                ms.import_workflows(client, self.workflows, "CRED1")

    def test_readback_accepts_the_staging_state_and_rejects_everything_else(self) -> None:
        client = FakeN8n([])
        with redirect_stdout(io.StringIO()):
            ms.import_workflows(client, self.workflows, "CRED1")
            self.assertEqual(ms.readback(client, self.workflows), "CRED1")
            changes = {
                "active": lambda w: w.update(active=True),
                "wrong credential": lambda w: w["nodes"][1]["credentials"]["postgres"].update(name="adapteng_ops Postgres (internal)"),
                "disabled node": lambda w: w["nodes"][1].update(disabled=True),
                "extra trigger": lambda w: w["nodes"].append({"name": "T", "type": "n8n-nodes-base.scheduleTrigger", "parameters": {}}),
            }
            for label, change in changes.items():
                fresh = FakeN8n([])
                ms.import_workflows(fresh, self.workflows, "CRED1")
                change(next(iter(fresh.workflows.values())))
                with self.subTest(label=label), self.assertRaises(ms.Refused):
                    ms.readback(fresh, self.workflows)

    def test_readback_refuses_when_another_workflow_uses_the_same_credential(self) -> None:
        client = FakeN8n([{"id": "OTHER1", "name": "AUT-001", "active": True, "nodes": [
            {"name": "P", "type": "n8n-nodes-base.postgres", "credentials": {"postgres": {"id": "CRED1", "name": "x"}}}]}])
        with redirect_stdout(io.StringIO()):
            ms.import_workflows(client, self.workflows, "CRED1")
            with self.assertRaisesRegex(ms.Refused, "also used by a workflow that is not a staging one"):
                ms.readback(client, self.workflows)

    def test_a_workflow_that_has_run_fails_the_readback(self) -> None:
        client = FakeN8n([])
        with redirect_stdout(io.StringIO()):
            ms.import_workflows(client, self.workflows, "CRED1")
            client.executions[next(iter(client.workflows))] = [{"id": 1}]
            with self.assertRaisesRegex(ms.Refused, "has executions"):
                ms.readback(client, self.workflows)


class ImportOperationTests(unittest.TestCase):
    def setUp(self) -> None:
        files, mpins, wpins = fake_pins()
        for patch in (mock.patch.object(ms, "MIGRATION_PINS", mpins), mock.patch.object(ms, "WORKFLOW_PINS", wpins)):
            patch.start()
            self.addCleanup(patch.stop)
        self.fetch = lambda path: files[path]
        self.self_hosted = {"id": "NsWG1hD8VmIRRwCv", "name": "AUT-001", "active": True, "nodes": []}

    def test_malformed_credential_ids_are_refused_before_anything_is_read(self) -> None:
        client = FakeN8n([self.self_hosted])
        for bad in ("", "short", "x" * 40, "abc def ghi", "id;DROP", "../etc/passwd"):
            with self.subTest(bad=bad), redirect_stdout(io.StringIO()), self.assertRaises(ms.Refused):
                ms.op_import_workflows(bad, fetch=self.fetch, n8n_client=client)
        self.assertEqual(client.calls, [])

    def test_imports_inactive_workflows_bound_to_the_given_id_and_needs_no_ssh(self) -> None:
        client = FakeN8n([self.self_hosted])
        with redirect_stdout(io.StringIO()):
            self.assertEqual(ms.op_import_workflows("StagingCred01", fetch=self.fetch, n8n_client=client), driver.EXIT_OK)
        staging = [w for w in client.workflows.values() if w["name"].startswith("STG ")]
        self.assertEqual(len(staging), 6)
        self.assertTrue(all(not w["active"] for w in staging))

    def test_refuses_an_id_that_a_non_staging_workflow_already_uses(self) -> None:
        production = {"id": "OTHER9999", "name": "WEB-002", "active": True, "nodes": [
            {"name": "P", "type": "n8n-nodes-base.postgres", "credentials": {"postgres": {"id": "ProdCred0001", "name": "adapteng_ops Postgres (internal)"}}}]}
        client = FakeN8n([self.self_hosted, production])
        with redirect_stdout(io.StringIO()), self.assertRaisesRegex(ms.Refused, "not a staging one"):
            ms.op_import_workflows("ProdCred0001", fetch=self.fetch, n8n_client=client)
        self.assertFalse(any(call[0] in ("POST", "PUT") for call in client.calls))

    def test_refuses_an_instance_that_is_not_the_self_hosted_one(self) -> None:
        cloud = {"id": n8n_deploy_cloud_marker(), "name": "MM-08", "active": True, "nodes": []}
        client = FakeN8n([cloud])
        with redirect_stdout(io.StringIO()), self.assertRaisesRegex(ms.Refused, "self-hosted"):
            ms.op_import_workflows("StagingCred01", fetch=self.fetch, n8n_client=client)


def n8n_deploy_cloud_marker() -> str:
    return ms.n8n_deploy.KNOWN_N8N_CLOUD_WORKFLOW_ID


class WorkflowFileTests(unittest.TestCase):
    def setUp(self) -> None:
        self.text = WORKFLOW.read_text(encoding="utf-8")

    def test_manual_dispatch_only_and_three_inputs(self) -> None:
        self.assertIn("workflow_dispatch:", self.text)
        for trigger in ("schedule:", "push:", "pull_request:", "workflow_run:"):
            self.assertNotIn(trigger, self.text)
        inputs = re.findall(r"^      (\w+):\n        description:", self.text, re.M)
        self.assertEqual(inputs, ["operation", "confirm", "credential_id"])
        options = re.search(r"options:\n((?:\s+- [\w-]+\n)+)", self.text)
        self.assertEqual(re.findall(r"- ([\w-]+)", options.group(1)), list(ms.OPERATIONS))

    def test_no_dispatch_input_is_interpolated_into_a_shell_command(self) -> None:
        for block in re.findall(r"run: \|?\n?((?:\s{10,}.*\n)+)", self.text):
            self.assertNotIn("inputs.", block)

    def test_only_main_may_run_it_and_writes_are_gated_by_an_environment(self) -> None:
        self.assertEqual(self.text.count("github.ref == 'refs/heads/main'"), 2)
        self.assertIn("environment: marketing-staging-provision", self.text)
        write = self.text[self.text.index("  write:"):]
        read = self.text[self.text.index("  read:"):self.text.index("  write:")]
        self.assertNotIn("environment:", read)
        self.assertIn("environment: marketing-staging-provision", write)

    def test_it_uses_no_arbitrary_sql_or_script_path(self) -> None:
        self.assertNotRegex(self.text, r"(?i)psql|\bsql\b:|--file|-f ")
        self.assertIn("python scripts/marketing_staging.py", self.text)

    def test_contents_read_only_and_serialised(self) -> None:
        self.assertIn("contents: read", self.text)
        self.assertNotIn("contents: write", self.text)
        self.assertIn("group: marketing-staging-provision", self.text)
        self.assertIn("cancel-in-progress: false", self.text)

    def test_the_secrets_it_needs_are_the_existing_ones(self) -> None:
        for name in ("PROD_SSH_PRIVATE_KEY", "PROD_SSH_KNOWN_HOSTS", "ADAPTENG_GITHUB_READ_TOKEN", "COOLIFY_API_TOKEN", "X_N8N_API_KEY"):
            self.assertIn(f"secrets.{name}", self.text)
        found = set(re.findall(r"secrets\.([A-Z0-9_]+)", self.text))
        self.assertLessEqual(found, {"PROD_SSH_PRIVATE_KEY", "PROD_SSH_KNOWN_HOSTS", "ADAPTENG_GITHUB_READ_TOKEN", "COOLIFY_API_TOKEN", "X_N8N_API_KEY"})


PG_CONTAINER = os.environ.get("MARKETING_STAGING_PG_CONTAINER")
CONFIRMED = os.environ.get("FIXED_MIGRATION_TEST_CONFIRM_DISPOSABLE") == "yes"


@unittest.skipUnless(PG_CONTAINER and CONFIRMED, "no disposable Postgres container confirmed")
class PostgresIsolationTests(unittest.TestCase):
    """Run the real SQL against a disposable PostgreSQL 16 holding fake production objects."""

    @staticmethod
    def psql(sql: str, user: str = "postgres", db: str = "adapteng_ops", check: bool = True) -> subprocess.CompletedProcess:
        result = subprocess.run(
            ["docker", "exec", "-i", PG_CONTAINER, "psql", "-v", "ON_ERROR_STOP=1", "--no-psqlrc", "-q", "-At", "-F", "|", "-U", user, "-d", db, "-f", "-"],
            input=sql, capture_output=True, text=True, timeout=120,
        )
        if check and result.returncode != 0:
            raise AssertionError(result.stderr)
        return result

    @classmethod
    def migrations(cls) -> dict[str, str]:
        base = Path(os.environ.get("MARKETING_STAGING_MIGRATIONS", "C:/GitHub/adapteng-automation-platform"))
        return {path: (base / path).read_text(encoding="utf-8") for path in ms.MIGRATION_PINS}

    def reset(self) -> None:
        self.psql("""
            DROP SCHEMA IF EXISTS marketing_staging CASCADE;
            DROP ROLE IF EXISTS marketing_staging_app;
            DROP SCHEMA IF EXISTS adapteng_ops CASCADE;
            DROP TABLE IF EXISTS public.lead_identity CASCADE;
            DROP FUNCTION IF EXISTS public.open_door();
            CREATE SCHEMA adapteng_ops;
            CREATE TABLE adapteng_ops.agent_run (run_id text primary key, status text);
            INSERT INTO adapteng_ops.agent_run VALUES ('r1', 'started');
            CREATE TABLE public.lead_identity (lead_id text primary key, email text);
            INSERT INTO public.lead_identity VALUES ('L1', 'x@example.test');
            REVOKE ALL ON public.lead_identity FROM PUBLIC;
        """)

    def counts(self) -> str:
        return self.psql(ms._OBJECT_COUNTS + " ORDER BY 1;").stdout

    def test_provision_creates_only_the_schema_and_role_and_the_proof_passes(self) -> None:
        self.reset()
        before = self.counts()
        sql = ms.provision_sql(self.migrations())
        result = self.psql(sql)
        self.assertEqual(self.counts(), before, "protected schemas changed")
        state = ms.parse_pairs(self.psql(ms.STATE_SQL).stdout)
        self.assertEqual(ms.classify(state), "provisioned-no-password")
        proof = ms.parse_pairs(self.psql(ms.VERIFY_SQL).stdout)
        for key in ("outside_relations_reachable", "outside_sequences_usable", "outside_schemas_creatable", "outside_secdef_executable", "inside_tables_deletable"):
            self.assertEqual(proof[key], "0", key)
        self.assertEqual(proof["database_create"], "false")
        self.assertEqual((proof["approvals_updatable"], proof["config_writable"]), ("false", "false"))
        self.assertEqual(proof["inside_tables_writable"], str(ms.EXPECTED_TABLES))
        self.assertNotIn("PASSWORD", result.stdout + result.stderr)

    def test_a_second_run_is_refused_by_the_transaction_and_changes_nothing(self) -> None:
        self.reset()
        sql = ms.provision_sql(self.migrations())
        self.psql(sql)
        again = self.psql(sql, check=False)
        self.assertNotEqual(again.returncode, 0)
        self.assertEqual(ms.classify(ms.parse_pairs(self.psql(ms.STATE_SQL).stdout)), "provisioned-no-password")

    def test_the_role_works_with_its_password_and_is_denied_everywhere_else(self) -> None:
        self.reset()
        self.psql(ms.provision_sql(self.migrations()))
        pw = "Aa0Bb1Cc2Dd3Ee4Ff5Gg6Hh7Ii8Jj9Kk0Ll1Mm2Nn3Oo4"
        self.psql(ms.SET_PASSWORD_SQL.replace("{pw}", pw))
        self.assertEqual(ms.classify(ms.parse_pairs(self.psql(ms.STATE_SQL).stdout)), "exact")
        network = os.environ.get("MARKETING_STAGING_PG_NETWORK", "")
        host = os.environ.get("MARKETING_STAGING_PG_HOST", "")
        if not network or not host:
            self.skipTest("the authenticated client check needs MARKETING_STAGING_PG_NETWORK and MARKETING_STAGING_PG_HOST")

        def as_role(sql: str) -> subprocess.CompletedProcess:
            return subprocess.run(
                ["docker", "run", "--rm", "-i", "--network", network, "-e", f"PGPASSWORD={pw}", "postgres:16-alpine",
                 "psql", "-v", "ON_ERROR_STOP=1", "-h", host, "-U", ms.ROLE, "-d", ms.DATABASE, "-At", "-q", "-f", "-"],
                input=sql, capture_output=True, text=True, timeout=120,
            )

        self.assertIn("marketing_staging", as_role("SHOW search_path;").stdout)
        self.assertEqual(as_role("INSERT INTO marketing_publication_radar (dedupe_key, pub_id, is_synthetic) VALUES ('doi:10.0/x', 'STG_X_1', true); SELECT count(*) FROM marketing_publication_radar;").stdout.strip(), "1")
        for denied in ("SELECT * FROM public.lead_identity;", "SELECT * FROM adapteng_ops.agent_run;", "CREATE TABLE public.x (i int);",
                       "CREATE SCHEMA evil;", "DELETE FROM marketing_publication_radar;", "UPDATE marketing_publication_approvals SET decision = 'approve';",
                       "SET ROLE postgres;", "ALTER ROLE marketing_staging_app SUPERUSER;"):
            self.assertNotEqual(as_role(denied).returncode, 0, denied)

    def test_a_migration_that_escapes_through_dynamic_sql_is_caught_and_rolled_back(self) -> None:
        self.reset()
        before = self.counts()
        leak = "DO $leak$ BEGIN EXECUTE 'CREATE TABLE ' || 'pub' || 'lic.leaked (i int)'; END $leak$;\n"
        migrations = self.migrations()
        bodies = "\n".join(ms.migration_body(path, migrations[path]) for path in ms.MIGRATION_PINS)
        sql = ms.PROVISION_HEAD + bodies + "\n" + leak + ms.PROVISION_TAIL
        result = self.psql(sql, check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("isolation: public changed", result.stderr)
        self.assertEqual(self.counts(), before)
        self.assertEqual(ms.classify(ms.parse_pairs(self.psql(ms.STATE_SQL).stdout)), "absent")

    def test_a_production_table_readable_by_public_stops_the_transaction(self) -> None:
        self.reset()
        self.psql("GRANT SELECT ON public.lead_identity TO PUBLIC;")
        result = self.psql(ms.provision_sql(self.migrations()), check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("the role can reach public.lead_identity", result.stderr)
        self.assertEqual(ms.classify(ms.parse_pairs(self.psql(ms.STATE_SQL).stdout)), "absent")

    def test_a_public_security_definer_function_stops_the_transaction(self) -> None:
        self.reset()
        self.psql("CREATE FUNCTION public.open_door() RETURNS int LANGUAGE sql SECURITY DEFINER AS 'SELECT 1';")
        result = self.psql(ms.provision_sql(self.migrations()), check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("SECURITY DEFINER function public.open_door", result.stderr)
        self.assertEqual(ms.classify(ms.parse_pairs(self.psql(ms.STATE_SQL).stdout)), "absent")

    def test_owner_sql_runs_and_prints_the_server_made_password_once(self) -> None:
        self.reset()
        result = self.psql(ms.owner_sql(self.migrations()))
        shown = [line for line in result.stdout.splitlines() if re.fullmatch(r"[0-9a-f]{64}", line.strip())]
        self.assertEqual(len(shown), 1)
        self.assertEqual(ms.classify(ms.parse_pairs(self.psql(ms.STATE_SQL).stdout)), "exact")


if __name__ == "__main__":
    unittest.main()
