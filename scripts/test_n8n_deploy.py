#!/usr/bin/env python3
"""Regression controls for the self-hosted n8n driver.

The central risk this file exists to catch is the one already hit once this
mission: silently operating against the wrong n8n account. Every test here
is either about that detection, or about the write operations never
touching a workflow the driver was not explicitly told to touch.
"""

from __future__ import annotations

import io
import json
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

import n8n_deploy as driver_module  # noqa: E402
import coolify_deploy as driver  # noqa: E402


class FakeN8nClient:
    """A scripted stand-in for N8nClient.request.

    ``responses`` maps (method, path) to either a (status, body) tuple or a
    callable returning one, so a test can assert on the exact body sent.
    """

    def __init__(self, responses: dict) -> None:
        self.responses = responses
        self.calls: list[tuple[str, str, object, object]] = []
        self.base_url = "https://n8n.adapteng.com"

    def request(self, method, path, body=None, query=None):
        self.calls.append((method.upper(), path, body, query))
        key = (method.upper(), path)
        if key not in self.responses:
            raise AssertionError(f"unscripted request: {key}")
        outcome = self.responses[key]
        if callable(outcome):
            return outcome(body, query)
        return outcome


def workflows_page(items: list[dict], next_cursor: str | None = None) -> tuple[int, dict]:
    return 200, {"data": items, "nextCursor": next_cursor}


AUT_001 = {"id": "NsWG1hD8VmIRRwCv", "name": "AUT-001", "active": True}
WEB_002 = {"id": "05ytz5If9kHUOYuA", "name": "WEB-002", "active": True}
L1_PROOF = {"id": "some-l1-id", "name": "L1 proof", "active": False}
SELF_HOSTED_SET = [AUT_001, WEB_002, L1_PROOF]


class StatusTests(unittest.TestCase):
    def run_status(self, client):
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            code = driver_module.operate_status(client)
        return code, buffer.getvalue()

    def test_self_hosted_markers_present_is_ok(self) -> None:
        client = FakeN8nClient({
            ("GET", "/workflows"): workflows_page(SELF_HOSTED_SET),
        })
        code, output = self.run_status(client)
        self.assertEqual(code, driver.EXIT_OK)
        self.assertIn("RESULT status ok", output)
        self.assertIn("confirmed_self_hosted=true", output)

    def test_n8n_cloud_marker_present_is_a_hard_failure(self) -> None:
        """Even if the self-hosted markers are also somehow present, the
        cloud marker alone must stop this being treated as the right
        target."""

        client = FakeN8nClient({
            ("GET", "/workflows"): workflows_page(
                SELF_HOSTED_SET + [{"id": driver_module.KNOWN_N8N_CLOUD_WORKFLOW_ID, "name": "MM-08", "active": False}]
            ),
        })
        code, output = self.run_status(client)
        self.assertEqual(code, driver.EXIT_FAILED)
        self.assertIn("wrong_instance_n8n_cloud", output)

    def test_no_known_markers_is_a_failure_not_a_guess(self) -> None:
        client = FakeN8nClient({
            ("GET", "/workflows"): workflows_page([{"id": "unknown", "name": "something", "active": False}]),
        })
        code, output = self.run_status(client)
        self.assertEqual(code, driver.EXIT_FAILED)
        self.assertIn("self_hosted_markers_absent", output)

    def test_pagination_is_followed_to_completion(self) -> None:
        calls = {"n": 0}

        def page(_body, query):
            calls["n"] += 1
            if query.get("cursor") is None:
                return workflows_page([AUT_001], next_cursor="page2")
            return workflows_page([WEB_002, L1_PROOF], next_cursor=None)

        client = FakeN8nClient({("GET", "/workflows"): page})
        code, output = self.run_status(client)
        self.assertEqual(code, driver.EXIT_OK)
        self.assertEqual(calls["n"], 2)
        self.assertIn("workflow count: 3", output)


