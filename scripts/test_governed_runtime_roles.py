"""Tests for scripts/governed_runtime_roles.py.

The module reuses scripts/postgres_runtime_role.py's plumbing (Coolify
discovery, the network/docker routes, credential generation, the
publish-and-reread contract) rather than duplicating it -- that plumbing is
exercised by test_postgres_runtime_role.py already. These tests cover what is
actually new here: the two profiles' declared grants and verification SQL, the
CLI wiring that selects between them, and that provision refuses to proceed on
a missing table or a missing application uuid, the same way the base module's
does.
"""

from __future__ import annotations

import io
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts import governed_runtime_roles as driver  # noqa: E402
from scripts import postgres_runtime_role as base  # noqa: E402


class ProfileRegistryTests(unittest.TestCase):
    def test_exactly_the_two_authorised_profiles_exist(self) -> None:
        self.assertEqual(sorted(driver.PROFILES), ["agent-runtime", "drive-bridge-replay"])

    def test_agent_runtime_profile_targets_the_right_role_and_env_key(self) -> None:
        profile = driver.PROFILES["agent-runtime"]
        self.assertEqual(profile.role, "governed_agent_runtime")
        self.assertEqual(profile.env_key, "GOVERNED_AGENT_RUNTIME_DATABASE_URL")
        self.assertEqual(profile.app_uuid_env, "AGENT_RUNTIME_COOLIFY_APP_UUID")
        self.assertEqual(profile.tables, ("agent_task", "agent_run"))

    def test_drive_bridge_replay_profile_targets_the_right_role_and_env_key(self) -> None:
        profile = driver.PROFILES["drive-bridge-replay"]
        self.assertEqual(profile.role, "drive_bridge_replay_runtime")
        self.assertEqual(profile.env_key, "DRIVE_BRIDGE_REPLAY_DATABASE_URL")
        self.assertEqual(profile.app_uuid_env, "DRIVE_ADAPTER_COOLIFY_APP_UUID")
        self.assertEqual(profile.tables, ("drive_bridge_replay_reservations",))

    def test_the_two_roles_are_named_differently_from_each_other_and_from_ai_gateway(self) -> None:
        names = {p.role for p in driver.PROFILES.values()} | {base.ROLE}
        self.assertEqual(len(names), 3)


class RoleSqlTests(unittest.TestCase):
    def test_an_absent_role_is_created_with_every_attribute_denied(self) -> None:
        statement = driver.role_sql("governed_agent_runtime", "x", role_exists=False)
        self.assertIn("CREATE ROLE governed_agent_runtime", statement)
        for attribute in (
            "NOSUPERUSER",
            "NOCREATEDB",
            "NOCREATEROLE",
            "NOREPLICATION",
            "NOBYPASSRLS",
        ):
            self.assertIn(attribute, statement)
        self.assertIn("CONNECTION LIMIT 20", statement)

    def test_an_existing_role_is_rotated_rather_than_recreated(self) -> None:
        statement = driver.role_sql("drive_bridge_replay_runtime", "x", role_exists=True)
        self.assertNotIn("CREATE ROLE", statement)
        self.assertIn("ALTER ROLE drive_bridge_replay_runtime WITH LOGIN PASSWORD", statement)

    def test_build_dsn_carries_the_right_role_and_database(self) -> None:
        dsn = driver.build_dsn("governed_agent_runtime", "secretvalue", "dbhost", 5432, "prefer")
        self.assertEqual(
            dsn,
            "postgresql://governed_agent_runtime:secretvalue@dbhost:5432/adapteng_ops?sslmode=prefer",
        )


