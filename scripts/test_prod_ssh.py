"""Tests for the production host command channel.

The thing under test is a tool that runs commands on a production host, so the
tests are mostly about what it refuses to do and what it never exposes. A test
that only checked "the right ssh flags are present" would pass just as happily
for a version that took SQL from a dispatch input.

Nothing here opens a network connection: subprocess.run is replaced, and the
argv and stdin it was handed are the observations.
"""

from __future__ import annotations

import io
import os
import pathlib
import subprocess
import sys
import unittest
from contextlib import redirect_stdout
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import prod_ssh as driver  # noqa: E402

KEY = "-----BEGIN OPENSSH PRIVATE KEY-----\nnotarealkey\n-----END-----"
KNOWN = "[example.invalid]:22 ssh-ed25519 AAAAC3Nz"

ENVIRONMENT = {
    "PROD_SSH_HOST": "203.0.113.9",
    "PROD_SSH_PORT": "22",
    "PROD_SSH_USER": "adapteng-ai",
    "PROD_SSH_PRIVATE_KEY": KEY,
    "PROD_SSH_KNOWN_HOSTS": KNOWN,
}


class Recorder:
    """A stand-in for subprocess.run that records and answers."""

    def __init__(self, answers=None):
        self.calls = []
        self.answers = list(answers or [])

    def __call__(self, argv, input=None, capture_output=None, text=None, timeout=None):
        self.calls.append({"argv": list(argv), "stdin": input})
        if self.answers:
            stdout, returncode = self.answers.pop(0)
        else:
            stdout, returncode = "", 0
        return subprocess.CompletedProcess(argv, returncode, stdout, "")


def with_environment(**overrides):
    environment = dict(ENVIRONMENT)
    environment.update(overrides)
    return mock.patch.dict(os.environ, environment, clear=False)


def drive(operation, recorder, run_id=""):
    argv = ["--operation", operation]
    if run_id:
        argv += ["--run-id", run_id]
    buffer = io.StringIO()
    with with_environment(), mock.patch.object(subprocess, "run", recorder):
        with redirect_stdout(buffer):
            code = driver.run(argv)
    return code, buffer.getvalue()


class ChannelTests(unittest.TestCase):
    def test_the_key_never_becomes_a_command_line_argument(self) -> None:
        """Anything in argv is readable in the host's process list.

        A key passed as an argument would be exposed to every other process on
        the runner for the life of the command, which is the one thing this
        tool must not do.
        """

        recorder = Recorder()
        drive("containers", recorder)
        self.assertTrue(recorder.calls)
        for call in recorder.calls:
            joined = " ".join(call["argv"])
            self.assertNotIn(KEY, joined)
            self.assertNotIn("notarealkey", joined)

    @unittest.skipUnless(os.name == "posix", "permission bits are a POSIX guarantee")
    def test_the_key_file_is_private_and_then_removed(self) -> None:
        """A key left behind on a shared runner outlives the job that needed it."""

        seen = {}

        def capture(argv, **kwargs):
            for index, item in enumerate(argv):
                if item == "-i":
                    seen["path"] = argv[index + 1]
                    seen["mode"] = os.stat(argv[index + 1]).st_mode & 0o777
            return subprocess.CompletedProcess(argv, 0, "", "")

        with with_environment(), mock.patch.object(subprocess, "run", capture):
            with redirect_stdout(io.StringIO()):
                driver.run(["--operation", "containers"])

        self.assertEqual(seen["mode"], 0o600)
        self.assertFalse(os.path.exists(seen["path"]))

    def test_host_key_checking_is_not_disabled(self) -> None:
        """Turning it off is the usual shortcut and it removes the guarantee.

        With StrictHostKeyChecking=no the connection would succeed against a
        substituted host, which is precisely the failure this channel must not
        have when it is about to write to a production database.
        """

        recorder = Recorder()
        drive("containers", recorder)
        options = recorder.calls[0]["argv"]
        self.assertIn("StrictHostKeyChecking=yes", options)
        self.assertIn("BatchMode=yes", options)
        self.assertNotIn("StrictHostKeyChecking=no", " ".join(options))
        self.assertNotIn("UserKnownHostsFile=/dev/null", " ".join(options))

    def test_a_missing_secret_stops_before_connecting(self) -> None:
        """Half a credential set should fail loudly, not attempt the connection."""

        recorder = Recorder()
        with with_environment(PROD_SSH_PRIVATE_KEY=""):
            with self.assertRaises(SystemExit):
                with mock.patch.object(subprocess, "run", recorder):
                    driver.run(["--operation", "containers"])
        self.assertEqual(recorder.calls, [])


