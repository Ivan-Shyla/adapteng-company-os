#!/usr/bin/env python3
"""Regression controls for the bounded n8n Cloud export.

The risks this file exists to catch: reaching the wrong instance, issuing
anything but a GET, asking for execution payloads or any path outside the
allowlist, reading a data table that is not one of the eight, and writing
secret-shaped values to the output.
"""

from __future__ import annotations

import io
import json
import re
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

import coolify_deploy as driver  # noqa: E402
import n8n_cloud_export as export  # noqa: E402

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
WORKFLOW_FILE = REPOSITORY_ROOT / ".github" / "workflows" / "n8n-cloud-export.yml"
CLOUD_URL = "https://example.app.n8n.cloud"
MM_08 = {"id": "RAPjKSnj6EY7axtb", "name": "MM-08"}
MM_20 = {"id": "gJMEoOMUI0uS0D1Y", "name": "MM-20 Publication Scout"}
MM_21 = {"id": "kB21JwdMDZt7nT5a", "name": "MM-21 Publication Fetcher"}


class FakeCloud(export.Cloud):
    """Applies the real allowlist, then answers from a script."""

    def __init__(self, routes: dict) -> None:
        super().__init__(CLOUD_URL, "test-value-not-a-real-key")
        self.routes = routes
        self.calls: list[tuple[str, dict]] = []

    def get(self, path, query=None):
        export.check_request(path, query)
        self.calls.append((path, dict(query or {})))
        outcome = self.routes.get(path)
        if outcome is None:
            return 404, {"message": "not found"}
        return outcome(query) if callable(outcome) else outcome


def page(items, cursor=None):
    return 200, {"data": items, "nextCursor": cursor}


def full_routes(extra_tables=(), skip=()):
    tables = [{"id": f"T{i:03d}abc", "name": name} for i, name in enumerate(export.EXPORT_TABLES) if name not in skip]
    tables += list(extra_tables)
    routes = {
        "/workflows": page([MM_08, MM_20, MM_21]),
        "/data-tables": page(tables),
    }
    for item in (MM_08, MM_20, MM_21):
        routes[f"/workflows/{item['id']}"] = (200, {"id": item["id"], "name": item["name"], "nodes": [], "pinData": {"x": [{"json": {"a": 1}}]}})
    for table in tables:
        routes[f"/data-tables/{table['id']}/rows"] = page([{"id": 1, "title": "row of " + table["name"]}])
    routes["/executions"] = lambda q: page(
        [
            {"id": "3", "workflowId": q["workflowId"], "status": "success", "startedAt": "2026-10-19T02:00:05Z", "stoppedAt": "2026-10-19T02:00:09Z", "mode": "trigger", "finished": True, "data": {"secret": "x"}},
            {"id": "1", "workflowId": q["workflowId"], "status": "success", "startedAt": "2026-01-01T00:00:00Z", "mode": "trigger", "finished": True},
        ]
    )
    return routes


class BaseUrlTests(unittest.TestCase):
    def test_cloud_address_is_accepted(self) -> None:
        self.assertEqual(export.check_base_url(CLOUD_URL + "/"), CLOUD_URL)

    def test_self_hosted_and_foreign_hosts_are_refused(self) -> None:
        for bad in ("https://n8n.adapteng.com", "https://evil.example.com", "http://x.app.n8n.cloud", "https://x.app.n8n.cloud/api", "https://x.app.n8n.cloud/?a=1", ""):
            with self.subTest(bad=bad), self.assertRaises(driver.Abort):
                export.check_base_url(bad)


class AllowlistTests(unittest.TestCase):
    def allowed(self, path, query=None):
        export.check_request(path, query)

    def refused(self, path, query=None):
        with self.assertRaises(driver.Abort):
            export.check_request(path, query)

    def test_expected_reads_pass(self) -> None:
        self.allowed("/workflows", {"limit": 100})
        self.allowed("/workflows/gJMEoOMUI0uS0D1Y")
        self.allowed("/executions", {"includeData": "false", "workflowId": "kB21JwdMDZt7nT5a", "limit": 100})
        self.allowed("/data-tables", {"limit": 100})
        self.allowed("/datatables/T001abc/rows", {"limit": 100, "cursor": "c"})

    def test_everything_else_is_refused(self) -> None:
        for path in ("/credentials", "/users", "/audit", "/variables", "/workflows/abc12345/activate", "/workflows/abc12345/transfer", "/executions/123", "/data-tables/T001abc", "/projects", "/tags"):
            with self.subTest(path=path):
                self.refused(path)

    def test_execution_payloads_cannot_be_requested(self) -> None:
        self.refused("/executions", {"workflowId": MM_20["id"]})
        self.refused("/executions", {"workflowId": MM_20["id"], "includeData": "true"})

    def test_executions_are_limited_to_mm20_and_mm21(self) -> None:
        self.refused("/executions", {"workflowId": "RAPjKSnj6EY7axtb", "includeData": "false"})
        self.refused("/executions", {"includeData": "false"})

    def test_unknown_query_parameters_are_refused(self) -> None:
        self.refused("/workflows", {"active": "true"})
        self.refused("/data-tables", {"filter": "x"})

    def test_client_issues_only_get(self) -> None:
        sent = []

        def fake_open(message, timeout=None):
            sent.append(message)
            raise OSError("stop")

        with mock.patch("urllib.request.urlopen", fake_open):
            with self.assertRaises(driver.Abort):
                export.Cloud(CLOUD_URL, "k" * 16).get("/workflows", {"limit": 1})
        self.assertEqual([m.get_method() for m in sent], ["GET"])
        self.assertEqual(sent[0].full_url, CLOUD_URL + "/api/v1/workflows?limit=1")
        self.assertFalse(any(hasattr(export.Cloud, name) for name in ("post", "put", "patch", "delete")))


