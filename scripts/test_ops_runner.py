#!/usr/bin/env python3
"""Tests for the operations runner preflight.

The one assertion that matters here is that a minted registration credential
never reaches the report. Preflight has to mint one to prove the authority
exists, which means the value passes through this process, which means the only
thing standing between it and a public build log is this code.
"""

from __future__ import annotations

import io
import json
import os
import pathlib
import sys
import unittest
from contextlib import redirect_stdout

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import ops_runner as runner  # noqa: E402

MINTED = "an-example-registration-value-standing-in-for-a-real-one"
ADMIN = "an-example-admin-value-standing-in-for-a-real-one"
REPOSITORY = "Ivan-Shyla/adapteng-company-os"


class FakeGitHub:
    """A stand-in for the REST API that records what was asked of it."""

    def __init__(
        self, *, mint_status: int = 201, list_status: int = 200, online: bool | None = None
    ) -> None:
        self.mint_status = mint_status
        self.list_status = list_status
        self.online = online
        self.calls: list[tuple[str, str]] = []

    def __call__(self, path, credential, method="GET"):
        self.calls.append((method, path))
        if path.endswith("/registration-token"):
            if self.mint_status != 201:
                return self.mint_status, {"message": "Resource not accessible"}
            return 201, {"token": MINTED, "expires_at": "2026-08-11T17:00:00Z"}
        if path.endswith("/actions/runners"):
            if self.list_status != 200:
                return self.list_status, {"message": "Resource not accessible"}
            if self.online is None:
                return 200, {
                    "total_count": 1,
                    "runners": [
                        {
                            "name": "existing-runner",
                            "status": "offline",
                            "busy": False,
                            "labels": [{"name": "self-hosted"}, {"name": "adapteng-ops"}],
                        }
                    ],
                }
            return 200, {
                "total_count": 1,
                "runners": [
                    {
                        "name": runner.RUNNER_NAME,
                        "status": "online" if self.online else "offline",
                        "busy": False,
                        "labels": [{"name": "self-hosted"}, {"name": runner.RUNNER_LABEL}],
                    }
                ],
            }
        return 404, None


def run_preflight(fake, **environment):
    values = {
        "GITHUB_REPOSITORY": REPOSITORY,
        "GITHUB_ADMIN_CREDENTIAL": ADMIN,
    }
    values.update(environment)
    saved = dict(os.environ)
    original = runner.github_call
    runner.github_call = fake
    buffer = io.StringIO()
    try:
        os.environ.update({key: value for key, value in values.items() if value is not None})
        for key, value in values.items():
            if value is None:
                os.environ.pop(key, None)
        with redirect_stdout(buffer):
            code = runner.operate_preflight()
    finally:
        runner.github_call = original
        os.environ.clear()
        os.environ.update(saved)
    return code, buffer.getvalue()