class GrantSqlTests(unittest.TestCase):
    def test_agent_runtime_grants_cover_both_tables_and_nothing_else_named(self) -> None:
        sql = driver.agent_runtime_grant_sql()
        self.assertIn("GRANT SELECT, INSERT, UPDATE ON TABLE agent_task", sql)
        self.assertIn("GRANT SELECT, INSERT, UPDATE ON TABLE agent_run", sql)
        self.assertNotIn("agent_outcome", sql)
        self.assertNotIn("ai_gateway_call", sql)
        self.assertNotIn("drive_bridge_replay_reservations", sql)
        self.assertNotIn("DELETE", sql)
        self.assertNotIn("TRUNCATE", sql)

    def test_drive_bridge_replay_grants_insert_and_select_broadly_but_update_only_by_column(self) -> None:
        sql = driver.drive_bridge_replay_grant_sql()
        self.assertIn("GRANT SELECT, INSERT ON TABLE drive_bridge_replay_reservations", sql)
        self.assertIn(
            "GRANT UPDATE (completed, completed_at) ON TABLE drive_bridge_replay_reservations",
            sql,
        )
        # The identity/payload columns must never appear inside the UPDATE grant's
        # column list -- that is exactly what migration 008's REVOKE FROM PUBLIC
        # anticipates a scoped grant respecting.
        update_line = next(line for line in sql.splitlines() if line.startswith("GRANT UPDATE"))
        for column in ("key_digest", "operation", "payload_sha256", "target_file_id"):
            self.assertNotIn(column, update_line)
        self.assertNotIn("DELETE", sql)
        self.assertNotIn("agent_task", sql)

    def test_every_grant_statement_first_revokes_all_from_the_role(self) -> None:
        for grant_sql in (driver.agent_runtime_grant_sql, driver.drive_bridge_replay_grant_sql):
            sql = grant_sql()
            self.assertIn("REVOKE ALL ON SCHEMA public", sql)
            self.assertTrue(any(line.startswith("REVOKE ALL ON TABLE") for line in sql.splitlines()))


class VerificationSqlTests(unittest.TestCase):
    def test_agent_runtime_verification_checks_isolation_from_both_other_ledgers(self) -> None:
        sql = driver.agent_runtime_verification_sql()
        self.assertIn("SET ROLE governed_agent_runtime", sql)
        self.assertIn("ai_gateway_call", sql)
        self.assertIn("drive_bridge_replay_reservations", sql)
        self.assertIn("ROLLBACK", sql)
        self.assertNotIn("COMMIT", sql)

    def test_drive_bridge_replay_verification_checks_the_column_scoped_deny(self) -> None:
        sql = driver.drive_bridge_replay_verification_sql()
        self.assertIn("SET ROLE drive_bridge_replay_runtime", sql)
        self.assertIn("UPDATE drive_bridge_replay_reservations SET key_digest", sql)
        self.assertIn("insufficient_privilege", sql)
        self.assertIn("agent_task", sql)
        self.assertIn("ROLLBACK", sql)
        self.assertNotIn("COMMIT", sql)

    def test_verification_fixture_values_satisfy_the_migrations_check_constraints(self) -> None:
        """A verification row that the table itself would reject proves nothing."""

        import re

        sql = driver.drive_bridge_replay_verification_sql()
        digest = re.search(r"VALUES \('([0-9a-f]{64})'", sql).group(1)
        payload = re.search(r"'verify', '([0-9a-f]{64})'", sql).group(1)
        file_id = re.search(r"'([A-Za-z0-9_-]{10,128})'\);", sql).group(1)
        self.assertRegex(digest, r"^[0-9a-f]{64}$")
        self.assertRegex(payload, r"^[0-9a-f]{64}$")
        self.assertRegex(file_id, r"^[A-Za-z0-9_-]{10,128}$")


class ReconTests(unittest.TestCase):
    def test_recon_reports_ready_when_every_table_is_present(self) -> None:
        target = mock.Mock()
        target.describe.return_value = "fake target"
        with mock.patch.object(driver, "scalar", side_effect=["fakeuser", "t", "t", "0"]):
            buffer = io.StringIO()
            with redirect_stdout(buffer):
                code = driver.operate_recon(driver.PROFILES["agent-runtime"], target)
        self.assertEqual(code, driver.EXIT_OK)
        self.assertIn("ready_to_provision=yes", buffer.getvalue())
        self.assertIn("role_exists=no", buffer.getvalue())

    def test_recon_reports_not_ready_when_a_table_is_missing(self) -> None:
        target = mock.Mock()
        target.describe.return_value = "fake target"
        with mock.patch.object(driver, "scalar", side_effect=["fakeuser", "f", "0"]):
            buffer = io.StringIO()
            with redirect_stdout(buffer):
                code = driver.operate_recon(driver.PROFILES["drive-bridge-replay"], target)
        self.assertEqual(code, driver.EXIT_OK)
        self.assertIn("ready_to_provision=no", buffer.getvalue())
        self.assertIn("missing_tables=1", buffer.getvalue())


