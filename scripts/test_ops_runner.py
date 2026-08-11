#!/usr/bin/env python3
"""Tests for the operations runner preflight.

The one assertion that matters here is that a minted registration credential
never reaches the report. Preflight has to mint one to prove the authority
exists, which means the value passes through this process, which means the only
thing standing between it and a public build log is this code.
"""

from __future__ import annotations

import io
import os
import sys
import unittest
from contextlib import redirect_stdout

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import ops_runner as runner  # noqa: E402

MINTED = "an-example-registration-value-standing-in-for-a-real-one"


class FakeGitHub:
    """A stand-in for the REST API that records what was asked of it."""

    def __init__(self, *, mint_status: int = 201, list_status: int = 200) -> None:
        self.mint_status = mint_status
        self.list_status = list_status
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
        return 404, None


def run_preflight(fake, **environment):
    values = {
        "GITHUB_REPOSITORY": "Ivan-Shyla/adapteng-company-os",
        "GITHUB_ADMIN_CREDENTIAL": "an-example-admin-value-standing-in-for-a-real-one",
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


class EntryPointTests(unittest.TestCase):
    def test_an_unknown_operation_is_refused(self) -> None:
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            code = runner.main(["deploy"])
        self.assertEqual(code, runner.EXIT_FAILED)
        self.assertIn("unknown operation", buffer.getvalue())


if __name__ == "__main__":
    unittest.main()
