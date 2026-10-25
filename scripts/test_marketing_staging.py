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

    def owner(self) -> str:
        return ms.owner_sql({path: text for path, text in zip(ms.MIGRATION_PINS, (MIGRATION_009, MIGRATION_010))})

    def test_owner_sql_is_one_transaction_that_also_stores_the_password(self) -> None:
        text = self.owner()
        self.assertEqual(text.count("BEGIN;"), 1)
        self.assertEqual(text.count("COMMIT;"), 1)
        self.assertEqual(text.count("CREATE SCHEMA"), 1)
        commit = text.index("COMMIT;")
        self.assertLess(text.index("$isolation$;"), text.index("ALTER ROLE %I PASSWORD"))
        self.assertLess(text.index("ALTER ROLE %I PASSWORD"), text.index(ms.APPLIED_MARK))
        self.assertLess(text.index(ms.APPLIED_MARK), commit)
        self.assertNotIn("\\echo :pw", text[:commit])
        self.assertGreater(text.index("\\echo :pw"), commit)
        self.assertEqual(text.count("\\echo :pw"), 1)

    def test_the_password_is_shown_only_when_the_transaction_marked_itself_applied(self) -> None:
        shown = ms.SHOW_PASSWORD_ONCE
        self.assertTrue(shown.startswith("\\if :{?applied}\n"))
        self.assertLess(shown.index("\\echo :pw"), shown.index("\\else"))
        self.assertIn("NOT APPLIED", shown[shown.index("\\else"):])
        self.assertTrue(shown.rstrip().endswith("\\endif"))

    def test_a_replay_is_stopped_by_a_guard_before_anything_is_created(self) -> None:
        for text in (self.sql, self.owner()):
            self.assertLess(text.index("do not replay this script"), text.index("CREATE SCHEMA"))
            self.assertLess(text.index("do not replay this script"), text.index("CREATE ROLE"))

    def test_rotate_script_changes_only_the_password_and_refuses_when_nothing_exists(self) -> None:
        text = ms.owner_rotate_sql()
        self.assertEqual((text.count("BEGIN;"), text.count("COMMIT;")), (1, 1))
        self.assertIn("nothing to rotate", text)
        self.assertLess(text.index("nothing to rotate"), text.index("ALTER ROLE %I PASSWORD"))
        for forbidden in ("CREATE SCHEMA", "CREATE ROLE", "CREATE TABLE", "GRANT", "DROP"):
            self.assertNotIn(forbidden, text)
        self.assertLess(text.index(ms.APPLIED_MARK), text.index("COMMIT;"))
        self.assertGreater(text.index("\\echo :pw"), text.index("COMMIT;"))

    def test_state_script_is_read_only(self) -> None:
        text = ms.owner_state_sql()
        self.assertIn("staging_state: ", text)
        self.assertNotRegex(text, r"(?i)\b(CREATE|ALTER|DROP|GRANT|REVOKE|INSERT|UPDATE|DELETE|TRUNCATE|COMMIT|BEGIN)\b")

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

    def test_the_database_writes_carry_the_no_restore_proof_acknowledgement(self) -> None:
        for operation in ("provision-database", "configure-n8n"):
            self.assertIn(ms.ACKNOWLEDGEMENT, ms.CONFIRMATION[operation])
            with self.assertRaises(ms.Refused):  # the phrase without the acknowledgement is not enough
                ms.require_confirmation(operation, ms.CONFIRMATION[operation].replace(f" {ms.ACKNOWLEDGEMENT}", ""))
        for operation in ("import-workflows", "enable-db-nodes"):
            self.assertNotIn(ms.ACKNOWLEDGEMENT, ms.CONFIRMATION[operation])
        self.assertEqual(set(ms.CONFIRMATION), set(ms.WRITE_OPERATIONS))
        self.assertEqual(len(set(ms.CONFIRMATION.values())), len(ms.CONFIRMATION))

    def test_the_recovery_point_gate_states_what_it_does_not_prove(self) -> None:
        out = io.StringIO()
        with mock.patch.object(driver, "operate_backup_check", return_value=driver.EXIT_OK) as check, redirect_stdout(out):
            ms.recovery_point_gate(object())
        self.assertEqual(check.call_args.kwargs["max_age_hours"], ms.BACKUP_MAX_AGE_HOURS)
        text = out.getvalue()
        self.assertIn("does not show the backup can be restored", text)
        self.assertIn("synthetic B2 rehearsal is not that proof", text)
        self.assertNotRegex(text, r"(?i)restore (is )?(proven|verified|tested)|backup (is )?(good|valid)")

    def test_preflight_reports_that_restore_proof_is_still_an_open_gate(self) -> None:
        fetch = self.fetch()
        out = io.StringIO()
        with mock.patch.object(driver, "operate_backup_check", return_value=driver.EXIT_OK), redirect_stdout(out):
            ms.op_preflight(FakeHost(GOOD_CONTAINERS), fetch=fetch, backup_client=object())
        self.assertIn("restore proof: NOT established", out.getvalue())

    def test_configure_n8n_creates_the_credential_for_the_restricted_role_and_imports_enabled(self) -> None:
        fetch = self.fetch()
        provisioned = "schema|present\nrole|present\nschema_tables|7\nschema_views|1\nrole_attrs_ok|true\nrole_memberships|0\nrole_has_password|false\nrole_search_path|search_path=marketing_staging, pg_temp\n"
        host = FakeHost(GOOD_CONTAINERS, {"state": provisioned})
        self_hosted = {"id": "NsWG1hD8VmIRRwCv", "name": "AUT-001", "active": True, "nodes": []}
        client = FakeN8n([self_hosted])
        secret_value = "Zz0123456789abcdefghijklmnopqrstuvwxyzAB"
        out = io.StringIO()
        with mock.patch.object(driver, "operate_backup_check", return_value=driver.EXIT_OK), redirect_stdout(out):
            code = ms.op_configure_n8n(host, fetch=fetch, backup_client=object(), n8n_client=client, password_factory=lambda: secret_value)
        self.assertEqual(code, driver.EXIT_OK)
        self.assertNotIn(secret_value, out.getvalue())
        created = client.created_credentials
        self.assertEqual(len(created), 1)
        self.assertEqual((created[0]["type"], created[0]["name"]), ("postgres", ms.CREDENTIAL_NAME))
        self.assertEqual((created[0]["data"]["user"], created[0]["data"]["database"]), (ms.ROLE, ms.DATABASE))
        staging = [w for w in client.workflows.values() if w["name"].startswith("STG ")]
        self.assertEqual(len(staging), 6)
        self.assertTrue(all("disabled" not in node for w in staging for node in postgres_nodes(w)))

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


