"""Tests for the AI Gateway credential binder.

The properties that matter here cannot be recovered if they are ever wrong: a
credential that reaches a log is disclosed for good, and a path variable set
before its file exists turns a missing credential into a boot loop. Both are
pinned by name below.
"""

from __future__ import annotations

import io
import unittest
from contextlib import redirect_stdout
from pathlib import Path

import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts import ai_gateway_credentials as binder  # noqa: E402

# The binder adds scripts/ to sys.path and imports coolify_deploy as a top-level
# module, so importing it here as scripts.coolify_deploy would produce a second,
# separate module object and patching it would silently patch nothing.
driver = binder.driver

SERVICE_ACCOUNT = (
    '{"type": "service_account", "project_id": "adapteng-eu", '
    '"client_email": "gateway@adapteng-eu.iam.gserviceaccount.com", '
    '"private_key": "-----BEGIN PRIVATE KEY-----\\nnot-a-real-key\\n-----END PRIVATE KEY-----\\n", '
    '"token_uri": "https://oauth2.googleapis.com/token"}'
)


class FakeCoolify:
    """A Coolify that records what it was asked to do and can be made forgetful."""

    def __init__(self, storages=None, environment=None, forget_storage=False):
        self.storages = list(storages or [])
        self.environment = list(environment or [])
        self.forget_storage = forget_storage
        self.writes: list[tuple[str, str, dict | None]] = []

    def __call__(self, client, method, path, body=None, expect=(200,), allow_absent=False):
        verb = method.upper()
        if verb == "GET" and path.endswith("/storages"):
            return list(self.storages)
        if verb == "GET" and path.endswith("/envs"):
            return list(self.environment)
        self.writes.append((verb, path, body))
        if path.endswith("/storages"):
            if not self.forget_storage:
                self.storages = [{
                    "uuid": "storage-1",
                    "mount_path": (body or {}).get("mount_path"),
                }]
            return {"uuid": "storage-1"}
        if path.endswith("/envs"):
            entry = {"key": (body or {}).get("key"), "value": (body or {}).get("value")}
            self.environment = [e for e in self.environment if e.get("key") != entry["key"]]
            self.environment.append(entry)
            return entry
        if path.startswith("/projects"):
            return []
        return {}


class SecretDeliveryTests(unittest.TestCase):
    """How the value reaches gh, which no test exercised until it failed live."""

    def setUp(self) -> None:
        self.real_run = binder.subprocess.run
        self.addCleanup(setattr, binder.subprocess, "run", self.real_run)
        self.calls: list[dict] = []

    def serve(self, returncode: int = 0, stderr: str = "") -> None:
        class Completed:
            def __init__(self) -> None:
                self.returncode = returncode
                self.stdout = ""
                self.stderr = stderr

        def fake(command, input=None, capture_output=False, text=False, check=False):
            self.calls.append({"command": list(command), "input": input})
            return Completed()

        binder.subprocess.run = fake

    def test_the_value_is_delivered_on_stdin_and_never_in_the_arguments(self) -> None:
        """Arguments are readable by every other process on the machine."""

        self.serve()
        binder.store_repository_secret("o/r", "NAME", "a-generated-value")
        call = self.calls[0]
        self.assertEqual(call["input"], "a-generated-value")
        self.assertNotIn("a-generated-value", " ".join(call["command"]))

    def test_no_flag_names_the_input_at_all(self) -> None:
        """gh reads stdin when no value is given, in every version.

        --body-file - expressed the same intent and the runner's gh rejected it
        outright, after the other store had already been written. A flag that is
        not passed cannot be unsupported.
        """

        self.serve()
        binder.store_repository_secret("o/r", "NAME", "value")
        command = self.calls[0]["command"]
        for flag in ("--body-file", "--body", "-b", "-f"):
            self.assertNotIn(flag, command)

    def test_a_refusal_aborts_and_reports_the_reason_not_the_value(self) -> None:
        self.serve(returncode=1, stderr="HTTP 403: Resource not accessible")
        with self.assertRaises(driver.Abort) as raised:
            binder.store_repository_secret("o/r", "NAME", "a-generated-value")
        self.assertIn("403", str(raised.exception))
        self.assertNotIn("a-generated-value", str(raised.exception))


