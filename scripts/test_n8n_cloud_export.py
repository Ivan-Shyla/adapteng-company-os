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


class EvidenceTests(unittest.TestCase):
    SENTINELS = ("SECRET-TITLE", "SECRET-ABSTRACT", "SECRET-NOTE", "SECRET-PARAM", "SECRET-FOLDER")

    def routes(self):
        routes = full_routes()
        routes["/workflows/gJMEoOMUI0uS0D1Y"] = (
            200,
            {
                "id": "gJMEoOMUI0uS0D1Y",
                "name": "MM-20 Publication Scout",
                "active": True,
                "nodes": [
                    {"name": "Weekly Monday 07:00", "type": "n8n-nodes-base.scheduleTrigger", "parameters": {"rule": {"interval": [{"field": "weeks", "weeksInterval": 1, "triggerAtDay": [1], "triggerAtHour": 7, "triggerAtMinute": 0, "note": "SECRET-PARAM"}]}}},
                    {"name": "Upsert Publication_Radar", "type": "n8n-nodes-base.dataTable", "parameters": {"operation": "upsert", "dataTableId": {"mode": "name", "value": "Publication_Radar"}, "columns": {"value": {"status": "={{ $json.status }}", "drive_file_id": "={{ $json.drive_file_id }}", "title": "SECRET-PARAM"}}}},
                    {"name": "Call MM-21", "type": "n8n-nodes-base.executeWorkflow", "parameters": {"workflowId": {"value": "kB21JwdMDZt7nT5a"}}},
                    {"name": "Make file", "type": "n8n-nodes-base.googleDrive", "parameters": {"operation": "createFromText", "folderId": {"value": "root"}, "content": "SECRET-FOLDER"}},
                    {"name": "Code step", "type": "n8n-nodes-base.code", "parameters": {"jsCode": "SECRET-PARAM"}},
                ],
                "connections": {"Weekly Monday 07:00": {"main": [[{"node": "Upsert Publication_Radar"}]]}},
            },
        )
        radar = [
            {"pub_id": "CROSSREF_10_1_x", "status": "approved_for_analysis", "drive_file_id": "", "analysis_id": "", "dedupe_key": "k1", "discovered_at": "2026-10-19T02:00:00Z", "title": "SECRET-TITLE", "abstract": "SECRET-ABSTRACT", "notes": "SECRET-NOTE"},
            {"pub_id": "CROSSREF_10_2_y", "status": "keep_in_radar_only", "drive_file_id": "f1", "analysis_id": "a1", "dedupe_key": "k2", "discovered_at": "2026-10-12T02:00:00Z"},
        ]
        radar_id = next(t["id"] for t in routes["/data-tables"][1]["data"] if t["name"] == "Publication_Radar")
        routes[f"/data-tables/{radar_id}/rows"] = page(radar)
        return routes

    def run_evidence(self):
        out = io.StringIO()
        with redirect_stdout(out):
            code = export.operate_evidence(FakeCloud(self.routes()), export.DEFAULT_SINCE)
        return code, out.getvalue()

    def test_output_never_contains_content(self) -> None:
        code, output = self.run_evidence()
        self.assertEqual(code, driver.EXIT_OK)
        for sentinel in self.SENTINELS:
            self.assertNotIn(sentinel, output)

    def test_it_reports_the_facts_the_migration_needs(self) -> None:
        _, output = self.run_evidence()
        self.assertIn('"triggerAtDay": [1]', output)
        self.assertIn("calls=kB21JwdMDZt7nT5a", output)
        self.assertIn("drive_file_id(from item)", output)
        self.assertIn("folder_is_root=True", output)
        self.assertIn("edge: Weekly Monday 07:00 -> Upsert Publication_Radar", output)
        self.assertIn("rows with empty drive_file_id and analysis_id in an approved status: 1", output)
        self.assertIn("Mon 02:00Z", output)

    def test_only_radar_rows_are_read(self) -> None:
        client = FakeCloud(self.routes())
        with redirect_stdout(io.StringIO()):
            export.operate_evidence(client, export.DEFAULT_SINCE)
        row_reads = [path for path, _ in client.calls if path.endswith("/rows")]
        self.assertEqual(len(row_reads), 1)

    def test_it_writes_no_files(self) -> None:
        with TemporaryDirectory() as directory:
            before = list(Path(directory).iterdir())
            self.run_evidence()
            self.assertEqual(before, list(Path(directory).iterdir()))

    def test_unprintable_values_are_withheld(self) -> None:
        self.assertEqual(export.safe_text("line one\nline two"), "[not printed]")
        self.assertEqual(export.safe_text("a b c d"), "[not printed]")
        self.assertEqual(export.safe_text("CROSSREF_10_1_x"), "CROSSREF_10_1_x")