PRODUCTION_CREDENTIAL_NAME = "adapteng_ops Postgres (internal)"
CREDENTIALS = {
    "CRED1": ("postgres", ms.CREDENTIAL_NAME),
    "StagingCred01": ("postgres", ms.CREDENTIAL_NAME),
    "ProdCred0001": ("postgres", PRODUCTION_CREDENTIAL_NAME),
    "WrongName001": ("postgres", "Some other Postgres"),
    "DriveCred001": ("googleDriveOAuth2Api", ms.CREDENTIAL_NAME),
}


class FakeN8n:
    """Just enough of the n8n public API for import and readback.

    Saving a workflow resolves the credential reference of each ENABLED node (a disabled node is
    stored as sent) the way n8n 2.31.5 does (measured against a disposable instance): a known id of the right type has its name
    replaced by the credential's real name; an unknown id whose name belongs to a credential
    is resolved to that credential's id; an unknown id and name are stored as sent.
    """

    def __init__(self, workflows: list[dict], credentials: dict | None = None) -> None:
        self.workflows = {w["id"]: w for w in workflows}
        self.credentials = dict(CREDENTIALS if credentials is None else credentials)
        self.executions: dict[str, list] = {}
        self.calls: list[tuple[str, str]] = []
        self.created_credentials: list[dict] = []

    def resolve(self, body: dict) -> dict:
        body = json.loads(json.dumps(body))
        for node in body.get("nodes", []):
            ref = (node.get("credentials") or {}).get("postgres")
            if not isinstance(ref, dict) or node.get("disabled"):
                continue  # measured: a disabled node is stored as sent, its credential is not resolved
            known = self.credentials.get(ref.get("id"))
            if known and known[0] == "postgres":
                ref["name"] = known[1]
            else:
                by_name = [i for i, (kind, name) in self.credentials.items() if kind == "postgres" and name == ref.get("name")]
                if len(by_name) == 1:
                    ref["id"] = by_name[0]
        return body

    def request(self, method, path, body=None, query=None):
        self.calls.append((method, path))
        if method == "GET" and path == "/workflows":
            return 200, {"data": [{"id": i, "name": w["name"], "active": w.get("active", False)} for i, w in self.workflows.items()], "nextCursor": None}
        if method == "GET" and path.startswith("/workflows/"):
            return 200, self.workflows[path.rsplit("/", 1)[1]]
        if method == "GET" and path == "/executions":
            return 200, {"data": self.executions.get(query["workflowId"], [])}
        if method == "POST" and path == "/credentials":
            credential_id = f"CredNew{len(self.credentials):05d}"
            self.credentials[credential_id] = (body["type"], body["name"])
            self.created_credentials.append(body)
            return 200, {"id": credential_id, "name": body["name"], "type": body["type"]}
        if method == "POST" and path == "/workflows":
            workflow_id = f"NEW{len(self.workflows):04d}"
            self.workflows[workflow_id] = {**self.resolve(body), "id": workflow_id, "active": False}
            return 200, self.workflows[workflow_id]
        if method == "PUT" and path.startswith("/workflows/"):
            workflow_id = path.rsplit("/", 1)[1]
            self.workflows[workflow_id] = {**self.resolve(body), "id": workflow_id, "active": self.workflows[workflow_id].get("active", False)}
            return 200, self.workflows[workflow_id]
        raise AssertionError(f"unscripted {method} {path}")