class SecretDeliveryTests_Ordering(unittest.TestCase):
    """Which store is written first, chosen for how a half-failure lands."""

    def setUp(self) -> None:
        self.real_store = binder.store_repository_secret
        self.addCleanup(setattr, binder, "store_repository_secret", self.real_store)
        self.order: list[str] = []

    def test_the_caller_copy_is_stored_before_the_gateway_accepts_it(self) -> None:
        """The reverse order failed live: Coolify accepted a credential nobody held.

        That silently revokes every existing caller and reports success up to the
        last line. Storing the caller's copy first makes the same half-failure
        visible instead: the gateway keeps accepting what it accepted before.
        """

        binder.store_repository_secret = lambda repo, name, value: self.order.append("secret")

        class Recording(FakeCoolify):
            def __call__(inner, client, method, path, body=None, expect=(200,), allow_absent=False):
                if method.upper() != "GET" and path.endswith("/envs"):
                    self.order.append("coolify")
                return FakeCoolify.__call__(inner, client, method, path, body, expect, allow_absent)

        run_operation(binder.operate_mint_caller, Recording(), repository="o/r")
        self.assertEqual(self.order[:2], ["secret", "coolify"])


class LocateTests(unittest.TestCase):
    """Exercising the one function that speaks to the driver's real API surface.

    Every other test here replaces locate with a stub, which is what let three
    wrong call signatures reach a live run: the wrong number of arguments, a
    project dict passed where a uuid string was expected, and an unchecked None.
    None of them are visible to a caller that never runs the function. So these
    tests fake only the transport and let the real driver functions run.
    """

    PROJECTS = [{"id": 2, "uuid": "project-uuid", "name": binder.PROJECT_NAME}]
    ENVIRONMENTS = [{"id": 7, "uuid": "env-uuid", "name": binder.ENVIRONMENT_NAME}]
    APPLICATIONS = [
        {"uuid": "gateway-uuid", "name": binder.RESOURCE_NAME, "environment_id": 7},
        {"uuid": "other-uuid", "name": "n8n-selfhosted", "environment_id": 7},
        {"uuid": "elsewhere", "name": binder.RESOURCE_NAME, "environment_id": 99},
    ]

    def setUp(self) -> None:
        self.real_call = driver.call
        self.addCleanup(setattr, driver, "call", self.real_call)
        self.requested: list[str] = []

    def serve(self, projects=None, environments=None, applications=None) -> None:
        def fake(client, method, path, body=None, expect=(200,), allow_absent=False):
            self.requested.append(path)
            if path == "/projects":
                return self.PROJECTS if projects is None else projects
            if path.endswith("/environments"):
                return self.ENVIRONMENTS if environments is None else environments
            if path == "/applications":
                return self.APPLICATIONS if applications is None else applications
            raise AssertionError(f"unexpected request {path}")

        driver.call = fake

    def test_the_gateway_application_is_found_through_the_real_driver(self) -> None:
        self.serve()
        self.assertEqual(binder.locate(None), "gateway-uuid")

    def test_the_project_uuid_is_what_reaches_the_url_not_the_project(self) -> None:
        """A dict interpolated into a path produced a URL with control characters in it."""

        self.serve()
        binder.locate(None)
        self.assertIn("/projects/project-uuid/environments", self.requested)

    def test_an_application_in_another_environment_is_not_mistaken_for_this_one(self) -> None:
        self.serve()
        self.assertEqual(binder.locate(None), "gateway-uuid")

    def test_a_missing_project_is_named_rather_than_dereferenced(self) -> None:
        self.serve(projects=[])
        with self.assertRaises(driver.Abort) as raised:
            binder.locate(None)
        self.assertIn(binder.PROJECT_NAME, str(raised.exception))

    def test_a_missing_environment_is_named_rather_than_dereferenced(self) -> None:
        self.serve(environments=[])
        with self.assertRaises(driver.Abort) as raised:
            binder.locate(None)
        self.assertIn(binder.ENVIRONMENT_NAME, str(raised.exception))

    def test_a_missing_application_says_what_has_to_happen_first(self) -> None:
        self.serve(applications=[])
        with self.assertRaises(driver.Abort) as raised:
            binder.locate(None)
        self.assertIn("reconciled first", str(raised.exception))