class ExportTests(unittest.TestCase):
    def test_export_writes_one_file_per_workflow_plus_a_manifest(self) -> None:
        client = FakeN8nClient({
            ("GET", "/workflows"): workflows_page(SELF_HOSTED_SET),
            ("GET", f"/workflows/{AUT_001['id']}"): (200, {**AUT_001, "nodes": []}),
            ("GET", f"/workflows/{WEB_002['id']}"): (200, {**WEB_002, "nodes": []}),
            ("GET", f"/workflows/{L1_PROOF['id']}"): (200, {**L1_PROOF, "nodes": []}),
        })
        with TemporaryDirectory() as tmp:
            out = Path(tmp) / "export"
            buffer = io.StringIO()
            with redirect_stdout(buffer):
                code = driver_module.operate_export(client, out)
            self.assertEqual(code, driver.EXIT_OK)
            for item in SELF_HOSTED_SET:
                self.assertTrue((out / f"{item['id']}.json").exists())
            manifest = json.loads((out / "_manifest.json").read_text())
            self.assertEqual(manifest["exported_count"], 3)
            self.assertEqual(len(manifest["workflow_ids"]), 3)

    def test_export_never_issues_a_write(self) -> None:
        client = FakeN8nClient({
            ("GET", "/workflows"): workflows_page([AUT_001]),
            ("GET", f"/workflows/{AUT_001['id']}"): (200, {**AUT_001, "nodes": []}),
        })
        with TemporaryDirectory() as tmp:
            with redirect_stdout(io.StringIO()):
                driver_module.operate_export(client, Path(tmp) / "export")
        methods = {call[0] for call in client.calls}
        self.assertEqual(methods, {"GET"})


class ImportCanaryTests(unittest.TestCase):
    def definition(self, **overrides):
        base = {
            "name": driver_module.CANARY_NAME,
            "nodes": [{"name": "Manual Trigger"}],
            "connections": {},
            "settings": {"executionOrder": "v1"},
        }
        base.update(overrides)
        return base

    def run_import(self, client, definition=None):
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            code = driver_module.operate_import_canary(client, definition or self.definition())
        return code, buffer.getvalue()

    def test_wrong_name_in_the_source_file_is_refused_before_any_call(self) -> None:
        client = FakeN8nClient({})
        with self.assertRaises(driver.Abort):
            self.run_import(client, self.definition(name="something else"))
        self.assertEqual(client.calls, [])

    def test_creates_when_no_existing_canary(self) -> None:
        client = FakeN8nClient({
            ("GET", "/workflows"): workflows_page(SELF_HOSTED_SET),
            ("POST", "/workflows"): (201, {"id": "new-canary-id", "active": False}),
        })
        code, output = self.run_import(client)
        self.assertEqual(code, driver.EXIT_OK)
        self.assertIn("id=new-canary-id", output)
        post_calls = [c for c in client.calls if c[0] == "POST"]
        self.assertEqual(len(post_calls), 1)
        sent_body = post_calls[0][2]
        self.assertEqual(set(sent_body), {"name", "nodes", "connections", "settings"})

    def test_updates_in_place_when_one_existing_canary(self) -> None:
        existing = {"id": "existing-canary-id", "name": driver_module.CANARY_NAME, "active": False}
        client = FakeN8nClient({
            ("GET", "/workflows"): workflows_page(SELF_HOSTED_SET + [existing]),
            ("PUT", f"/workflows/{existing['id']}"): (200, {"id": existing["id"], "active": False}),
        })
        code, output = self.run_import(client)
        self.assertEqual(code, driver.EXIT_OK)
        self.assertIn(f"id={existing['id']}", output)
        self.assertEqual([c for c in client.calls if c[0] == "POST"], [])

    def test_two_existing_canaries_is_refused_rather_than_guessed(self) -> None:
        dupe_a = {"id": "a", "name": driver_module.CANARY_NAME, "active": False}
        dupe_b = {"id": "b", "name": driver_module.CANARY_NAME, "active": False}
        client = FakeN8nClient({
            ("GET", "/workflows"): workflows_page([dupe_a, dupe_b]),
        })
        with self.assertRaises(driver.Abort):
            self.run_import(client)

    def test_an_unrelated_workflow_is_never_touched(self) -> None:
        """The only GET/PUT/POST paths touched must be /workflows and the
        exact existing-canary id, never AUT-001/WEB-002/anything else."""

        client = FakeN8nClient({
            ("GET", "/workflows"): workflows_page(SELF_HOSTED_SET),
            ("POST", "/workflows"): (201, {"id": "new-canary-id", "active": False}),
        })
        self.run_import(client)
        touched_paths = {call[1] for call in client.calls}
        self.assertEqual(touched_paths, {"/workflows"})

    def test_a_response_reporting_active_true_is_refused(self) -> None:
        client = FakeN8nClient({
            ("GET", "/workflows"): workflows_page([]),
            ("POST", "/workflows"): (201, {"id": "new-canary-id", "active": True}),
        })
        with self.assertRaises(driver.Abort):
            self.run_import(client)