class PreflightTests(unittest.TestCase):
    def test_a_minted_credential_never_reaches_the_report(self) -> None:
        """The whole reason this module is allowed to mint anything.

        Proving the authority is held requires obtaining a real registration
        credential. A build log is public to anyone who can read the repository,
        so the value has to be described rather than shown, and the fixture
        carries a recognisable one so that the assertion has something to fail
        on if that ever stops being true.
        """

        fake = FakeGitHub()
        code, report = run_preflight(fake)
        self.assertEqual(code, runner.EXIT_OK)
        self.assertIn("registration=available", report)
        self.assertIn("expires_at=2026-08-11T17:00:00Z", report)
        self.assertNotIn(MINTED, report)

    def test_the_report_says_a_credential_was_obtained(self) -> None:
        fake = FakeGitHub()
        _, report = run_preflight(fake)
        self.assertIn(f"length={len(MINTED)}", report)

    def test_a_refused_mint_blocks_rather_than_pretending(self) -> None:
        """A refusal is the finding, and it must not read as a success.

        This is the case that decides whether the work can continue without the
        owner, so it has to exit non-zero and name the reason rather than carry
        on and fail later at a point that no longer explains itself.
        """

        fake = FakeGitHub(mint_status=403)
        code, report = run_preflight(fake)
        self.assertEqual(code, runner.EXIT_FAILED)
        self.assertIn("cannot-register-runner", report)
        self.assertIn("HTTP 403", report)

    def test_an_absent_admin_credential_blocks_before_calling_anything(self) -> None:
        fake = FakeGitHub()
        code, report = run_preflight(fake, GITHUB_ADMIN_CREDENTIAL=None)
        self.assertEqual(code, runner.EXIT_FAILED)
        self.assertIn("no-admin-credential", report)
        self.assertEqual(fake.calls, [])

    def test_an_unreadable_listing_does_not_stop_the_decisive_check(self) -> None:
        """Listing is informative; minting is the question. Only the latter decides."""

        fake = FakeGitHub(list_status=403)
        code, report = run_preflight(fake)
        self.assertEqual(code, runner.EXIT_OK)
        self.assertIn("administration read refused", report)

    def test_existing_runners_are_reported_with_their_labels(self) -> None:
        fake = FakeGitHub()
        _, report = run_preflight(fake)
        self.assertIn("self-hosted runners already registered: 1", report)
        self.assertIn("existing-runner", report)
        self.assertIn("adapteng-ops", report)

    def test_an_absent_repository_aborts(self) -> None:
        fake = FakeGitHub()
        with self.assertRaises(runner.driver.Abort):
            run_preflight(fake, GITHUB_REPOSITORY=None)


class DescriptionTests(unittest.TestCase):
    def test_a_description_never_contains_the_value(self) -> None:
        self.assertNotIn(MINTED, runner.describe_secret(MINTED))
        self.assertEqual(runner.describe_secret(""), "absent")
        self.assertEqual(runner.describe_secret(None), "absent")
        self.assertEqual(runner.describe_secret(123), "absent")

    def test_the_pinned_image_is_the_official_one_and_is_pinned(self) -> None:
        """A runner image executes whatever a workflow asks of it, so provenance matters."""

        self.assertEqual(runner.RUNNER_IMAGE, "ghcr.io/actions/actions-runner")
        self.assertNotIn(runner.RUNNER_IMAGE_TAG, {"latest", ""})


class FakeCoolify:
    """A stand-in for the Coolify API that records every write it is asked to make.

    It answers reads from mutable state rather than from a script, so a test can
    make the instance store something other than what was sent and prove that
    the re-read catches it.
    """

    def __init__(self, *, application: dict | None = None) -> None:
        self.application = application
        self.environment_entries: list[dict] = []
        self.writes: list[tuple[str, str, dict | None]] = []
        self.deployment_states = ["in_progress", "finished"]

    def __call__(
        self, client, method, path, *, body=None, query=None, expect=(200,), allow_absent=False
    ):
        verb = method.upper()
        if verb != "GET":
            self.writes.append((verb, path, body))
        if verb == "GET" and path == "/projects":
            return [{"name": "adapteng-ops", "uuid": "project-uuid"}]
        if verb == "GET" and path == "/projects/project-uuid/environments":
            return [{"name": "production", "uuid": "environment-uuid", "id": 2}]
        if verb == "GET" and path == "/applications":
            return [] if self.application is None else [dict(self.application)]
        if verb == "GET" and path == "/servers":
            return [
                {
                    "name": "adapteng-core-01",
                    "uuid": "server-uuid",
                    "destinations": [{"name": "coolify", "uuid": "destination-uuid"}],
                }
            ]
        if verb == "POST" and path == "/applications/dockerfile":
            self.application = {
                "uuid": "application-uuid",
                "name": runner.RUNNER_RESOURCE_NAME,
                "environment_id": 2,
                "dockerfile": body.get("dockerfile"),
                "fqdn": None,
            }
            return {"uuid": "application-uuid"}
        if path == "/applications/application-uuid":
            if verb == "GET":
                return dict(self.application or {})
            self.application.update(body or {})
            return dict(self.application)
        if path == "/applications/application-uuid/envs":
            if verb == "GET":
                return [dict(entry) for entry in self.environment_entries]
            key, value = body["key"], body["value"]
            for entry in self.environment_entries:
                if entry["key"] == key:
                    entry["value"] = value
                    break
            else:
                self.environment_entries.append({"key": key, "value": value})
            return {"uuid": "env-uuid"}
        if verb == "POST" and path == "/deploy":
            return {"deployments": [{"deployment_uuid": "deployment-uuid"}]}
        if verb == "GET" and path == "/deployments/deployment-uuid":
            state = self.deployment_states[0]
            if len(self.deployment_states) > 1:
                self.deployment_states.pop(0)
            return {"status": state}
        if allow_absent:
            return None
        raise AssertionError(f"unexpected call {verb} {path}")