def postgres_nodes(workflow: dict) -> list[dict]:
    return [n for n in workflow["nodes"] if n["type"] == "n8n-nodes-base.postgres"]


class ImportTests(unittest.TestCase):
    def setUp(self) -> None:
        files, mpins, wpins = fake_pins()
        self.workflows = {path: json.loads(files[path]) for path in wpins}

    def imported(self, db_disabled: bool = False, credential_id: str = "CRED1") -> FakeN8n:
        client = FakeN8n([])
        with redirect_stdout(io.StringIO()):
            ms.import_workflows(client, self.workflows, credential_id, db_disabled=db_disabled)
        return client

    def test_creates_inactive_workflows_bound_to_the_credential(self) -> None:
        client = FakeN8n([])
        with redirect_stdout(io.StringIO()):
            ids = ms.import_workflows(client, self.workflows, "CRED1")
        self.assertEqual(len(ids), 6)
        for workflow in client.workflows.values():
            self.assertFalse(workflow["active"])
            for node in postgres_nodes(workflow):
                self.assertEqual(node["credentials"]["postgres"], {"id": "CRED1", "name": ms.CREDENTIAL_NAME})
                self.assertNotIn("disabled", node)

    def test_the_database_nodes_can_be_imported_disabled(self) -> None:
        client = self.imported(db_disabled=True)
        for workflow in client.workflows.values():
            self.assertTrue(all(node.get("disabled") is True for node in postgres_nodes(workflow)))
            self.assertFalse(any(node.get("disabled") for node in workflow["nodes"] if node not in postgres_nodes(workflow)))

    def test_replaces_the_one_that_was_imported_with_the_wrong_credential(self) -> None:
        first = next(iter(self.workflows.values()))
        wrong = {"id": "91uUwiZrnky3ArZf", "name": first["name"], "active": False, "nodes": [
            {"name": "Q", "type": "n8n-nodes-base.postgres", "disabled": True, "credentials": {"postgres": {"id": "ProdCred0001", "name": PRODUCTION_CREDENTIAL_NAME}}}]}
        client = FakeN8n([wrong])
        with redirect_stdout(io.StringIO()):
            ms.import_workflows(client, self.workflows, "CRED1")
        fixed = client.workflows["91uUwiZrnky3ArZf"]
        self.assertEqual(fixed["nodes"][1]["credentials"]["postgres"]["id"], "CRED1")
        self.assertNotIn("ProdCred0001", json.dumps(fixed))
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

    # --- the credential is identified by n8n, not by the name written in the JSON ---

    def test_an_unknown_id_is_refused_even_when_its_name_is_the_staging_name(self) -> None:
        client = FakeN8n([])
        with redirect_stdout(io.StringIO()), self.assertRaisesRegex(ms.Refused, "no Postgres credential with that id"):
            ms.import_workflows(client, self.workflows, "Unknown00001")
        self.assertEqual(len(client.workflows), 1, "only the probe workflow may exist after a refused identity check")
        for node in postgres_nodes(next(iter(client.workflows.values()))):
            self.assertNotIn("credentials", node)
            self.assertTrue(node["disabled"])

    def test_an_id_that_belongs_to_the_production_credential_is_refused(self) -> None:
        client = FakeN8n([])
        with redirect_stdout(io.StringIO()), self.assertRaisesRegex(ms.Refused, "is named 'adapteng_ops Postgres"):
            ms.import_workflows(client, self.workflows, "ProdCred0001")
        self.assertEqual(len(client.workflows), 1)
        self.assertNotIn("ProdCred0001", json.dumps(client.workflows))

    def test_an_id_with_another_name_or_another_type_is_refused(self) -> None:
        for credential_id, message in (("WrongName001", "is named 'Some other Postgres'"), ("DriveCred001", "no Postgres credential with that id")):
            client = FakeN8n([])
            with self.subTest(credential_id=credential_id), redirect_stdout(io.StringIO()), self.assertRaisesRegex(ms.Refused, message):
                ms.import_workflows(client, self.workflows, credential_id)
            self.assertEqual(len(client.workflows), 1)

    def test_a_refused_identity_check_leaves_an_existing_workflow_with_no_credential_at_all(self) -> None:
        first = next(iter(self.workflows.values()))
        existing = {"id": "A1", "name": first["name"], "active": False, "nodes": [
            {"name": "Q", "type": "n8n-nodes-base.postgres", "credentials": {"postgres": {"id": "ProdCred0001", "name": PRODUCTION_CREDENTIAL_NAME}}}]}
        client = FakeN8n([existing])
        with redirect_stdout(io.StringIO()), self.assertRaises(ms.Refused):
            ms.import_workflows(client, self.workflows, "ProdCred0001")
        self.assertNotIn("ProdCred0001", json.dumps(client.workflows["A1"]))
        self.assertNotIn("credentials", json.dumps(client.workflows["A1"]))

    # --- exact graph and parameters ---

    def test_readback_accepts_the_staging_state_and_returns_what_it_found(self) -> None:
        for disabled in (False, True):
            client = self.imported(db_disabled=disabled)
            with self.subTest(disabled=disabled), redirect_stdout(io.StringIO()):
                self.assertEqual(ms.readback(client, self.workflows), ("CRED1", disabled))
                self.assertEqual(ms.readback(client, self.workflows, "CRED1", disabled), ("CRED1", disabled))

    def test_readback_demands_the_credential_and_the_state_it_was_given(self) -> None:
        client = self.imported(db_disabled=True)
        with redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(ms.Refused, "different credential id"):
                ms.readback(client, self.workflows, "StagingCred01", True)
            with self.assertRaisesRegex(ms.Refused, "disabled, expected enabled"):
                ms.readback(client, self.workflows, "CRED1", False)

    def test_readback_rejects_every_kind_of_drift(self) -> None:
        def first(w):
            return w

        changes = {
            "active": lambda w: w.update(active=True),
            "wrong credential name": lambda w: w["nodes"][1]["credentials"]["postgres"].update(name=PRODUCTION_CREDENTIAL_NAME),
            "wrong credential id": lambda w: w["nodes"][1]["credentials"]["postgres"].update(id="OTHERCRED1"),
            "disabled node": lambda w: w["nodes"][1].update(disabled=True),
            "enabled node when disabled is expected": lambda w: w["nodes"][1].pop("disabled", None),
            "extra trigger": lambda w: w["nodes"].append({"name": "T", "type": "n8n-nodes-base.scheduleTrigger", "parameters": {}}),
            "removed node": lambda w: w["nodes"].pop(0),
            "changed query": lambda w: w["nodes"][1]["parameters"].update(query="select * from adapteng_ops.accounts"),
            "added parameter": lambda w: w["nodes"][1]["parameters"].update(options={"queryBatching": "single"}),
            "changed node type": lambda w: w["nodes"][0].update(type="n8n-nodes-base.scheduleTrigger"),
            "changed position": lambda w: w["nodes"][0].update(position=[1, 1]),
            "added connection": lambda w: w["connections"].update({"Manual Trigger": {"main": [[{"node": "Q", "type": "main", "index": 0}]]}}),
            "changed setting": lambda w: w["settings"].update(executionOrder="v0"),
        }
        for label, change in changes.items():
            disabled = label == "enabled node when disabled is expected"
            fresh = self.imported(db_disabled=disabled)
            change(next(iter(fresh.workflows.values())))
            with self.subTest(label=label), redirect_stdout(io.StringIO()), self.assertRaises(ms.Refused):
                ms.readback(fresh, self.workflows)

    def test_readback_rejects_a_second_credential_across_workflows(self) -> None:
        client = self.imported()
        second = list(client.workflows.values())[1]
        second["nodes"][1]["credentials"]["postgres"]["id"] = "CRED2"
        with redirect_stdout(io.StringIO()), self.assertRaisesRegex(ms.Refused, "2 different Postgres credentials"):
            ms.readback(client, self.workflows)

    def test_readback_rejects_a_mixed_enabled_and_disabled_state(self) -> None:
        client = self.imported()
        list(client.workflows.values())[2]["nodes"][1]["disabled"] = True
        with redirect_stdout(io.StringIO()), self.assertRaisesRegex(ms.Refused, "not all in the same enabled or disabled state"):
            ms.readback(client, self.workflows)

    def test_readback_refuses_when_another_workflow_uses_the_same_credential(self) -> None:
        client = FakeN8n([{"id": "OTHER1", "name": "AUT-001", "active": True, "nodes": [
            {"name": "P", "type": "n8n-nodes-base.postgres", "credentials": {"postgres": {"id": "CRED1", "name": "x"}}}]}])
        with redirect_stdout(io.StringIO()):
            ms.import_workflows(client, self.workflows, "CRED1")
            with self.assertRaisesRegex(ms.Refused, "also used by a workflow that is not a staging one"):
                ms.readback(client, self.workflows)

    def test_a_workflow_that_has_run_fails_the_readback(self) -> None:
        client = self.imported()
        client.executions[next(iter(client.workflows))] = [{"id": 1}]
        with redirect_stdout(io.StringIO()), self.assertRaisesRegex(ms.Refused, "has executions"):
            ms.readback(client, self.workflows)