class OperationTests(unittest.TestCase):
    def test_the_remote_command_is_never_taken_from_an_input(self) -> None:
        """The only caller-supplied value is a run id, and it is not a command.

        The parser accepts a fixed set of operations, so a dispatch cannot ask
        for arbitrary SQL or an arbitrary shell command.
        """

        with self.assertRaises(SystemExit):
            driver.parse_args(["--operation", "rm -rf /"])
        self.assertEqual(
            set(driver.OPERATIONS),
            {"containers", "ledger-status", "open-smoke-run"},
        )

    def test_containers_changes_nothing(self) -> None:
        """The first operation exists to prove the channel, not to use it."""

        recorder = Recorder([("gw\tUp 2 days\n", 0)])
        code, output = drive("containers", recorder)
        self.assertEqual(code, driver.EXIT_OK)
        self.assertEqual(len(recorder.calls), 1)
        remote = recorder.calls[0]["argv"][-1]
        self.assertIn("docker ps", remote)
        self.assertNotIn("exec", remote)
        self.assertIsNone(recorder.calls[0]["stdin"])
        self.assertIn("Up 2 days", output)

    def test_sql_travels_on_stdin_and_not_in_the_command(self) -> None:
        """Quoting a statement through two shells is where the bugs live.

        On stdin no shell parses it at all, so a value containing a quote
        cannot change what runs.
        """

        recorder = Recorder([("abc123\n", 0), ("count\n", 0)])
        code, _ = drive("ledger-status", recorder)
        self.assertEqual(code, driver.EXIT_OK)
        remote = recorder.calls[1]["argv"][-1]
        self.assertIn("docker exec -i abc123", remote)
        self.assertNotIn("SELECT", remote)
        self.assertIn("SELECT", recorder.calls[1]["stdin"])

    def test_ledger_status_reads_counts_and_no_business_values(self) -> None:
        """A status query that selected rows would pull production data into a log."""

        statement = driver.LEDGER_STATUS_SQL.lower()
        self.assertIn("count(1)", statement)
        for forbidden in ("select *", "input_text", "output_text", "prompt"):
            self.assertNotIn(forbidden, statement)

    def test_an_ambiguous_container_match_stops_the_operation(self) -> None:
        """Two matches means the target is unknown, and one of them is the wrong database."""

        recorder = Recorder([("aaa\nbbb\n", 0)])
        code, output = drive("ledger-status", recorder)
        self.assertEqual(code, driver.EXIT_FAILED)
        self.assertEqual(len(recorder.calls), 1)
        self.assertIn("expected exactly one container", output)

    def test_a_failed_remote_command_is_reported_as_a_failure(self) -> None:
        """A non-zero exit that returned EXIT_OK would make every check meaningless."""

        recorder = Recorder([("abc123\n", 0), ("", 3)])
        code, _ = drive("ledger-status", recorder)
        self.assertEqual(code, driver.EXIT_FAILED)


class SmokeRunTests(unittest.TestCase):
    def test_it_opens_one_run_idempotently(self) -> None:
        """A second dispatch must reconcile, not fail and not duplicate."""

        recorder = Recorder([("abc123\n", 0), ("smk-1 | started\n", 0)])
        code, _ = drive("open-smoke-run", recorder, run_id="smk-20260907T235959Z")
        self.assertEqual(code, driver.EXIT_OK)
        sql = recorder.calls[1]["stdin"]
        self.assertEqual(sql.lower().count("insert into"), 2)
        self.assertEqual(sql.lower().count("on conflict"), 2)
        self.assertIn("smk-20260907T235959Z", sql)

    def test_it_writes_only_to_the_ledger_tables(self) -> None:
        """The one write this tool performs has to be the one it documents."""

        statement = driver.SMOKE_RUN_SQL.lower()
        self.assertIn("insert into agent_task", statement)
        self.assertIn("insert into agent_run", statement)
        for forbidden in ("delete", "drop", "truncate", "update ", "alter"):
            self.assertNotIn(forbidden, statement)

    def test_an_identifier_with_a_quote_is_refused(self) -> None:
        """psql has no client-side binding, so the identifier is substituted.

        Generated values are safe today; the check is here so that they stay
        safe when someone later wires the run id to an input.
        """

        recorder = Recorder([("abc123\n", 0)])
        code, output = drive("open-smoke-run", recorder, run_id="a'; DROP TABLE agent_run;--")
        self.assertEqual(code, driver.EXIT_FAILED)
        self.assertIn("unexpected characters", output)
        self.assertEqual(len(recorder.calls), 1)

    def test_a_missing_run_id_is_refused(self) -> None:
        """Opening a run with an empty key would create a row nothing can address."""

        recorder = Recorder()
        code, output = drive("open-smoke-run", recorder)
        self.assertEqual(code, driver.EXIT_FAILED)
        self.assertIn("a run id is required", output)
        self.assertEqual(recorder.calls, [])


class WorkflowContractTests(unittest.TestCase):
    """The workflow and the driver have to offer the same operations.

    They are edited separately, and a choice list that drifts from the parser
    produces a dispatch that fails only once someone selects the stale entry.
    """

    def workflow(self) -> str:
        path = (
            pathlib.Path(__file__).resolve().parent.parent
            / ".github"
            / "workflows"
            / "prod-ssh.yml"
        )
        return path.read_text(encoding="utf-8")

    def test_every_offered_operation_is_implemented(self) -> None:
        body = self.workflow()
        offered = set()
        for line in body.splitlines():
            stripped = line.strip()
            if stripped.startswith("- ") and stripped[2:] in driver.OPERATIONS:
                offered.add(stripped[2:])
        self.assertEqual(offered, set(driver.OPERATIONS))

    def test_the_workflow_passes_no_secret_on_a_command_line(self) -> None:
        """Secrets belong in env, where they are masked and not in the process list."""

        body = self.workflow()
        for line in body.splitlines():
            if "python scripts/prod_ssh.py" in line or line.strip().startswith("--"):
                self.assertNotIn("secrets.", line)


if __name__ == "__main__":
    unittest.main()