def run_operation(operation, coolify, github, **keywords):
    """Drive one operation against both fakes and return its code and report."""

    saved_call, saved_github = runner.driver.call, runner.github_call
    runner.driver.call = coolify
    runner.github_call = github
    runner.driver.reset_redactions()
    buffer = io.StringIO()
    client = runner.driver.Client("https://example.invalid", "an-example-instance-value")
    try:
        with redirect_stdout(buffer):
            code = operation(client, **keywords)
    finally:
        runner.driver.call, runner.github_call = saved_call, saved_github
        runner.driver.reset_redactions()
    return code, buffer.getvalue()


class DockerfileTests(unittest.TestCase):
    def test_the_image_stops_being_root_before_it_runs_anything(self) -> None:
        """The isolation claim is only true if the last USER is unprivileged.

        Installing packages needs root, so the build takes it. If it were never
        given back, every job this runner executes would run as root on the
        production host, which is the precise authority the design exists to
        avoid holding.
        """

        users = [line for line in runner.DOCKERFILE.split("\n") if line.startswith("USER ")]
        self.assertEqual(users[-1], "USER runner")

    def test_a_database_client_is_installed_because_that_is_the_whole_reason(self) -> None:
        self.assertIn("postgresql-client", runner.DOCKERFILE)
        self.assertIn(f"FROM {runner.RUNNER_IMAGE}:{runner.RUNNER_IMAGE_TAG}", runner.DOCKERFILE)

    def test_the_start_command_is_a_parseable_exec_array(self) -> None:
        """A malformed CMD builds fine and fails only at start, on the host."""

        line = [line for line in runner.DOCKERFILE.split("\n") if line.startswith("CMD ")][0]
        parsed = json.loads(line[len("CMD ") :])
        self.assertEqual(parsed[:2], ["/bin/bash", "-c"])
        self.assertIn("./run.sh", parsed[2])

    def test_stale_configuration_is_cleared_so_a_restart_can_re_register(self) -> None:
        self.assertIn("rm -f .runner", runner.DOCKERFILE)
        self.assertIn("--replace", runner.DOCKERFILE)

    def test_no_credential_is_baked_into_the_image(self) -> None:
        """The registration value must arrive at start, never in a layer."""

        self.assertNotIn(MINTED, runner.DOCKERFILE)
        self.assertIn(f"${runner.REGISTRATION_KEY}", runner.DOCKERFILE)


