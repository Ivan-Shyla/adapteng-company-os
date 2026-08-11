"""Tests for scripts/postgres_runtime_role.py.

The subject runs on the production host and handles the one value in this system
that must never be seen: the runtime role's password. So the tests are weighted
towards two things that cannot be checked by reading the code later — that the
password never reaches an output stream, and that a write nobody could confirm
is reported as a failure rather than a success.

Everything that talks to Postgres or to Coolify is injected. No test runs docker,
opens a socket, or needs a database.
"""

from __future__ import annotations

import io
import unittest
from contextlib import redirect_stdout
from pathlib import Path

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts import postgres_runtime_role as driver  # noqa: E402


ROLE_CREDENTIAL = "an-example-value-standing-in-for-a-real-one"


class ContainerDiscoveryTests(unittest.TestCase):
    """The database is found by what it runs, not by what it is called."""

    def test_a_name_and_image_pair_is_parsed(self) -> None:
        rows = driver.parse_container_table("db\tpostgres:16\nweb\tnginx:1.27\n")
        self.assertEqual(rows, [("db", "postgres:16"), ("web", "nginx:1.27")])

    def test_blank_and_malformed_lines_are_ignored(self) -> None:
        rows = driver.parse_container_table("\ndb\tpostgres:16\nbroken\n\t\n")
        self.assertEqual(rows, [("db", "postgres:16")])

    def test_the_image_decides_and_the_name_does_not(self) -> None:
        """A container called db-something may be a backup sidecar, not a server."""

        rows = [
            ("adapteng-ops-db-backup", "pgbackrest/pgbackrest:2.51"),
            ("some-container", "postgres:16.4"),
        ]
        self.assertEqual(driver.postgres_containers(rows), ["some-container"])

    def test_a_registry_prefix_and_a_digest_do_not_hide_the_image(self) -> None:
        rows = [("db", "ghcr.io/library/postgres:16@sha256:" + "0" * 64)]
        self.assertEqual(driver.postgres_containers(rows), ["db"])

    def test_no_postgres_container_aborts(self) -> None:
        with self.assertRaises(driver.Abort) as raised:
            driver.choose_container([])
        self.assertIn("no running container", str(raised.exception))

    def test_two_postgres_containers_abort_rather_than_pick(self) -> None:
        """Which one holds canonical production is a decision, not a lookup."""

        with self.assertRaises(driver.Abort) as raised:
            driver.choose_container(["one", "two"])
        self.assertIn("--container", str(raised.exception))

    def test_exactly_one_is_returned(self) -> None:
        self.assertEqual(driver.choose_container(["only"]), "only")


class SqlBuildingTests(unittest.TestCase):
    def test_a_quote_in_a_literal_is_doubled(self) -> None:
        self.assertEqual(driver.sql_literal("it's"), "'it''s'")

    def test_a_generated_password_needs_no_escaping_anywhere(self) -> None:
        """A password with a quote or a slash would break the SQL or the DSN.

        This is asserted over many draws rather than one, because the failure
        would be intermittent and would first appear in production.
        """

        allowed = set(
            "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
        )
        for _ in range(200):
            role_credential = driver.generate_credential()
            self.assertTrue(set(role_credential) <= allowed)
            self.assertGreaterEqual(len(role_credential), 43)

    def test_two_passwords_are_not_the_same(self) -> None:
        self.assertNotEqual(driver.generate_credential(), driver.generate_credential())

    def test_an_absent_role_is_created_with_every_attribute_denied(self) -> None:
        statement = driver.role_sql(ROLE_CREDENTIAL, role_exists=False)
        self.assertIn("CREATE ROLE ai_gateway_runtime", statement)
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
        statement = driver.role_sql(ROLE_CREDENTIAL, role_exists=True)
        self.assertNotIn("CREATE ROLE", statement)
        self.assertIn("ALTER ROLE ai_gateway_runtime WITH LOGIN PASSWORD", statement)

    def test_the_attributes_are_reset_even_for_a_role_that_already_existed(self) -> None:
        """A role with a different history must not keep a privilege from it."""

        statement = driver.role_sql(ROLE_CREDENTIAL, role_exists=True)
        self.assertIn(
            "ALTER ROLE ai_gateway_runtime NOSUPERUSER NOCREATEDB NOCREATEROLE",
            statement,
        )

    def test_the_grants_cover_the_six_functions_and_nothing_else(self) -> None:
        statement = driver.grant_sql()
        granted = [line for line in statement.splitlines() if line.startswith("GRANT EXECUTE")]
        self.assertEqual(len(granted), 6)
        self.assertEqual(len(driver.GRANTED_FUNCTIONS), 6)
        for name, _ in driver.GRANTED_FUNCTIONS:
            self.assertIn(f"GRANT EXECUTE ON FUNCTION {name}(", statement)

    def test_table_access_is_revoked_and_never_granted_back(self) -> None:
        statement = driver.grant_sql()
        self.assertIn("REVOKE ALL ON TABLE ai_gateway_call FROM ai_gateway_runtime;", statement)
        self.assertNotIn("GRANT SELECT", statement)
        self.assertNotIn("GRANT INSERT", statement)
        self.assertNotIn("GRANT ALL", statement)

    def test_only_usage_is_granted_on_the_schema(self) -> None:
        statement = driver.grant_sql()
        self.assertIn("REVOKE ALL ON SCHEMA public FROM ai_gateway_runtime;", statement)
        self.assertIn("GRANT USAGE ON SCHEMA public TO ai_gateway_runtime;", statement)

    def test_a_missing_function_reads_as_absent_rather_than_raising(self) -> None:
        """to_regprocedure returns NULL for an unknown identity, so a drifted
        signature is a survey result instead of an aborted survey."""

        statement = driver.function_exists_sql("f", "TEXT")
        self.assertIn("to_regprocedure('f(TEXT)')", statement)
        self.assertIn("IS NOT NULL", statement)

    def test_the_verifications_assert_all_four_and_give_the_role_back(self) -> None:
        statement = driver.verification_sql()
        for marker in ("4a", "4b", "4c", "4d"):
            self.assertIn(marker, statement)
        self.assertIn("insufficient_privilege", statement)
        self.assertIn("sqlstate '22023'", statement)
        self.assertTrue(statement.rstrip().endswith("RESET ROLE;"))

    def test_the_positive_control_treats_a_denied_execute_as_a_failure(self) -> None:
        """4d is the only check that proves the grant took effect at all."""

        statement = driver.verification_sql()
        self.assertIn(
            "RAISE EXCEPTION '4d FAILED: EXECUTE denied, the grant did not take effect'",
            statement,
        )