class RunCanaryTests(unittest.TestCase):
    def run_run(self, client):
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            code = driver_module.operate_run_canary(client)
        return code, buffer.getvalue()

    def test_canary_not_found_is_a_failure(self) -> None:
        client = FakeN8nClient({("GET", "/workflows"): workflows_page(SELF_HOSTED_SET)})
        code, output = self.run_run(client)
        self.assertEqual(code, driver.EXIT_FAILED)
        self.assertIn("canary_not_found", output)

    def test_a_404_on_run_reports_the_api_does_not_support_execution(self) -> None:
        canary = {"id": "canary-1", "name": driver_module.CANARY_NAME, "active": False}
        client = FakeN8nClient({
            ("GET", "/workflows"): workflows_page([canary]),
            ("POST", f"/workflows/{canary['id']}/run"): (404, {"message": "not found"}),
        })
        code, output = self.run_run(client)
        self.assertEqual(code, driver.EXIT_FAILED)
        self.assertIn("api_execute_unsupported", output)
        self.assertIn("n8n UI", output)

    def test_a_405_on_run_also_reports_the_api_does_not_support_execution(self) -> None:
        """Confirmed against the live instance: it answers 405, not 404."""

        canary = {"id": "canary-1", "name": driver_module.CANARY_NAME, "active": False}
        client = FakeN8nClient({
            ("GET", "/workflows"): workflows_page([canary]),
            ("POST", f"/workflows/{canary['id']}/run"): (405, {"message": "POST method not allowed"}),
        })
        code, output = self.run_run(client)
        self.assertEqual(code, driver.EXIT_FAILED)
        self.assertIn("api_execute_unsupported", output)

    def test_a_successful_run_reports_the_execution_id(self) -> None:
        canary = {"id": "canary-1", "name": driver_module.CANARY_NAME, "active": False}
        client = FakeN8nClient({
            ("GET", "/workflows"): workflows_page([canary]),
            ("POST", f"/workflows/{canary['id']}/run"): (200, {"executionId": "exec-42"}),
        })
        code, output = self.run_run(client)
        self.assertEqual(code, driver.EXIT_OK)
        self.assertIn("execution_id=exec-42", output)