class ImportOperationTests(unittest.TestCase):
    def setUp(self) -> None:
        files, mpins, wpins = fake_pins()
        for patch in (mock.patch.object(ms, "MIGRATION_PINS", mpins), mock.patch.object(ms, "WORKFLOW_PINS", wpins)):
            patch.start()
            self.addCleanup(patch.stop)
        self.fetch = lambda path: files[path]
        self.self_hosted = {"id": "NsWG1hD8VmIRRwCv", "name": "AUT-001", "active": True, "nodes": []}

    def staging(self, client: FakeN8n) -> list[dict]:
        return [w for w in client.workflows.values() if w["name"].startswith("STG ")]

    def test_malformed_credential_ids_are_refused_before_anything_is_read(self) -> None:
        client = FakeN8n([self.self_hosted])
        for operation in (ms.op_import_workflows, ms.op_enable_db_nodes):
            for bad in ("", "short", "x" * 40, "abc def ghi", "id;DROP", "../etc/passwd"):
                with self.subTest(operation=operation.__name__, bad=bad), redirect_stdout(io.StringIO()), self.assertRaises(ms.Refused):
                    operation(bad, fetch=self.fetch, n8n_client=client)
        self.assertEqual(client.calls, [])

    def test_import_leaves_the_database_nodes_disabled_and_needs_no_ssh(self) -> None:
        client = FakeN8n([self.self_hosted])
        out = io.StringIO()
        with redirect_stdout(out):
            self.assertEqual(ms.op_import_workflows("StagingCred01", fetch=self.fetch, n8n_client=client), driver.EXIT_OK)
        staging = self.staging(client)
        self.assertEqual(len(staging), 6)
        self.assertTrue(all(not w["active"] for w in staging))
        self.assertTrue(all(node.get("disabled") is True for w in staging for node in postgres_nodes(w)))
        self.assertIn("NOT yet usable", out.getvalue())
        self.assertIn("enable-db-nodes", out.getvalue())

    def test_enable_needs_a_prior_disabled_import_and_then_enables_exactly_the_database_nodes(self) -> None:
        client = FakeN8n([self.self_hosted])
        with redirect_stdout(io.StringIO()):
            with self.assertRaises(ms.Refused):  # nothing imported yet
                ms.op_enable_db_nodes("StagingCred01", fetch=self.fetch, n8n_client=client)
            ms.op_import_workflows("StagingCred01", fetch=self.fetch, n8n_client=client)
            self.assertEqual(ms.op_enable_db_nodes("StagingCred01", fetch=self.fetch, n8n_client=client), driver.EXIT_OK)
        staging = self.staging(client)
        self.assertEqual(len(staging), 6)
        self.assertTrue(all(not w["active"] for w in staging))
        self.assertTrue(all("disabled" not in node for w in staging for node in postgres_nodes(w)))

    def test_enable_refuses_a_different_id_than_the_one_imported(self) -> None:
        client = FakeN8n([self.self_hosted])
        with redirect_stdout(io.StringIO()):
            ms.op_import_workflows("StagingCred01", fetch=self.fetch, n8n_client=client)
            writes = len([c for c in client.calls if c[0] in ("POST", "PUT")])
            with self.assertRaisesRegex(ms.Refused, "different credential id"):
                ms.op_enable_db_nodes("WrongName001", fetch=self.fetch, n8n_client=client)
        self.assertEqual(len([c for c in client.calls if c[0] in ("POST", "PUT")]), writes)

    def test_enable_refuses_drifted_workflows_and_changes_nothing(self) -> None:
        client = FakeN8n([self.self_hosted])
        with redirect_stdout(io.StringIO()):
            ms.op_import_workflows("StagingCred01", fetch=self.fetch, n8n_client=client)
            list(self.staging(client))[0]["nodes"][1]["parameters"]["query"] = "select 2"
            writes = len([c for c in client.calls if c[0] in ("POST", "PUT")])
            with self.assertRaisesRegex(ms.Refused, "differs from the pinned definition"):
                ms.op_enable_db_nodes("StagingCred01", fetch=self.fetch, n8n_client=client)
        self.assertEqual(len([c for c in client.calls if c[0] in ("POST", "PUT")]), writes)

    def test_refuses_an_id_that_a_non_staging_workflow_already_uses(self) -> None:
        production = {"id": "OTHER9999", "name": "WEB-002", "active": True, "nodes": [
            {"name": "P", "type": "n8n-nodes-base.postgres", "credentials": {"postgres": {"id": "ProdCred0001", "name": PRODUCTION_CREDENTIAL_NAME}}}]}
        client = FakeN8n([self.self_hosted, production])
        with redirect_stdout(io.StringIO()), self.assertRaisesRegex(ms.Refused, "not a staging one"):
            ms.op_import_workflows("ProdCred0001", fetch=self.fetch, n8n_client=client)
        self.assertFalse(any(call[0] in ("POST", "PUT") for call in client.calls))

    def test_refuses_an_id_that_n8n_does_not_know_before_creating_more_than_the_probe(self) -> None:
        client = FakeN8n([self.self_hosted])
        with redirect_stdout(io.StringIO()), self.assertRaisesRegex(ms.Refused, "credential identity check failed"):
            ms.op_import_workflows("Unknown00001", fetch=self.fetch, n8n_client=client)
        self.assertEqual(len(self.staging(client)), 1)

    def test_refuses_an_instance_that_is_not_the_self_hosted_one(self) -> None:
        cloud = {"id": n8n_deploy_cloud_marker(), "name": "MM-08", "active": True, "nodes": []}
        for operation in (ms.op_import_workflows, ms.op_enable_db_nodes):
            client = FakeN8n([cloud])
            with self.subTest(operation=operation.__name__), redirect_stdout(io.StringIO()), self.assertRaisesRegex(ms.Refused, "self-hosted"):
                operation("StagingCred01", fetch=self.fetch, n8n_client=client)
            self.assertFalse(any(call[0] in ("POST", "PUT") for call in client.calls))