class InstanceGuardTests(unittest.TestCase):
    def test_cloud_is_confirmed_by_mm_08(self) -> None:
        export.confirm_cloud([MM_08, MM_20])

    def test_self_hosted_markers_stop_the_run(self) -> None:
        with self.assertRaises(driver.Abort):
            export.confirm_cloud([MM_08, {"id": "NsWG1hD8VmIRRwCv"}])

    def test_unconfirmed_account_stops_the_run(self) -> None:
        with self.assertRaises(driver.Abort):
            export.confirm_cloud([MM_20])


class RedactionTests(unittest.TestCase):
    def test_workflow_is_cleaned(self) -> None:
        definition = {
            "id": "w1",
            "pinData": {"Node": [{"json": {"email": "a@b.c"}}]},
            "staticData": {"cursor": 1},
            "nodes": [
                {
                    "name": "Call",
                    "credentials": {"httpHeaderAuth": {"id": "c1", "name": "Header", "data": "SHOULDNOTSURVIVE"}},
                    "parameters": {
                        "authentication": "genericCredentialType",
                        "headerParameters": {"parameters": [{"name": "Authorization", "value": "Bearer abcdefghijklmnop1234"}]},
                        "apiKey": "sk1234567890abcdefgh",
                        "url": "https://api.example.com/x",
                        "token": "={{ $credentials.token }}",
                    },
                }
            ],
        }
        cleaned, hits = export.clean_workflow(definition)
        text = json.dumps(cleaned)
        self.assertNotIn("pinData", cleaned)
        self.assertNotIn("staticData", cleaned)
        self.assertNotIn("SHOULDNOTSURVIVE", text)
        self.assertNotIn("abcdefghijklmnop1234", text)
        self.assertNotIn("sk1234567890abcdefgh", text)
        self.assertIn("genericCredentialType", text)
        self.assertIn("$credentials.token", text)
        self.assertEqual(cleaned["nodes"][0]["credentials"]["httpHeaderAuth"], {"id": "c1", "name": "Header"})
        self.assertGreaterEqual(hits, 2)

    def test_secret_shaped_text_in_rows_is_masked_and_counted(self) -> None:
        row = {"title": "Normal", "notes": "call with Bearer abcdefghijklmnopqrstuv please"}
        cleaned, hits = export.scrub(row)
        self.assertEqual(cleaned["title"], "Normal")
        self.assertNotIn("abcdefghijklmnopqrstuv", json.dumps(cleaned))
        self.assertEqual(hits, 1)

    def test_ordinary_ids_and_urls_are_kept(self) -> None:
        row = {"drive_file_id": "1AbCdEfGhIjKlMnOpQrStUvWx", "source_url": "https://example.org/a"}
        self.assertEqual(export.scrub(row), (row, 0))


class ExecutionTests(unittest.TestCase):
    def test_metadata_only_and_stops_at_cutoff(self) -> None:
        client = FakeCloud(full_routes())
        rows = export.executions_since(client, MM_20["id"], "2026-06-01T00:00:00Z")
        self.assertEqual([r["id"] for r in rows], ["3"])
        self.assertNotIn("data", rows[0])
        self.assertTrue(all(call[1].get("includeData") == "false" for call in client.calls if call[0] == "/executions"))