class SubstituteCredentialIdsTests(unittest.TestCase):
    def definition_with_placeholders(self) -> dict:
        return {
            "name": driver_module.CANARY_NAME,
            "nodes": [
                {"name": "Manual Trigger"},
                {
                    "name": "Call Governed Agent Runtime",
                    "credentials": {
                        "httpHeaderAuth": {
                            "id": driver_module.PLACEHOLDER_CREDENTIAL_IDS["agent-runtime"],
                            "name": "placeholder",
                        }
                    },
                },
                {
                    "name": "Create Baserow Draft",
                    "credentials": {
                        "httpHeaderAuth": {
                            "id": driver_module.PLACEHOLDER_CREDENTIAL_IDS["baserow-adapter"],
                            "name": "placeholder",
                        }
                    },
                },
                {
                    "name": "Ensure Canary Drive Folder",
                    "credentials": {
                        "httpHeaderAuth": {
                            "id": driver_module.PLACEHOLDER_CREDENTIAL_IDS["drive-adapter"],
                            "name": "placeholder",
                        }
                    },
                },
                {
                    "name": "Upload Canary Draft Artifact",
                    "credentials": {
                        "httpHeaderAuth": {
                            "id": driver_module.PLACEHOLDER_CREDENTIAL_IDS["drive-adapter"],
                            "name": "placeholder",
                        }
                    },
                },
                {"name": "Build Redacted Result"},
            ],
        }

    def test_every_placeholder_is_replaced_with_its_real_id(self) -> None:
        real_ids = {"agent-runtime": "id-a", "baserow-adapter": "id-b", "drive-adapter": "id-d"}
        updated = driver_module.substitute_credential_ids(
            self.definition_with_placeholders(), real_ids
        )
        found = {
            node["name"]: node["credentials"]["httpHeaderAuth"]["id"]
            for node in updated["nodes"]
            if "credentials" in node
        }
        self.assertEqual(found["Call Governed Agent Runtime"], "id-a")
        self.assertEqual(found["Create Baserow Draft"], "id-b")
        self.assertEqual(found["Ensure Canary Drive Folder"], "id-d")
        self.assertEqual(found["Upload Canary Draft Artifact"], "id-d")

    def test_the_input_definition_is_not_mutated(self) -> None:
        original = self.definition_with_placeholders()
        original_agent_runtime_id = original["nodes"][1]["credentials"]["httpHeaderAuth"]["id"]
        driver_module.substitute_credential_ids(
            original, {"agent-runtime": "id-a", "baserow-adapter": "id-b", "drive-adapter": "id-d"}
        )
        self.assertEqual(
            original["nodes"][1]["credentials"]["httpHeaderAuth"]["id"],
            original_agent_runtime_id,
        )

    def test_a_missing_placeholder_is_refused_rather_than_silently_partial(self) -> None:
        definition = self.definition_with_placeholders()
        # Simulate the source file's shape having changed under us.
        definition["nodes"][1]["credentials"]["httpHeaderAuth"]["id"] = "already-something-else"
        with self.assertRaises(driver.Abort):
            driver_module.substitute_credential_ids(
                definition,
                {"agent-runtime": "id-a", "baserow-adapter": "id-b", "drive-adapter": "id-d"},
            )


class ReadBaserowAdapterTokenTests(unittest.TestCase):
    def test_returns_the_value_and_registers_it_for_redaction(self) -> None:
        coolify_client = object()
        with mock.patch.object(
            driver,
            "read_environment_entries",
            return_value=[{"key": "OTHER", "value": "x"}, {"key": "ADAPTER_SERVICE_TOKEN", "value": "the-token"}],
        ):
            with mock.patch.object(driver, "register_redaction") as redact:
                value = driver_module.read_baserow_adapter_token(coolify_client, "uuid-1")
        self.assertEqual(value, "the-token")
        redact.assert_called_once_with("the-token")

    def test_a_missing_key_aborts(self) -> None:
        coolify_client = object()
        with mock.patch.object(driver, "read_environment_entries", return_value=[{"key": "OTHER", "value": "x"}]):
            with self.assertRaises(driver.Abort):
                driver_module.read_baserow_adapter_token(coolify_client, "uuid-1")