class ReconcileTests(unittest.TestCase):
    def test_creation_asks_for_the_network_and_refuses_a_public_address(self) -> None:
        """Two settings carry the entire security posture of this container."""

        coolify = FakeCoolify()
        code, report = run_operation(
            runner.operate_reconcile, coolify, FakeGitHub(), repository=REPOSITORY
        )
        self.assertEqual(code, runner.EXIT_OK, report)
        created = [body for verb, path, body in coolify.writes if path.endswith("/dockerfile")]
        self.assertEqual(len(created), 1)
        self.assertIs(created[0]["connect_to_docker_network"], True)
        self.assertIs(created[0]["autogenerate_domain"], False)
        self.assertIs(created[0]["instant_deploy"], False)
        self.assertEqual(created[0]["build_pack"], "dockerfile")

    def test_the_declared_environment_is_written_and_then_verified(self) -> None:
        coolify = FakeCoolify()
        code, report = run_operation(
            runner.operate_reconcile, coolify, FakeGitHub(), repository=REPOSITORY
        )
        self.assertEqual(code, runner.EXIT_OK, report)
        stored = {entry["key"]: entry["value"] for entry in coolify.environment_entries}
        self.assertEqual(stored[runner.SCOPE_URL_KEY], f"https://github.com/{REPOSITORY}")
        self.assertEqual(stored[runner.LABELS_KEY], runner.RUNNER_LABEL)
        self.assertIn("VERIFY OK", report)

    def test_a_build_definition_the_instance_did_not_keep_is_a_failure(self) -> None:
        """The re-read is the only thing standing between a silent no-op and a lie.

        An API that accepts a write and stores something else would otherwise be
        indistinguishable from one that worked, and the difference here is
        whether the runner has a database client at all.
        """

        coolify = FakeCoolify()

        class Truncating(FakeCoolify):
            def __call__(self, client, method, path, **keywords):
                result = FakeCoolify.__call__(self, client, method, path, **keywords)
                if method.upper() == "POST" and path.endswith("/dockerfile"):
                    self.application["dockerfile"] = "FROM scratch\n"
                return result

        coolify = Truncating()
        code, report = run_operation(
            runner.operate_reconcile, coolify, FakeGitHub(), repository=REPOSITORY
        )
        self.assertEqual(code, runner.EXIT_FAILED)
        self.assertIn("VERIFY FAILED", report)
        self.assertIn("stored build definition differs", report)
        self.assertIn("line 1:", report)

    def test_a_public_address_appearing_is_refused_rather_than_removed(self) -> None:
        coolify = FakeCoolify()

        class Routed(FakeCoolify):
            def __call__(self, client, method, path, **keywords):
                result = FakeCoolify.__call__(self, client, method, path, **keywords)
                if method.upper() == "POST" and path.endswith("/dockerfile"):
                    self.application["fqdn"] = "https://runner.example.com"
                return result

        coolify = Routed()
        code, report = run_operation(
            runner.operate_reconcile, coolify, FakeGitHub(), repository=REPOSITORY
        )
        self.assertEqual(code, runner.EXIT_FAILED)
        self.assertIn("public address", report)
        self.assertIn("owner action", report)

    def test_reconciling_twice_creates_nothing_the_second_time(self) -> None:
        coolify = FakeCoolify()
        run_operation(runner.operate_reconcile, coolify, FakeGitHub(), repository=REPOSITORY)
        coolify.writes.clear()
        code, report = run_operation(
            runner.operate_reconcile, coolify, FakeGitHub(), repository=REPOSITORY
        )
        self.assertEqual(code, runner.EXIT_OK, report)
        self.assertEqual(
            [path for _, path, _ in coolify.writes if path.endswith("/dockerfile")], []
        )

    def test_the_unverifiable_setting_is_named_rather_than_quietly_omitted(self) -> None:
        """Claiming everything verified when four settings are unreadable would be false."""

        coolify = FakeCoolify()
        _, report = run_operation(
            runner.operate_reconcile, coolify, FakeGitHub(), repository=REPOSITORY
        )
        self.assertIn("connect_to_docker_network is not", report)
        self.assertIn("first database connection", report)