class DsnTests(unittest.TestCase):
    def test_the_dsn_matches_the_declared_shape(self) -> None:
        dsn = driver.build_dsn(ROLE_CREDENTIAL, "db.internal", 5432, "verify-full")
        self.assertEqual(
            dsn,
            f"postgresql://ai_gateway_runtime:{ROLE_CREDENTIAL}@db.internal:5432/"
            "adapteng_ops?sslmode=verify-full",
        )

    def test_the_ssl_mode_is_carried_through_rather_than_assumed(self) -> None:
        dsn = driver.build_dsn(ROLE_CREDENTIAL, "h", 6543, "require")
        self.assertIn("sslmode=require", dsn)
        self.assertIn(":6543/", dsn)


class FakeCoolify:
    """A stand-in for the Coolify API that records calls and stores values."""

    def __init__(self, existing: dict | None = None) -> None:
        self.entries = [{"key": key, "value": value} for key, value in (existing or {}).items()]
        self.calls: list[tuple[str, str]] = []
        self.patch_status = 200
        self.post_status = 201
        self.store_writes = True
        self.get_status = 200

    def __call__(self, base_url, credential, method, path, payload):
        self.calls.append((method, path))
        if method == "GET":
            return self.get_status, [dict(entry) for entry in self.entries]
        status = self.patch_status if method == "PATCH" else self.post_status
        if 200 <= status < 300 and self.store_writes:
            for entry in self.entries:
                if entry["key"] == payload["key"]:
                    entry["value"] = payload["value"]
                    break
            else:
                self.entries.append(dict(payload))
        return status, None