class BindCanaryCredentialsTests(unittest.TestCase):
    def definition(self) -> dict:
        return {
            "name": driver_module.CANARY_NAME,
            "nodes": [
                {
                    "name": n,
                    "credentials": {
                        "httpHeaderAuth": {"id": placeholder, "name": "placeholder"}
                    },
                }
                for n, placeholder in (
                    ("agent-runtime node", driver_module.PLACEHOLDER_CREDENTIAL_IDS["agent-runtime"]),
                    ("baserow node", driver_module.PLACEHOLDER_CREDENTIAL_IDS["baserow-adapter"]),
                    ("drive node a", driver_module.PLACEHOLDER_CREDENTIAL_IDS["drive-adapter"]),
                    ("drive node b", driver_module.PLACEHOLDER_CREDENTIAL_IDS["drive-adapter"]),
                )
            ],
            "connections": {},
            "settings": {},
        }

    def test_creates_three_credentials_and_reimports_with_real_ids(self) -> None:
        created_bodies: list[dict] = []

        def credentials_post(body, _query):
            created_bodies.append(body)
            return 201, {"id": f"cred-{len(created_bodies)}"}

        client = FakeN8nClient({
            ("POST", "/credentials"): credentials_post,
            ("GET", "/workflows"): workflows_page([]),
            ("POST", "/workflows"): (201, {"id": "canary-id", "active": False}),
        })
        with mock.patch.object(
            driver, "read_environment_entries",
            return_value=[{"key": "ADAPTER_SERVICE_TOKEN", "value": "baserow-secret-value"}],
        ):
            with mock.patch.object(driver, "register_redaction"):
                buffer = io.StringIO()
                with redirect_stdout(buffer):
                    code = driver_module.operate_bind_canary_credentials(
                        client,
                        self.definition(),
                        coolify_client=object(),
                        baserow_adapter_uuid="uuid-1",
                        agent_runtime_token="agent-token-value",
                        drive_adapter_token="drive-token-value,second-token",
                    )
        self.assertEqual(code, driver.EXIT_OK)
        self.assertEqual(len(created_bodies), 3)
        for body in created_bodies:
            self.assertEqual(body["type"], "httpHeaderAuth")
            self.assertEqual(body["data"]["name"], "Authorization")
        values = {body["data"]["value"] for body in created_bodies}
        self.assertEqual(
            values,
            {"Bearer agent-token-value", "Bearer baserow-secret-value", "Bearer drive-token-value"},
        )
        # None of the raw secret values leaked into the printed log.
        self.assertNotIn("agent-token-value", buffer.getvalue())
        self.assertNotIn("baserow-secret-value", buffer.getvalue())
        self.assertNotIn("drive-token-value", buffer.getvalue())

        post_workflow_body = next(
            body for method, path, body, _q in client.calls
            if method == "POST" and path == "/workflows"
        )
        for node in post_workflow_body["nodes"]:
            credential_id = node["credentials"]["httpHeaderAuth"]["id"]
            self.assertTrue(credential_id.startswith("cred-"))

    def test_a_missing_agent_runtime_token_aborts_before_any_call(self) -> None:
        client = FakeN8nClient({})
        with self.assertRaises(driver.Abort):
            driver_module.operate_bind_canary_credentials(
                client,
                self.definition(),
                coolify_client=object(),
                baserow_adapter_uuid="uuid-1",
                agent_runtime_token="",
                drive_adapter_token="drive-token-value",
            )
        self.assertEqual(client.calls, [])


def execution_detail(node_name: str, output_json: dict | None) -> dict:
    run_data = {}
    if output_json is not None:
        run_data[node_name] = [{"data": {"main": [[{"json": output_json}]]}}]
    return {"data": {"resultData": {"runData": run_data}}}


class ExtractRedactedResultTests(unittest.TestCase):
    def test_the_real_node_shape_is_extracted(self) -> None:
        payload = {"baserow_business_id": "AE-SYS-governed-agent-pilot-canary", "drive_file_id": "abc123"}
        detail = execution_detail(driver_module.REDACTED_RESULT_NODE, payload)
        self.assertEqual(driver_module.extract_redacted_result(detail), payload)

    def test_a_missing_node_returns_none_rather_than_raising(self) -> None:
        detail = execution_detail("Some Other Node", {"x": 1})
        self.assertIsNone(driver_module.extract_redacted_result(detail))

    def test_an_empty_items_list_returns_none(self) -> None:
        detail = {
            "data": {"resultData": {"runData": {
                driver_module.REDACTED_RESULT_NODE: [{"data": {"main": [[]]}}]
            }}}
        }
        self.assertIsNone(driver_module.extract_redacted_result(detail))

    def test_a_completely_unexpected_shape_returns_none(self) -> None:
        self.assertIsNone(driver_module.extract_redacted_result({}))
        self.assertIsNone(driver_module.extract_redacted_result({"data": {}}))
        self.assertIsNone(driver_module.extract_redacted_result({"data": None}))