class DeployTests(unittest.TestCase):
    def test_a_minted_credential_never_reaches_the_deploy_report(self) -> None:
        coolify = FakeCoolify()
        run_operation(runner.operate_reconcile, coolify, FakeGitHub(), repository=REPOSITORY)
        github = FakeGitHub(online=True)
        code, report = run_operation(
            runner.operate_deploy,
            coolify,
            github,
            repository=REPOSITORY,
            credential=ADMIN,
            sleep=lambda _: None,
        )
        self.assertEqual(code, runner.EXIT_OK, report)
        self.assertNotIn(MINTED, report)
        stored = {entry["key"]: entry["value"] for entry in coolify.environment_entries}
        self.assertEqual(stored[runner.REGISTRATION_KEY], MINTED)

    def test_a_built_container_that_never_registers_is_not_a_success(self) -> None:
        """A green deployment says a container started, not that a runner exists.

        Treating the build as the answer would report a working runner while
        every host-side workflow queued against its label waited forever, which
        is the failure this whole workstream exists to stop producing.
        """

        coolify = FakeCoolify()
        run_operation(runner.operate_reconcile, coolify, FakeGitHub(), repository=REPOSITORY)
        github = FakeGitHub(online=False)
        code, report = run_operation(
            runner.operate_deploy,
            coolify,
            github,
            repository=REPOSITORY,
            credential=ADMIN,
            poll_seconds=0,
            registration_timeout_seconds=0,
            sleep=lambda _: None,
        )
        self.assertEqual(code, runner.EXIT_FAILED)
        self.assertIn("build=succeeded", report)
        self.assertIn("runner=", report)

    def test_deploying_before_reconciling_stops_rather_than_creating(self) -> None:
        coolify = FakeCoolify()
        with self.assertRaises(runner.driver.Abort) as caught:
            run_operation(
                runner.operate_deploy,
                coolify,
                FakeGitHub(),
                repository=REPOSITORY,
                credential=ADMIN,
                sleep=lambda _: None,
            )
        self.assertIn("run reconcile first", str(caught.exception))

    def test_a_failed_build_is_not_followed_by_a_registration_wait(self) -> None:
        coolify = FakeCoolify()
        run_operation(runner.operate_reconcile, coolify, FakeGitHub(), repository=REPOSITORY)
        coolify.deployment_states = ["failed"]
        github = FakeGitHub(online=True)
        code, report = run_operation(
            runner.operate_deploy,
            coolify,
            github,
            repository=REPOSITORY,
            credential=ADMIN,
            sleep=lambda _: None,
        )
        self.assertEqual(code, runner.EXIT_FAILED)
        self.assertIn("RESULT deploy failed", report)
        self.assertNotIn("build=succeeded", report)


class StatusTests(unittest.TestCase):
    def test_an_unregistered_runner_is_reported_not_ready(self) -> None:
        coolify = FakeCoolify()
        run_operation(runner.operate_reconcile, coolify, FakeGitHub(), repository=REPOSITORY)
        code, report = run_operation(
            runner.operate_status,
            coolify,
            FakeGitHub(),
            repository=REPOSITORY,
            credential=ADMIN,
        )
        self.assertEqual(code, runner.EXIT_FAILED)
        self.assertIn("runner-unregistered", report)

    def test_an_online_runner_with_the_label_is_ready(self) -> None:
        coolify = FakeCoolify()
        run_operation(runner.operate_reconcile, coolify, FakeGitHub(), repository=REPOSITORY)
        code, report = run_operation(
            runner.operate_status,
            coolify,
            FakeGitHub(online=True),
            repository=REPOSITORY,
            credential=ADMIN,
        )
        self.assertEqual(code, runner.EXIT_OK, report)
        self.assertIn("RESULT status ok", report)
        self.assertIn(runner.RUNNER_LABEL, report)


class EntryPointTests(unittest.TestCase):
    def test_an_unknown_operation_is_refused(self) -> None:
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            code = runner.main(["remove"])
        self.assertEqual(code, runner.EXIT_FAILED)
        self.assertIn("unknown operation", buffer.getvalue())

    def test_every_offered_operation_is_one_the_module_implements(self) -> None:
        """The workflow offers a fixed menu, and a typo in it is a run wasted."""

        text = pathlib.Path(__file__).resolve().parent.parent / ".github/workflows/ops-runner.yml"
        offered = {
            line.strip().lstrip("- ").strip()
            for line in text.read_text(encoding="utf-8").split("\n")
            if line.startswith("          - ")
        }
        self.assertEqual(offered, {"preflight", "reconcile", "deploy", "status"})


if __name__ == "__main__":
    unittest.main()