class ProvisionGuardTests(unittest.TestCase):
    """The refusals that must fire before anything touches the database."""

    def test_provision_refuses_without_a_coolify_credential(self) -> None:
        with self.assertRaises(driver.Abort) as raised:
            driver.operate_provision(
                driver.PROFILES["agent-runtime"], mock.Mock(), "uuid", "https://coolify.example",
                "", "dbhost", 5432, "prefer",
            )
        self.assertIn("credential", str(raised.exception))

    def test_provision_refuses_without_an_application_uuid(self) -> None:
        with self.assertRaises(driver.Abort) as raised:
            driver.operate_provision(
                driver.PROFILES["drive-bridge-replay"], mock.Mock(), "", "https://coolify.example",
                "token", "dbhost", 5432, "prefer",
            )
        self.assertIn("DRIVE_ADAPTER_COOLIFY_APP_UUID", str(raised.exception))

    def test_provision_refuses_without_a_dsn_host(self) -> None:
        with self.assertRaises(driver.Abort) as raised:
            driver.operate_provision(
                driver.PROFILES["agent-runtime"], mock.Mock(), "uuid", "https://coolify.example",
                "token", "", 5432, "prefer",
            )
        self.assertIn("host", str(raised.exception))


class CliWiringTests(unittest.TestCase):
    def test_profile_and_operation_are_both_required_and_validated(self) -> None:
        arguments = driver.parse_arguments(["drive-bridge-replay", "recon"])
        self.assertEqual(arguments.profile, "drive-bridge-replay")
        self.assertEqual(arguments.operation, "recon")

    def test_an_unknown_profile_is_rejected_by_argparse(self) -> None:
        with self.assertRaises(SystemExit):
            driver.parse_arguments(["not-a-real-profile", "recon"])

    def test_target_namespace_carries_every_field_choose_target_reads(self) -> None:
        arguments = driver.parse_arguments(
            ["agent-runtime", "recon", "--db-host", "h", "--db-user", "u", "--admin-sslmode", "require"]
        )
        namespace = driver.target_namespace(arguments)
        self.assertEqual(namespace.transport, "network")
        self.assertEqual(namespace.db_host, "h")
        self.assertEqual(namespace.db_user, "u")
        self.assertEqual(namespace.admin_sslmode, "require")

    def test_main_dispatches_recon_through_choose_target(self) -> None:
        fake_target = mock.Mock()
        fake_target.describe.return_value = "fake"
        with mock.patch.object(driver.base, "choose_target", return_value=fake_target) as choose:
            with mock.patch.object(driver, "operate_recon", return_value=0) as recon:
                code = driver.main(["agent-runtime", "recon"])
        self.assertEqual(code, 0)
        choose.assert_called_once()
        recon.assert_called_once_with(driver.PROFILES["agent-runtime"], fake_target)

    def test_main_reads_the_profiles_own_app_uuid_env_var_for_provision(self) -> None:
        fake_target = mock.Mock()
        with mock.patch.object(driver.base, "choose_target", return_value=fake_target):
            with mock.patch.object(driver, "operate_provision", return_value=0) as provision:
                with mock.patch.dict(
                    "os.environ",
                    {"DRIVE_ADAPTER_COOLIFY_APP_UUID": "the-uuid", "COOLIFY_URL": "https://c",
                     "COOLIFY_API_TOKEN": "tok"},
                    clear=False,
                ):
                    driver.main(["drive-bridge-replay", "provision"])
        provision.assert_called_once()
        called_uuid = provision.call_args.args[2]
        self.assertEqual(called_uuid, "the-uuid")

    def test_main_surfaces_an_abort_as_a_failed_exit_code_without_raising(self) -> None:
        with mock.patch.object(driver.base, "choose_target", side_effect=driver.Abort("no route")):
            buffer = io.StringIO()
            with redirect_stdout(buffer):
                code = driver.main(["agent-runtime", "recon"])
        self.assertEqual(code, driver.base.EXIT_FAILED)
        self.assertIn("ABORT no route", buffer.getvalue())


if __name__ == "__main__":
    unittest.main()