class CanaryExecutionsTests(unittest.TestCase):
    def test_lists_executions_and_reports_the_redacted_result(self) -> None:
        canary = {"id": "canary-1", "name": driver_module.CANARY_NAME, "active": False}
        payload = {"baserow_business_id": "AE-SYS-governed-agent-pilot-canary", "drive_folder_id": "f1"}
        client = FakeN8nClient({
            ("GET", "/workflows"): workflows_page([canary]),
            ("GET", "/executions"): (200, {"data": [{"id": "exec-1", "status": "success"}]}),
            ("GET", "/executions/exec-1"): (200, execution_detail(driver_module.REDACTED_RESULT_NODE, payload)),
        })
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            code = driver_module.operate_canary_executions(client, limit=5)
        self.assertEqual(code, driver.EXIT_OK)
        self.assertIn("execution id=exec-1", buffer.getvalue())
        self.assertIn("baserow_business_id: AE-SYS-governed-agent-pilot-canary", buffer.getvalue())
        self.assertIn("drive_folder_id: f1", buffer.getvalue())
        self.assertIn("RESULT canary-executions ok count=1", buffer.getvalue())

    def test_canary_not_found_is_a_failure(self) -> None:
        client = FakeN8nClient({("GET", "/workflows"): workflows_page(SELF_HOSTED_SET)})
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            code = driver_module.operate_canary_executions(client, limit=5)
        self.assertEqual(code, driver.EXIT_FAILED)
        self.assertIn("canary_not_found", buffer.getvalue())

    def test_a_missing_redacted_result_is_reported_not_hidden(self) -> None:
        canary = {"id": "canary-1", "name": driver_module.CANARY_NAME, "active": False}
        client = FakeN8nClient({
            ("GET", "/workflows"): workflows_page([canary]),
            ("GET", "/executions"): (200, {"data": [{"id": "exec-1", "status": "error"}]}),
            ("GET", "/executions/exec-1"): (200, execution_detail("Some Other Node", {"x": 1})),
        })
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            driver_module.operate_canary_executions(client, limit=5)
        self.assertIn("output: not available", buffer.getvalue())


class N8nClientTests(unittest.TestCase):
    def test_delete_is_refused_before_any_network_call(self) -> None:
        client = driver_module.N8nClient("https://n8n.adapteng.com", "fake-credential")
        with self.assertRaises(driver.Abort):
            client.request("DELETE", "/workflows/x")

    def test_auth_header_is_x_n8n_api_key_not_bearer(self) -> None:
        captured = {}

        class FakeResponse:
            status = 200

            def read(self):
                return b'{"data": []}'

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        def fake_urlopen(request, timeout):
            captured["headers"] = dict(request.header_items())
            return FakeResponse()

        client = driver_module.N8nClient("https://n8n.adapteng.com", "fake-credential")
        with mock.patch.object(driver_module.urllib.request, "urlopen", fake_urlopen):
            client.request("GET", "/workflows")
        self.assertEqual(captured["headers"].get("X-n8n-api-key"), "fake-credential")
        self.assertNotIn("Authorization", captured["headers"])


class MainEntryTests(unittest.TestCase):
    def test_missing_base_url_aborts_before_any_credential_use(self) -> None:
        with mock.patch.dict("os.environ", {}, clear=False):
            import os as _os

            _os.environ.pop(driver_module.BASE_URL_VARIABLE, None)
            _os.environ.pop(driver_module.CREDENTIAL_VARIABLE, None)
            buffer = io.StringIO()
            with redirect_stdout(buffer):
                code = driver_module.main(["status"])
            self.assertEqual(code, driver.EXIT_FAILED)
            self.assertIn(driver_module.BASE_URL_VARIABLE, buffer.getvalue())

    def test_missing_credential_aborts(self) -> None:
        with mock.patch.dict(
            "os.environ",
            {driver_module.BASE_URL_VARIABLE: "https://n8n.adapteng.com"},
        ):
            import os as _os

            _os.environ.pop(driver_module.CREDENTIAL_VARIABLE, None)
            buffer = io.StringIO()
            with redirect_stdout(buffer):
                code = driver_module.main(["status"])
            self.assertEqual(code, driver.EXIT_FAILED)
            self.assertIn(driver_module.CREDENTIAL_VARIABLE, buffer.getvalue())

    def test_import_canary_without_canary_file_is_refused(self) -> None:
        with mock.patch.dict(
            "os.environ",
            {
                driver_module.BASE_URL_VARIABLE: "https://n8n.adapteng.com",
                driver_module.CREDENTIAL_VARIABLE: "fake-credential",
            },
        ):
            buffer = io.StringIO()
            with redirect_stdout(buffer):
                code = driver_module.main(["import-canary"])
            self.assertEqual(code, driver.EXIT_FAILED)
            self.assertIn("--canary-file", buffer.getvalue())


if __name__ == "__main__":
    unittest.main()