def n8n_deploy_cloud_marker() -> str:
    return ms.n8n_deploy.KNOWN_N8N_CLOUD_WORKFLOW_ID


class OwnerScriptFileTests(unittest.TestCase):
    """The local command that writes the SQL the owner pastes into the Coolify terminal."""

    def test_a_path_inside_the_checkout_is_refused(self) -> None:
        for inside in (ROOT / "x.sql", ROOT / "scripts" / "x.sql", ROOT):
            with self.subTest(path=str(inside)), self.assertRaisesRegex(ms.Refused, "outside the git checkout"):
                ms.outside_checkout(str(inside))
        with self.assertRaisesRegex(ms.Refused, "--out is required"):
            ms.outside_checkout("")

    def test_a_path_outside_the_checkout_is_accepted(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "marketing_staging_provision.sql"
            self.assertEqual(ms.outside_checkout(str(target)), target.resolve())

    def test_run_writes_each_script_with_unix_line_endings_and_no_secret(self) -> None:
        import tempfile

        migrations = {path: text for path, text in zip(ms.MIGRATION_PINS, (MIGRATION_009, MIGRATION_010))}
        with tempfile.TemporaryDirectory() as folder, mock.patch.object(ms, "load_pinned", return_value=(migrations, {})):
            for operation, marker in (("owner-sql", "CREATE SCHEMA"), ("owner-sql-rotate", "nothing to rotate"), ("owner-sql-state", "staging_state: ")):
                target = Path(folder) / f"{operation}.sql"
                with redirect_stdout(io.StringIO()):
                    self.assertEqual(ms.run(["--operation", operation, "--out", str(target)]), driver.EXIT_OK)
                data = target.read_bytes()
                self.assertNotIn(b"\r", data)
                self.assertIn(marker, data.decode("utf-8"))
                self.assertNotRegex(data.decode("utf-8"), r"[0-9a-f]{64}")

    def test_run_refuses_a_path_inside_the_checkout_and_writes_nothing(self) -> None:
        target = ROOT / "should_never_exist.sql"
        out = io.StringIO()
        with redirect_stdout(out):
            code = ms.run(["--operation", "owner-sql-state", "--out", str(target)])
        self.assertNotEqual(code, driver.EXIT_OK)
        self.assertFalse(target.exists())

    def test_owner_scripts_are_not_dispatchable_operations(self) -> None:
        self.assertFalse(set(ms.OWNER_SCRIPTS) & set(ms.OPERATIONS))
        self.assertNotRegex(WORKFLOW.read_text(encoding="utf-8"), "owner-sql")


class RunbookTests(unittest.TestCase):
    def setUp(self) -> None:
        self.text = (ROOT / "runbooks" / "marketing-staging-provision.md").read_text(encoding="utf-8")

    def test_the_file_command_is_valid_in_windows_powershell(self) -> None:
        self.assertNotIn("%USERPROFILE%", self.text)  # that is cmd.exe syntax and is a literal in PowerShell
        commands = re.findall(r"^python scripts/marketing_staging\.py --operation owner-sql[\w-]* .*$", self.text, re.M)
        self.assertEqual(len(commands), 3)
        for command in commands:
            self.assertIn('--out "$env:USERPROFILE\\', command)

    def test_every_command_in_the_runbook_names_a_real_operation(self) -> None:
        named = set(re.findall(r"--operation ([\w-]+)", self.text))
        self.assertTrue({"owner-sql", "owner-sql-state", "owner-sql-rotate"} <= named)
        self.assertLessEqual(named, set(ms.OPERATIONS) | set(ms.OWNER_SCRIPTS))

    def test_the_exact_confirmation_phrases_are_in_the_runbook(self) -> None:
        for phrase in ms.CONFIRMATION.values():
            self.assertIn(phrase, self.text)

    def test_it_separates_a_recovery_point_from_a_restore_proof(self) -> None:
        self.assertRegex(self.text, r"(?i)recovery point")
        self.assertRegex(self.text, r"(?i)restore proof")
        self.assertIn("synthetic", self.text)
        self.assertNotRegex(self.text, r"(?i)\bbackup (is |was )?(verified|proven|restorable)\b")

    def test_it_documents_partial_state_replay_and_a_rollback_for_each_step(self) -> None:
        for phrase in ("owner-sql-state", "provisioned-no-password", "drifted", "owner-sql-rotate", "Rollback", "disabled", "enable-db-nodes", "NOT APPLIED"):
            self.assertIn(phrase, self.text, phrase)
        self.assertRegex(self.text, r"(?i)do not (run|paste|replay)")

    def test_no_secret_shaped_value_is_in_the_runbook(self) -> None:
        self.assertNotRegex(self.text, r"(?i)(password|passwd|secret|token)\s*[:=]\s*\S{12,}")
        self.assertNotRegex(self.text, r"\b[0-9a-f]{64}\b")


class WorkflowFileTests(unittest.TestCase):
    def setUp(self) -> None:
        self.text = WORKFLOW.read_text(encoding="utf-8")

    def test_every_write_is_behind_the_environment_and_every_read_is_not(self) -> None:
        write_if = re.search(r"  write:\n(?:.*\n)*?    if: (.*)\n", self.text).group(1)
        read_if = re.search(r"  read:\n(?:.*\n)*?    if: (.*)\n", self.text).group(1)
        self.assertEqual(set(re.findall(r"inputs\.operation == '([\w-]+)'", write_if)), set(ms.WRITE_OPERATIONS))
        self.assertEqual(set(re.findall(r"inputs\.operation == '([\w-]+)'", read_if)), set(ms.OPERATIONS) - set(ms.WRITE_OPERATIONS))

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

    @staticmethod
    def shown_passwords(result: subprocess.CompletedProcess) -> list[str]:
        return [line for line in result.stdout.splitlines() if re.fullmatch(r"[0-9a-f]{64}", line.strip())]

    def paste(self, sql: str) -> subprocess.CompletedProcess:
        """Emulate pasting into the Coolify terminal: psql carries on after an error.

        An interactive psql does not stop a pasted script on an error, so the script's own
        `\\set ON_ERROR_STOP on` line is removed and the statements after the failing one are
        still executed (and rejected, because the transaction is aborted).
        """

        sql = sql.replace("\\set ON_ERROR_STOP on\n", "")
        return subprocess.run(
            ["docker", "exec", "-i", PG_CONTAINER, "psql", "--no-psqlrc", "-q", "-At", "-F", "|", "-U", "postgres", "-d", "adapteng_ops", "-f", "-"],
            input=sql, capture_output=True, text=True, timeout=120,
        )

    def verdict(self) -> str:
        return ms.classify(ms.parse_pairs(self.psql(ms.STATE_SQL).stdout))

    def stored_verifier(self) -> str:
        return self.psql("SELECT coalesce(rolpassword, '') FROM pg_authid WHERE rolname = 'marketing_staging_app';").stdout.strip()

    def script_verdict(self) -> str:
        output = self.psql(ms.owner_state_sql()).stdout
        match = re.search(r"staging_state: (\S+)", output)
        self.assertIsNotNone(match, output)
        return match.group(1)

    def test_owner_sql_runs_and_prints_the_server_made_password_once(self) -> None:
        self.reset()
        result = self.psql(ms.owner_sql(self.migrations()))
        self.assertEqual(len(self.shown_passwords(result)), 1)
        self.assertEqual(self.verdict(), "exact")
        self.assertTrue(self.stored_verifier().startswith("SCRAM-SHA-256$"))

    def test_the_printed_password_is_the_one_the_role_actually_has(self) -> None:
        self.reset()
        shown = self.shown_passwords(self.psql(ms.owner_sql(self.migrations())))[0]
        # Verify without a client round trip: recompute the SCRAM stored key from the stored verifier's salt.
        verifier = self.stored_verifier()
        import base64, hashlib, hmac

        _, rest = verifier.split("$", 1)
        iterations, salt_and_keys = rest.split(":", 1)
        salt, keys = salt_and_keys.split("$", 1)
        stored_key = keys.split(":", 1)[0]
        salted = hashlib.pbkdf2_hmac("sha256", shown.encode(), base64.b64decode(salt), int(iterations))
        client_key = hmac.new(salted, b"Client Key", hashlib.sha256).digest()
        self.assertEqual(base64.b64encode(hashlib.sha256(client_key).digest()).decode(), stored_key)

    def test_a_failure_in_the_one_transaction_leaves_no_role_no_schema_and_no_password(self) -> None:
        self.reset()
        self.psql("GRANT SELECT ON public.lead_identity TO PUBLIC;")
        for label, runner in (("piped with ON_ERROR_STOP", lambda sql: self.psql(sql, check=False)), ("pasted", self.paste)):
            with self.subTest(runner=label):
                result = runner(ms.owner_sql(self.migrations()))
                self.assertEqual(self.shown_passwords(result), [])
                self.assertEqual(self.verdict(), "absent")
                self.assertEqual(self.stored_verifier(), "")
        self.assertIn("NOT APPLIED", self.paste(ms.owner_sql(self.migrations())).stdout)

    def test_a_pasted_replay_on_a_finished_provisioning_prints_nothing_and_changes_nothing(self) -> None:
        self.reset()
        self.psql(ms.owner_sql(self.migrations()))
        before = self.stored_verifier()
        replay = self.paste(ms.owner_sql(self.migrations()))
        self.assertEqual(self.shown_passwords(replay), [])
        self.assertIn("NOT APPLIED", replay.stdout)
        self.assertIn("do not replay this script", replay.stderr)
        self.assertEqual(self.stored_verifier(), before)
        self.assertEqual(self.verdict(), "exact")

    def test_a_replay_after_a_partial_old_style_provisioning_does_not_touch_the_existing_objects(self) -> None:
        self.reset()
        self.psql(ms.provision_sql(self.migrations()))  # schema and role without a password, as an older two-step script left it
        self.psql("INSERT INTO marketing_staging.marketing_publication_radar (dedupe_key, pub_id, is_synthetic) VALUES ('doi:10.0/keep', 'STG_KEEP', true);")
        counts_before = self.counts()
        for runner in (lambda sql: self.psql(sql, check=False), self.paste):
            replay = runner(ms.owner_sql(self.migrations()))
            self.assertEqual(self.shown_passwords(replay), [])
        self.assertEqual(self.counts(), counts_before)
        self.assertEqual(self.verdict(), "provisioned-no-password")
        self.assertEqual(self.psql("SELECT count(*) FROM marketing_staging.marketing_publication_radar;").stdout.strip(), "1")

    def test_rotate_sets_a_new_password_only_and_only_when_provisioned(self) -> None:
        self.reset()
        refused = self.paste(ms.owner_rotate_sql())
        self.assertEqual(self.shown_passwords(refused), [])
        self.assertIn("nothing to rotate", refused.stderr)
        self.assertEqual(self.verdict(), "absent")
        self.psql(ms.provision_sql(self.migrations()))
        first = self.shown_passwords(self.psql(ms.owner_rotate_sql()))
        self.assertEqual(len(first), 1)
        self.assertEqual(self.verdict(), "exact")
        verifier = self.stored_verifier()
        second = self.shown_passwords(self.psql(ms.owner_rotate_sql()))
        self.assertEqual(len(second), 1)
        self.assertNotEqual(first, second)
        self.assertNotEqual(self.stored_verifier(), verifier)
        proof = ms.parse_pairs(self.psql(ms.VERIFY_SQL).stdout)
        self.assertEqual(proof["outside_relations_reachable"], "0")

    @staticmethod
    def runbook_rollback() -> str:
        text = (ROOT / "runbooks" / "marketing-staging-provision.md").read_text(encoding="utf-8")
        block = re.search(r"```sql\n(-- rollback-database\n.*?)```", text, re.S)
        assert block, "the runbook has no rollback-database block"
        return block.group(1)

    def test_the_rollback_in_the_runbook_removes_exactly_what_was_added(self) -> None:
        self.reset()
        before = self.counts()
        self.psql(ms.owner_sql(self.migrations()))
        self.assertEqual(self.verdict(), "exact")
        self.psql(self.runbook_rollback())
        self.assertEqual(self.verdict(), "absent")
        self.assertEqual(self.counts(), before)
        self.assertEqual(self.psql("SELECT count(*) FROM public.lead_identity;").stdout.strip(), "1")
        # and the script can be used again afterwards
        self.assertEqual(len(self.shown_passwords(self.psql(ms.owner_sql(self.migrations())))), 1)

    def test_the_rollback_fails_closed_and_refuses_when_nothing_exists(self) -> None:
        self.reset()
        refused = self.psql(self.runbook_rollback(), check=False)
        self.assertNotEqual(refused.returncode, 0)
        self.assertIn("nothing to roll back", refused.stderr)
        self.psql(ms.owner_sql(self.migrations()))
        self.psql("GRANT SELECT ON public.lead_identity TO marketing_staging_app;")  # the role now holds something outside the schema
        self.addCleanup(self.psql, "REVOKE ALL ON public.lead_identity FROM marketing_staging_app;", check=False)
        stuck = self.psql(self.runbook_rollback(), check=False)
        self.assertNotEqual(stuck.returncode, 0)
        self.assertEqual(self.verdict(), "exact", "a failed rollback must leave everything as it was")

    def test_the_state_script_agrees_with_the_classifier_in_every_state(self) -> None:
        self.reset()
        self.assertEqual((self.script_verdict(), self.verdict()), ("absent", "absent"))
        self.psql(ms.provision_sql(self.migrations()))
        self.assertEqual((self.script_verdict(), self.verdict()), ("provisioned-no-password", "provisioned-no-password"))
        self.psql(ms.owner_rotate_sql())
        self.assertEqual((self.script_verdict(), self.verdict()), ("exact", "exact"))
        self.psql("ALTER ROLE marketing_staging_app CONNECTION LIMIT 9;")
        self.assertEqual((self.script_verdict(), self.verdict()), ("drifted", "drifted"))
        self.reset()
        self.psql("CREATE ROLE marketing_staging_app LOGIN;")
        self.assertEqual((self.script_verdict(), self.verdict()), ("drifted", "drifted"))
        self.psql("DROP ROLE marketing_staging_app;")


if __name__ == "__main__":
    unittest.main()