class ExportGateTests(unittest.TestCase):
    def test_export_is_refused_without_a_private_destination(self) -> None:
        env = {"N8N_CLOUD_BASE_URL": CLOUD_URL, "N8N_CLOUD_API_CREDENTIAL": "k" * 20}
        out = io.StringIO()
        with mock.patch.dict("os.environ", env, clear=True), redirect_stdout(out):
            code = export.main(["export"])
        self.assertEqual(code, driver.EXIT_MISCONFIGURED)
        self.assertIn("EXPORT_DESTINATION_PRIVATE", out.getvalue())

    def test_export_is_refused_when_the_flag_is_not_exactly_true(self) -> None:
        env = {"N8N_CLOUD_BASE_URL": CLOUD_URL, "N8N_CLOUD_API_CREDENTIAL": "k" * 20, "EXPORT_DESTINATION_PRIVATE": "yes"}
        with mock.patch.dict("os.environ", env, clear=True), redirect_stdout(io.StringIO()):
            self.assertEqual(export.main(["export"]), driver.EXIT_MISCONFIGURED)


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

    def test_only_ciphertext_is_uploaded(self) -> None:
        self.assertEqual(self.text.count("actions/upload-artifact@"), 1)
        upload = self.text[self.text.index("actions/upload-artifact@"):]
        self.assertIn("path: ${{ runner.temp }}/enc", upload)
        self.assertIn("encrypted", upload)
        self.assertNotIn("/plain", upload)
        self.assertNotIn("plain.tgz", upload)
        self.assertLessEqual(int(re.search(r"retention-days:\s*(\d+)", upload).group(1)), 3)

    def test_plaintext_is_encrypted_with_the_public_key_and_removed_before_upload(self) -> None:
        encrypt = self.text.index("Encrypt the snapshot")
        upload = self.text.index("Upload ciphertext")
        self.assertLess(encrypt, upload)
        step = self.text[encrypt:upload]
        self.assertIn("-pubin", step)
        self.assertIn("rsa_padding_mode:oaep", step)
        self.assertIn("-pbkdf2", step)
        self.assertIn("registry/n8n-export-transport.pub.pem", step)
        self.assertIn("rm -rf plain plain.tgz session.key", step)
        self.assertNotIn("N8N_CLOUD_API", step)

    def test_both_steps_run_only_for_the_encrypted_export(self) -> None:
        for name in ("Encrypt the snapshot", "Upload ciphertext"):
            start = self.text.index(name)
            self.assertIn("if: inputs.operation == 'export-encrypted'", self.text[start:start + 200])

    def test_private_destination_flag_is_set_only_for_the_encrypted_export(self) -> None:
        self.assertIn(
            "EXPORT_DESTINATION_PRIVATE: ${{ inputs.operation == 'export-encrypted' && 'true' || 'false' }}",
            self.text,
        )

    def test_only_expected_operations_are_offered(self) -> None:
        options = re.search(r"options:\n((?:\s+- [\w-]+\n)+)", self.text)
        self.assertIsNotNone(options)
        self.assertEqual(
            re.findall(r"- ([\w-]+)", options.group(1)), ["preflight", "evidence", "export-encrypted"]
        )

    def test_transport_key_file_is_a_public_key_only(self) -> None:
        key = (REPOSITORY_ROOT / "registry" / "n8n-export-transport.pub.pem").read_text(encoding="utf-8")
        self.assertTrue(key.startswith("-----BEGIN PUBLIC KEY-----"))
        self.assertNotIn("PRIVATE", key)

    def test_read_only_permissions(self) -> None:
        self.assertIn("contents: read", self.text)
        self.assertNotIn("contents: write", self.text)


if __name__ == "__main__":
    unittest.main()