class ExportTests(unittest.TestCase):
    def run_export(self, routes):
        client = FakeCloud(routes)
        out = io.StringIO()
        with TemporaryDirectory() as directory, redirect_stdout(out):
            code = export.operate_export(client, Path(directory), export.DEFAULT_SINCE)
            files = sorted(p.relative_to(directory).as_posix() for p in Path(directory).rglob("*.json"))
            manifest = json.loads((Path(directory) / "_manifest.json").read_text(encoding="utf-8"))
        return code, client, files, manifest, out.getvalue()

    def test_full_export_writes_the_expected_files(self) -> None:
        code, client, files, manifest, output = self.run_export(full_routes())
        self.assertEqual(code, driver.EXIT_OK)
        self.assertEqual(sum(f.startswith("data-tables/") for f in files), 8)
        self.assertEqual(sum(f.startswith("workflows/") for f in files), 3)
        self.assertEqual(sum(f.startswith("executions/") for f in files), 2)
        self.assertEqual(manifest["table_row_counts"], {name: 1 for name in export.EXPORT_TABLES})
        self.assertEqual(manifest["execution_counts"], {MM_20["id"]: 1, MM_21["id"]: 1})
        self.assertEqual(len(manifest["sha256"]), len(files) - 1)
        self.assertNotIn("test-value-not-a-real-key", output)

    def test_other_tables_are_never_read(self) -> None:
        extra = [{"id": "LEADS999", "name": "Leads"}]
        routes = full_routes(extra_tables=extra)
        routes["/data-tables/LEADS999/rows"] = page([{"email": "private@example.com"}])
        code, client, files, manifest, _ = self.run_export(routes)
        self.assertEqual(code, driver.EXIT_OK)
        self.assertFalse(any("LEADS999" in call[0] for call in client.calls))
        self.assertNotIn("data-tables/Leads.json", files)

    def test_missing_table_is_reported_not_hidden(self) -> None:
        code, _, files, manifest, output = self.run_export(full_routes(skip=("Raw_Media",)))
        self.assertEqual(code, driver.EXIT_FAILED)
        self.assertEqual(manifest["tables_missing"], ["Raw_Media"])
        self.assertIn("RESULT export partial", output)

    def test_duplicate_table_names_stop_the_run(self) -> None:
        routes = full_routes(extra_tables=[{"id": "DUP00001", "name": "Website_Content"}])
        with self.assertRaises(driver.Abort):
            export.operate_export(FakeCloud(routes), Path("unused"), export.DEFAULT_SINCE)

    def test_table_path_falls_back(self) -> None:
        routes = full_routes()
        routes["/datatables"] = routes.pop("/data-tables")
        for key in [k for k in routes if k.startswith("/data-tables/")]:
            routes["/datatables/" + key.split("/", 2)[2]] = routes.pop(key)
        base, mapping, missing = export.resolve_tables(FakeCloud(routes))
        self.assertEqual(base, "/datatables")
        self.assertEqual(missing, [])
        self.assertEqual(len(mapping), 8)

    def test_rejected_key_is_named_plainly(self) -> None:
        client = FakeCloud({"/workflows": (401, {"message": "Unauthorized"})})
        with self.assertRaises(driver.Abort) as raised:
            export.operate_preflight(client)
        self.assertIn("401", str(raised.exception))

    def test_preflight_writes_nothing(self) -> None:
        out = io.StringIO()
        with redirect_stdout(out), TemporaryDirectory() as directory:
            code = export.operate_preflight(FakeCloud(full_routes()))
            self.assertEqual(list(Path(directory).iterdir()), [])
        self.assertEqual(code, driver.EXIT_OK)
        self.assertIn("RESULT preflight ok", out.getvalue())


class MainTests(unittest.TestCase):
    def test_missing_settings_stop_before_any_request(self) -> None:
        out = io.StringIO()
        with mock.patch.dict("os.environ", {}, clear=True), redirect_stdout(out):
            self.assertEqual(export.main(["preflight"]), driver.EXIT_MISCONFIGURED)
        self.assertIn("N8N_CLOUD_BASE_URL is not set", out.getvalue())

    def test_self_hosted_key_names_are_not_read(self) -> None:
        source = Path(export.__file__).read_text(encoding="utf-8")
        self.assertNotIn("X_N8N_API_KEY", source)
        self.assertNotIn('"N8N_BASE_URL"', source)
        self.assertNotIn("N8N_API_CREDENTIAL", source.replace("N8N_CLOUD_API_CREDENTIAL", ""))


class WorkflowFileTests(unittest.TestCase):
    def setUp(self) -> None:
        self.text = WORKFLOW_FILE.read_text(encoding="utf-8")

    def test_only_manual_dispatch_triggers_it(self) -> None:
        self.assertIn("workflow_dispatch:", self.text)
        for trigger in ("schedule:", "push:", "pull_request:", "workflow_run:"):
            self.assertNotIn(trigger, self.text)

    def test_it_never_touches_the_self_hosted_key(self) -> None:
        self.assertNotIn("X_N8N_API_KEY", self.text)
        self.assertNotIn("vars.N8N_BASE_URL", self.text)

    def test_it_uses_its_own_environment_and_names(self) -> None:
        self.assertIn("environment: n8n-cloud-export", self.text)
        self.assertIn("secrets.N8N_CLOUD_API_KEY", self.text)
        self.assertIn("vars.N8N_CLOUD_BASE_URL", self.text)

    def test_artifact_is_short_lived_and_read_only_permissions(self) -> None:
        self.assertIn("contents: read", self.text)
        match = re.search(r"retention-days:\s*(\d+)", self.text)
        self.assertIsNotNone(match)
        self.assertLessEqual(int(match.group(1)), 7)


if __name__ == "__main__":
    unittest.main()