def run_operation(operation, coolify: FakeCoolify, **kwargs) -> tuple[int, str]:
    real_call = driver.call
    real_locate = binder.locate
    driver.call = coolify
    binder.locate = lambda client: "app-1"
    buffer = io.StringIO()
    try:
        with redirect_stdout(buffer):
            code = operation(None, **kwargs)
    finally:
        driver.call = real_call
        binder.locate = real_locate
    return code, buffer.getvalue()


class ServiceAccountTests(unittest.TestCase):
    def test_material_that_is_not_a_service_account_is_refused_before_mounting(self) -> None:
        """A wrong value mounts happily and fails at the first model call instead."""

        for bad in ('{"type": "authorized_user"}', "not json at all", "[]"):
            with self.assertRaises(driver.Abort):
                binder.parse_service_account(bad)

    def test_a_key_missing_a_required_field_is_refused(self) -> None:
        with self.assertRaises(driver.Abort) as raised:
            binder.parse_service_account('{"type": "service_account", "project_id": "p"}')
        self.assertIn("client_email", str(raised.exception))

    def test_a_valid_key_is_accepted(self) -> None:
        parsed = binder.parse_service_account(SERVICE_ACCOUNT)
        self.assertEqual(parsed["project_id"], "adapteng-eu")


class BindAdcTests(unittest.TestCase):
    def test_the_key_never_reaches_the_report(self) -> None:
        """The single property that cannot be recovered if it is ever wrong."""

        coolify = FakeCoolify()
        _, report = run_operation(binder.operate_bind_adc, coolify, material=SERVICE_ACCOUNT)
        self.assertNotIn("BEGIN PRIVATE KEY", report)
        self.assertNotIn("not-a-real-key", report)

    def test_the_address_is_reported_because_a_wrong_key_is_otherwise_invisible(self) -> None:
        coolify = FakeCoolify()
        _, report = run_operation(binder.operate_bind_adc, coolify, material=SERVICE_ACCOUNT)
        self.assertIn("gateway@adapteng-eu.iam.gserviceaccount.com", report)

    def test_the_path_is_named_only_after_the_mount_is_recorded(self) -> None:
        """Order is the whole point: the service fails closed on an unreadable path."""

        coolify = FakeCoolify()
        run_operation(binder.operate_bind_adc, coolify, material=SERVICE_ACCOUNT)
        paths = [path for _, path, _ in coolify.writes]
        self.assertLess(
            paths.index(f"/applications/app-1/storages"),
            paths.index(f"/applications/app-1/envs"),
        )

    def test_a_mount_the_instance_does_not_report_stops_before_the_path_is_set(self) -> None:
        """Leaving the variable unset is a no-op; setting it wrongly is a boot loop."""

        coolify = FakeCoolify(forget_storage=True)
        with self.assertRaises(driver.Abort) as raised:
            run_operation(binder.operate_bind_adc, coolify, material=SERVICE_ACCOUNT)
        self.assertIn("boot", str(raised.exception).lower() + "boot")
        self.assertEqual([p for _, p, _ in coolify.writes if p.endswith("/envs")], [])

    def test_an_existing_mount_is_replaced_rather_than_skipped(self) -> None:
        """A rotated key that is silently not delivered is worse than a failure."""

        coolify = FakeCoolify(storages=[{"uuid": "old", "mount_path": binder.ADC_MOUNT_PATH}])
        run_operation(binder.operate_bind_adc, coolify, material=SERVICE_ACCOUNT)
        methods = [verb for verb, path, _ in coolify.writes if path.endswith("/storages")]
        self.assertEqual(methods, ["PATCH"])

    def test_the_variable_names_exactly_the_mount_point(self) -> None:
        coolify = FakeCoolify()
        run_operation(binder.operate_bind_adc, coolify, material=SERVICE_ACCOUNT)
        written = [body for _, path, body in coolify.writes if path.endswith("/envs")][0]
        self.assertEqual(written["key"], binder.ADC_PATH_KEY)
        self.assertEqual(written["value"], binder.ADC_MOUNT_PATH)


class MintCallerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.real_store = binder.store_repository_secret
        self.addCleanup(setattr, binder, "store_repository_secret", self.real_store)
        self.stored: list[tuple[str, str, str]] = []
        binder.store_repository_secret = lambda repo, name, value: self.stored.append(
            (repo, name, value)
        )

    def test_the_generated_credential_never_reaches_the_report(self) -> None:
        coolify = FakeCoolify()
        _, report = run_operation(binder.operate_mint_caller, coolify, repository="o/r")
        credential = self.stored[0][2]
        self.assertNotIn(credential, report)
        self.assertIn("present length=", report)

    def test_both_stores_receive_the_same_value(self) -> None:
        """Two stores written from two generations would disagree silently."""

        coolify = FakeCoolify()
        run_operation(binder.operate_mint_caller, coolify, repository="o/r")
        written = [body for _, path, body in coolify.writes if path.endswith("/envs")][0]
        self.assertEqual(written["value"], self.stored[0][2])

    def test_the_credential_is_long_enough_that_guessing_is_not_a_strategy(self) -> None:
        coolify = FakeCoolify()
        run_operation(binder.operate_mint_caller, coolify, repository="o/r")
        self.assertGreaterEqual(len(self.stored[0][2]), 40)

    def test_a_value_the_instance_does_not_report_is_a_failure_not_a_success(self) -> None:
        """The gateway refusing every caller while the secret store says otherwise."""

        class Forgetful(FakeCoolify):
            def __call__(self, client, method, path, body=None, expect=(200,), allow_absent=False):
                result = super().__call__(client, method, path, body, expect, allow_absent)
                if method.upper() == "GET" and path.endswith("/envs"):
                    return [e for e in result if e.get("key") != binder.CALLER_KEY]
                return result

        with self.assertRaises(driver.Abort):
            run_operation(binder.operate_mint_caller, Forgetful(), repository="o/r")


class StatusTests(unittest.TestCase):
    def test_a_path_naming_a_mount_that_is_not_there_is_reported_as_a_problem(self) -> None:
        coolify = FakeCoolify(
            environment=[{"key": binder.ADC_PATH_KEY, "value": binder.ADC_MOUNT_PATH}]
        )
        code, report = run_operation(binder.operate_status, coolify)
        self.assertEqual(code, 1)
        self.assertIn("PROBLEM", report)

    def test_a_complete_binding_is_reported_as_bound(self) -> None:
        coolify = FakeCoolify(
            storages=[{"uuid": "s", "mount_path": binder.ADC_MOUNT_PATH}],
            environment=[
                {"key": binder.ADC_PATH_KEY, "value": binder.ADC_MOUNT_PATH},
                {"key": binder.CALLER_KEY, "value": "x"},
            ],
        )
        code, report = run_operation(binder.operate_status, coolify)
        self.assertEqual(code, 0)
        self.assertIn("credentials=bound", report)

    def test_status_writes_nothing(self) -> None:
        """A survey that changes state is not a survey."""

        coolify = FakeCoolify()
        run_operation(binder.operate_status, coolify)
        self.assertEqual(coolify.writes, [])

    def test_a_stored_caller_value_is_never_printed(self) -> None:
        coolify = FakeCoolify(
            environment=[{"key": binder.CALLER_KEY, "value": "a-value-that-must-not-appear"}]
        )
        _, report = run_operation(binder.operate_status, coolify)
        self.assertNotIn("a-value-that-must-not-appear", report)


class WorkflowTests(unittest.TestCase):
    def test_the_workflow_offers_exactly_the_implemented_operations(self) -> None:
        """A menu entry with no implementation fails only when someone runs it."""

        text = Path(__file__).resolve().parent.parent.joinpath(
            ".github/workflows/ai-gateway-credentials.yml"
        ).read_text(encoding="utf-8")
        for operation in ("status", "bind-adc", "mint-caller"):
            self.assertIn(f"- {operation}", text)

    def test_the_workflow_passes_the_material_by_reference(self) -> None:
        text = Path(__file__).resolve().parent.parent.joinpath(
            ".github/workflows/ai-gateway-credentials.yml"
        ).read_text(encoding="utf-8")
        self.assertIn("VERTEX_SERVICE_ACCOUNT_MATERIAL", text)
        self.assertIn("GOOGLE_SERVICE_ACCOUNT_JSON", text)


if __name__ == "__main__":
    unittest.main()
