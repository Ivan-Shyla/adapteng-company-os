"""Tests for the drive-adapter credential binder.

The property that matters most: GOOGLE_SERVICE_ACCOUNT_JSON_B64 must be
exactly the base64 encoding of the material given, and neither it nor the
plaintext material may ever appear in the run report.
"""

from __future__ import annotations

import base64
import io
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts import drive_adapter_credentials as binder  # noqa: E402
from scripts.test_ai_gateway_credentials import FakeCoolify  # noqa: E402

driver = binder.driver

SERVICE_ACCOUNT = (
    '{"type": "service_account", "project_id": "adapteng-workspace-automation", '
    '"client_email": "workspace-automation@adapteng-workspace-automation.iam.gserviceaccount.com", '
    '"private_key": "-----BEGIN PRIVATE KEY-----\\nnot-a-real-key\\n-----END PRIVATE KEY-----\\n", '
    '"token_uri": "https://oauth2.googleapis.com/token"}'
)


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


class BindGoogleCredentialTests(unittest.TestCase):
    def written_value(self, coolify: FakeCoolify) -> str:
        return [
            body["value"]
            for method, path, body in coolify.writes
            if path.endswith("/envs") and isinstance(body, dict)
        ][0]

    def test_the_written_value_is_exactly_the_base64_encoding(self) -> None:
        coolify = FakeCoolify()
        run_operation(
            binder.operate_bind_google_credential, coolify, material=SERVICE_ACCOUNT
        )
        written = self.written_value(coolify)
        self.assertEqual(
            base64.b64decode(written).decode("utf-8"), SERVICE_ACCOUNT
        )

    def test_the_written_key_is_the_b64_contract_not_the_plain_one(self) -> None:
        coolify = FakeCoolify()
        run_operation(
            binder.operate_bind_google_credential, coolify, material=SERVICE_ACCOUNT
        )
        keys = [body["key"] for _m, path, body in coolify.writes if path.endswith("/envs")]
        self.assertEqual(keys, [binder.ENCODED_KEY])

    def test_neither_the_material_nor_the_encoded_value_reaches_the_report(self) -> None:
        coolify = FakeCoolify()
        _, report = run_operation(
            binder.operate_bind_google_credential, coolify, material=SERVICE_ACCOUNT
        )
        self.assertNotIn(SERVICE_ACCOUNT, report)
        self.assertNotIn(self.written_value(coolify), report)
        self.assertIn("present length=", report)

    def test_material_that_is_not_a_service_account_is_refused_before_any_write(self) -> None:
        for bad in ('{"type": "authorized_user"}', "not json at all", "[]"):
            coolify = FakeCoolify()
            with self.assertRaises(driver.Abort):
                run_operation(binder.operate_bind_google_credential, coolify, material=bad)
            self.assertEqual(coolify.writes, [])

    def test_a_value_the_instance_does_not_report_is_a_failure_not_a_success(self) -> None:
        class Forgetful(FakeCoolify):
            def __call__(self, client, method, path, body=None, expect=(200,), allow_absent=False):
                result = super().__call__(client, method, path, body, expect, allow_absent)
                if method.upper() == "GET" and path.endswith("/envs"):
                    return [e for e in result if e.get("key") != binder.ENCODED_KEY]
                return result

        with self.assertRaises(driver.Abort):
            run_operation(
                binder.operate_bind_google_credential, Forgetful(), material=SERVICE_ACCOUNT
            )


class StatusTests(unittest.TestCase):
    def test_an_empty_instance_reports_every_key_absent(self) -> None:
        code, report = run_operation(binder.operate_status, FakeCoolify())
        self.assertEqual(code, 0)
        self.assertIn(f"env {binder.ENCODED_KEY}: ABSENT", report)
        self.assertIn("credentials=incomplete", report)

    def test_all_three_required_keys_present_reports_bound(self) -> None:
        coolify = FakeCoolify(
            environment=[
                {"key": binder.ENCODED_KEY, "value": "x"},
                {"key": "DRIVE_SERVICE_BEARER_TOKENS", "value": "y"},
                {"key": "DRIVE_BRIDGE_REPLAY_DATABASE_URL", "value": "z"},
            ]
        )
        code, report = run_operation(binder.operate_status, coolify)
        self.assertEqual(code, 0)
        self.assertIn("credentials=bound", report)

    def test_status_writes_nothing(self) -> None:
        coolify = FakeCoolify()
        run_operation(binder.operate_status, coolify)
        self.assertEqual(coolify.writes, [])

    def test_a_stored_value_is_never_printed(self) -> None:
        coolify = FakeCoolify(
            environment=[{"key": binder.ENCODED_KEY, "value": "a-value-that-must-not-appear"}]
        )
        _, report = run_operation(binder.operate_status, coolify)
        self.assertNotIn("a-value-that-must-not-appear", report)


class WorkflowTests(unittest.TestCase):
    def workflow_text(self) -> str:
        return Path(__file__).resolve().parent.parent.joinpath(
            ".github/workflows/drive-adapter-credentials.yml"
        ).read_text(encoding="utf-8")

    def test_the_workflow_offers_exactly_the_implemented_operations(self) -> None:
        text = self.workflow_text()
        for operation in ("status", "bind-google-credential"):
            self.assertIn(f"- {operation}", text)

    def test_the_workflow_passes_the_existing_material_by_reference(self) -> None:
        text = self.workflow_text()
        self.assertIn(
            "GOOGLE_SERVICE_ACCOUNT_MATERIAL: ${{ secrets.GOOGLE_SERVICE_ACCOUNT_JSON }}",
            text,
        )


if __name__ == "__main__":
    unittest.main()