class PublishTests(unittest.TestCase):
    """A value that could not be re-read has not been set, whatever the API said."""

    def setUp(self) -> None:
        self.real = driver.coolify_request
        self.addCleanup(setattr, driver, "coolify_request", self.real)

    def use(self, fake: FakeCoolify) -> FakeCoolify:
        driver.coolify_request = fake
        return fake

    def test_an_existing_key_is_updated(self) -> None:
        fake = self.use(FakeCoolify({"AI_GATEWAY_DATABASE_URL": "old"}))
        outcome = driver.publish_environment_value(
            "https://c.example", "t", "app-1", "AI_GATEWAY_DATABASE_URL", "new"
        )
        self.assertEqual(outcome, "updated")
        self.assertEqual(fake.entries[0]["value"], "new")

    def test_a_missing_key_falls_through_to_creation(self) -> None:
        fake = self.use(FakeCoolify())
        fake.patch_status = 404
        outcome = driver.publish_environment_value(
            "https://c.example", "t", "app-1", "AI_GATEWAY_DATABASE_URL", "new"
        )
        self.assertEqual(outcome, "created")
        self.assertEqual([method for method, _ in fake.calls], ["PATCH", "POST", "GET"])

    def test_an_accepted_write_that_stored_nothing_is_a_failure(self) -> None:
        """The defect this exists to catch: HTTP 200 and no stored value."""

        fake = self.use(FakeCoolify())
        fake.store_writes = False
        with self.assertRaises(driver.Abort) as raised:
            driver.publish_environment_value(
                "https://c.example", "t", "app-1", "AI_GATEWAY_DATABASE_URL", "new"
            )
        self.assertIn("not present on re-read", str(raised.exception))

    def test_a_stored_value_that_differs_is_a_failure(self) -> None:
        fake = self.use(FakeCoolify({"AI_GATEWAY_DATABASE_URL": "old"}))
        fake.store_writes = False
        with self.assertRaises(driver.Abort) as raised:
            driver.publish_environment_value(
                "https://c.example", "t", "app-1", "AI_GATEWAY_DATABASE_URL", "new"
            )
        self.assertIn("does not hold what was sent", str(raised.exception))

    def test_an_unreadable_environment_is_a_failure_not_a_success(self) -> None:
        fake = self.use(FakeCoolify())
        fake.get_status = 500
        with self.assertRaises(driver.Abort) as raised:
            driver.publish_environment_value(
                "https://c.example", "t", "app-1", "AI_GATEWAY_DATABASE_URL", "new"
            )
        self.assertIn("refusing to report success", str(raised.exception))

    def test_an_unexpected_status_aborts_immediately(self) -> None:
        fake = self.use(FakeCoolify())
        fake.patch_status = 403
        with self.assertRaises(driver.Abort) as raised:
            driver.publish_environment_value(
                "https://c.example", "t", "app-1", "AI_GATEWAY_DATABASE_URL", "new"
            )
        self.assertIn("HTTP 403", str(raised.exception))
        self.assertEqual([method for method, _ in fake.calls], ["PATCH"])

    def test_a_plaintext_base_address_is_refused(self) -> None:
        with self.assertRaises(driver.Abort) as raised:
            driver.coolify_request("http://c.example", "t", "GET", "/x", None)
        self.assertIn("must be https", str(raised.exception))


class FakePostgres:
    """Answers the queries the driver asks, and records the SQL it was given."""

    def __init__(self, role_exists: bool = False, functions_present: bool = True) -> None:
        self.role_exists = role_exists
        self.functions_present = functions_present
        self.statements: list[str] = []
        self.notices = (
            "NOTICE:  4a OK: INSERT denied\nNOTICE:  4b OK: SELECT denied\n"
            "NOTICE:  4d OK: EXECUTE succeeded and the function validated its own input\n"
        )

    def __call__(self, container, sql, database=driver.DATABASE, check=True):
        self.statements.append(sql)
        if "to_regprocedure" in sql:
            return 0, "t\n" if self.functions_present else "f\n", ""
        if "pg_roles WHERE rolname" in sql:
            return 0, ("1\n" if self.role_exists else "0\n"), ""
        if "pg_database" in sql:
            return 0, "1\n", ""
        if "to_regclass" in sql:
            return 0, "t\n", ""
        if "SET ROLE" in sql:
            return 0, "", self.notices
        return 0, "value\n", ""


class ProvisionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.real_psql = driver.psql
        self.real_run = driver.run
        self.real_credential = driver.generate_credential
        self.real_request = driver.coolify_request
        self.addCleanup(setattr, driver, "psql", self.real_psql)
        self.addCleanup(setattr, driver, "run", self.real_run)
        self.addCleanup(setattr, driver, "generate_credential", self.real_credential)
        self.addCleanup(setattr, driver, "coolify_request", self.real_request)
        driver.generate_credential = lambda: ROLE_CREDENTIAL
        driver.run = lambda command, stdin=None, check=True: (0, "host\n", "")

    def provision(self, postgres: FakePostgres, coolify: FakeCoolify, **overrides):
        driver.psql = postgres
        driver.coolify_request = coolify
        arguments = {
            "container": "db",
            "application_uuid": "app-1",
            "base_url": "https://c.example",
            "credential": "an-example-credential",
            "dsn_host": "db.internal",
            "dsn_port": 5432,
            "sslmode": "verify-full",
        }
        arguments.update(overrides)
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            code = driver.operate_provision(**arguments)
        return code, buffer.getvalue()

    def test_the_password_never_reaches_the_output(self) -> None:
        """The single property that cannot be recovered if it is ever wrong."""

        postgres = FakePostgres()
        code, report = self.provision(postgres, FakeCoolify())
        self.assertEqual(code, driver.EXIT_OK)
        self.assertNotIn(ROLE_CREDENTIAL, report)
        self.assertIn("RESULT provision ok", report)

    def test_the_password_reaches_postgres_and_the_dsn(self) -> None:
        """The other half: it must be absent from the log and present where used."""

        postgres = FakePostgres()
        coolify = FakeCoolify()
        self.provision(postgres, coolify)
        self.assertTrue(any(ROLE_CREDENTIAL in statement for statement in postgres.statements))
        stored = [item for item in coolify.entries if item["key"] == "AI_GATEWAY_DATABASE_URL"]
        self.assertEqual(len(stored), 1)
        self.assertIn(ROLE_CREDENTIAL, stored[0]["value"])
        self.assertTrue(stored[0]["value"].startswith("postgresql://ai_gateway_runtime:"))

    def test_a_missing_function_stops_the_run_before_anything_is_written(self) -> None:
        """The grants would bind to nothing, and the role would look provisioned."""

        postgres = FakePostgres(functions_present=False)
        driver.psql = postgres
        driver.coolify_request = FakeCoolify()
        with self.assertRaises(driver.Abort) as raised:
            driver.operate_provision(
                "db", "app-1", "https://c.example", "t", "db.internal", 5432, "verify-full"
            )
        self.assertIn("008a", str(raised.exception))
        self.assertFalse(any("CREATE ROLE" in s for s in postgres.statements))
        self.assertFalse(any("GRANT" in s for s in postgres.statements))

    def test_an_existing_role_is_rotated_and_said_so(self) -> None:
        postgres = FakePostgres(role_exists=True)
        _, report = self.provision(postgres, FakeCoolify())
        self.assertIn("rotated", report)
        self.assertTrue(any("ALTER ROLE ai_gateway_runtime WITH LOGIN" in s for s in postgres.statements))

    def test_the_verifications_are_run_and_their_notices_reported(self) -> None:
        postgres = FakePostgres()
        _, report = self.provision(postgres, FakeCoolify())
        self.assertIn("4a OK", report)
        self.assertIn("4d OK", report)
        self.assertIn("verifications 4a-4d passed", report)

    def test_a_missing_dsn_host_is_refused_rather_than_guessed(self) -> None:
        driver.psql = FakePostgres()
        driver.coolify_request = FakeCoolify()
        with self.assertRaises(driver.Abort) as raised:
            driver.operate_provision(
                "db", "app-1", "https://c.example", "t", "", 5432, "verify-full"
            )
        self.assertIn("placement fact", str(raised.exception))

    def test_a_missing_token_is_refused_before_the_role_is_touched(self) -> None:
        postgres = FakePostgres()
        driver.psql = postgres
        with self.assertRaises(driver.Abort):
            driver.operate_provision(
                "db", "app-1", "https://c.example", "", "db.internal", 5432, "verify-full"
            )
        self.assertEqual(postgres.statements, [])


class ReconTests(unittest.TestCase):
    def setUp(self) -> None:
        self.real_psql = driver.psql
        self.real_run = driver.run
        self.addCleanup(setattr, driver, "psql", self.real_psql)
        self.addCleanup(setattr, driver, "run", self.real_run)
        driver.run = lambda command, stdin=None, check=True: (0, "host\n", "")

    def recon(self, postgres: FakePostgres) -> tuple[int, str]:
        driver.psql = postgres
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            code = driver.operate_recon("db")
        return code, buffer.getvalue()

    def test_recon_writes_nothing(self) -> None:
        """A survey that changes state is not a survey."""

        postgres = FakePostgres()
        self.recon(postgres)
        joined = " ".join(postgres.statements).upper()
        for verb in ("CREATE ", "ALTER ", "GRANT ", "REVOKE ", "INSERT ", "UPDATE ", "DROP "):
            self.assertNotIn(verb, joined)

    def test_recon_reports_a_ready_database(self) -> None:
        code, report = self.recon(FakePostgres())
        self.assertEqual(code, driver.EXIT_OK)
        self.assertIn("ready_to_provision=yes", report)
        self.assertIn("role ai_gateway_runtime: ABSENT", report)

    def test_recon_reports_missing_functions_without_failing(self) -> None:
        """An incomplete database is a finding to read, not an error to debug."""

        code, report = self.recon(FakePostgres(functions_present=False))
        self.assertEqual(code, driver.EXIT_OK)
        self.assertIn("ready_to_provision=no", report)
        self.assertIn("missing_functions=6", report)


class EntryPointTests(unittest.TestCase):
    def test_an_abort_is_reported_and_exits_non_zero(self) -> None:
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            code = driver.main(["provision"])
        self.assertEqual(code, driver.EXIT_FAILED)
        self.assertIn("ABORT", buffer.getvalue())

    def test_an_unknown_operation_is_refused(self) -> None:
        with self.assertRaises(SystemExit) as raised:
            driver.parse_arguments(["destroy"])
        self.assertEqual(raised.exception.code, driver.EXIT_USAGE)


if __name__ == "__main__":
    unittest.main()
