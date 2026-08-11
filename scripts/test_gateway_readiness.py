#!/usr/bin/env python3
"""Tests for the gateway readiness probe.

The probe's job is to distinguish outcomes that all look like "it did not work":
not on the network, listening but not ready, ready. Those have different owners,
so the tests are mostly about which verdict is reported rather than whether the
run passed.
"""

from __future__ import annotations

import io
import unittest
from contextlib import redirect_stdout

try:
    from scripts import gateway_readiness as probe
except ImportError:  # pragma: no cover - direct execution from scripts/
    import gateway_readiness as probe  # type: ignore[no-redef]

# The probe puts scripts/ on sys.path and imports coolify_deploy as a top-level
# module. Reaching the driver through the probe rather than importing it here
# guarantees this is the same module object it uses; importing it separately
# would create a second one, and patching that would silently patch nothing.
driver = probe.driver

APPLICATION = {"uuid": "app-uuid-1", "name": "ai-gateway"}


def load_spec() -> dict:
    return driver.load_spec(driver.spec_path("ai-gateway"))


class FakeHttp:
    """Answers the URLs a real deployment would, and records what was asked."""

    def __init__(self, answers: dict[str, list[tuple[int | None, str]]]) -> None:
        # Each URL maps to a queue of answers so a retry can differ from the
        # first attempt, which is the whole point of retrying.
        self.answers = {url: list(queue) for url, queue in answers.items()}
        self.asked: list[str] = []

    def __call__(self, url: str, timeout: int = 0) -> probe.ProbeResult:
        self.asked.append(url)
        queue = self.answers.get(url)
        if not queue:
            return probe.ProbeResult(None, "", "gaierror: Name does not resolve")
        status, body = queue.pop(0) if len(queue) > 1 else queue[0]
        if status is None:
            return probe.ProbeResult(None, "", body)
        return probe.ProbeResult(status, body)


def run(operation, http: FakeHttp, spec=None) -> tuple[int, str]:
    """Run one operation with the transport faked and nothing else.

    Only the two module-level seams are replaced - the HTTP call and the API
    lookup - so everything the operation actually decides runs for real.
    """

    spec = spec or load_spec()
    real_fetch = probe.fetch
    real_locate = probe.locate_application
    probe.fetch = http
    probe.locate_application = lambda client, spec: APPLICATION
    try:
        buffer = io.StringIO()
        extra = {"sleep": lambda _seconds: None} if operation is probe.operate_probe else {}
        with redirect_stdout(buffer):
            code = operation(object(), spec, **extra)
        return code, buffer.getvalue()
    finally:
        probe.fetch = real_fetch
        probe.locate_application = real_locate


LIVE = "http://app-uuid-1:8081/health"
READY = "http://app-uuid-1:8081/ready"


class ProbeTests(unittest.TestCase):
    def test_a_ready_gateway_passes_and_says_the_database_was_proven(self) -> None:
        http = FakeHttp({LIVE: [(200, '{"status":"ok"}')], READY: [(200, '{"status":"ready"}')]})
        code, report = run(probe.operate_probe, http)
        self.assertEqual(code, driver.EXIT_OK)
        self.assertIn("RESULT probe ok reachable=yes ready=yes", report)
        self.assertIn("database", report)

    def test_liveness_alone_is_not_reported_as_success(self) -> None:
        """The distinction this whole script exists for.

        Coolify's health check is /health, so a gateway with no database reports
        running:healthy. Passing on liveness would make the probe agree with the
        instrument it was written to correct.
        """

        http = FakeHttp(
            {LIVE: [(200, '{"status":"ok"}')], READY: [(503, '{"status":"not_ready"}')]}
        )
        code, report = run(probe.operate_probe, http)
        self.assertEqual(code, driver.EXIT_FAILED)
        self.assertIn("reachable=yes ready=no", report)

    def test_unreachable_and_not_ready_are_different_verdicts(self) -> None:
        """One is a network placement problem, the other is the gateway's own."""

        unreachable = FakeHttp({})
        code, report = run(probe.operate_probe, unreachable)
        self.assertEqual(code, driver.EXIT_FAILED)
        self.assertIn("reachable=no", report)
        self.assertNotIn("ready=", report.split("RESULT")[-1].replace("reachable=no", ""))

    def test_a_pool_that_opens_late_is_not_reported_as_broken(self) -> None:
        """Seconds after a deploy this is normal, and failing on it is a false alarm."""

        http = FakeHttp(
            {
                LIVE: [(200, "")],
                READY: [(503, '{"status":"not_ready"}'), (200, '{"status":"ready"}')],
            }
        )
        code, report = run(probe.operate_probe, http)
        self.assertEqual(code, driver.EXIT_OK)
        self.assertIn("readiness attempt 2", report)

    def test_liveness_is_not_retried_because_a_listener_answers_or_is_not_there(self) -> None:
        http = FakeHttp({READY: [(200, "")]})
        run(probe.operate_probe, http)
        self.assertEqual(http.asked.count(LIVE), 1)

    def test_the_second_alias_is_tried_and_the_first_failure_is_still_reported(self) -> None:
        """A silent fallback would hide that the relied-upon alias does not answer."""

        http = FakeHttp(
            {"http://ai-gateway:8081/health": [(200, "")], "http://ai-gateway:8081/ready": [(200, "")]}
        )
        code, report = run(probe.operate_probe, http)
        self.assertEqual(code, driver.EXIT_OK)
        self.assertIn("liveness at app-uuid-1: no answer", report)
        self.assertIn("liveness confirmed at http://ai-gateway:8081", report)

    def test_no_credential_is_presented_and_no_model_endpoint_is_called(self) -> None:
        """The probe must not be able to spend money or authenticate."""

        http = FakeHttp({LIVE: [(200, "")], READY: [(200, "")]})
        run(probe.operate_probe, http)
        for url in http.asked:
            self.assertTrue(url.endswith("/health") or url.endswith("/ready"), url)

    def test_a_long_body_is_truncated_rather_than_pasted_into_the_log(self) -> None:
        http = FakeHttp({LIVE: [(200, "x" * 5000)], READY: [(200, "")]})
        _, report = run(probe.operate_probe, http)
        self.assertIn("...", report)
        self.assertNotIn("x" * 300, report)


class AddressTests(unittest.TestCase):
    def test_address_reports_every_candidate_without_failing(self) -> None:
        """A diagnostic that aborts is useless at the moment it is needed."""

        http = FakeHttp({"http://ai-gateway:8081/health": [(200, "")]})
        code, report = run(probe.operate_address, http)
        self.assertEqual(code, driver.EXIT_OK)
        self.assertIn("answering=1", report)
        self.assertIn("app-uuid-1: no answer", report)


class CandidateTests(unittest.TestCase):
    def test_the_uuid_leads_because_that_is_what_postgres_answered_to(self) -> None:
        names = probe.candidate_addresses(APPLICATION, load_spec())
        self.assertEqual(names[0], "app-uuid-1")
        self.assertIn("ai-gateway", names)

    def test_operator_aliases_are_included_but_do_not_take_precedence(self) -> None:
        record = dict(APPLICATION, custom_network_aliases="edge, extra")
        names = probe.candidate_addresses(record, load_spec())
        self.assertEqual(names[:2], ["app-uuid-1", "ai-gateway"])
        self.assertIn("edge", names)
        self.assertIn("extra", names)

    def test_a_missing_alias_field_is_not_an_error(self) -> None:
        for value in (None, "", [], "  "):
            with self.subTest(value=value):
                record = dict(APPLICATION, custom_network_aliases=value)
                self.assertIn("app-uuid-1", probe.candidate_addresses(record, load_spec()))


class TransportTests(unittest.TestCase):
    def test_a_refused_connection_and_an_unknown_name_stay_distinguishable(self) -> None:
        """Collapsing them into 'unreachable' is what makes an outage take an afternoon."""

        refused = probe.ProbeResult(None, "", "ConnectionRefusedError: refused")
        unknown = probe.ProbeResult(None, "", "gaierror: Name does not resolve")
        self.assertIn("refused", probe.summarize(refused))
        self.assertIn("resolve", probe.summarize(unknown))

    def test_a_non_2xx_answer_is_data_rather_than_an_exception(self) -> None:
        result = probe.ProbeResult(503, '{"status":"not_ready"}')
        self.assertTrue(result.answered)
        self.assertIn("503", probe.summarize(result))


class EntryPointTests(unittest.TestCase):
    def test_the_probe_refuses_to_run_without_a_base_address(self) -> None:
        with self.assertRaises(driver.Abort) as raised:
            probe.client_from_environment({"COOLIFY_API_CREDENTIAL": "x" * 20})
        self.assertIn("COOLIFY_BASE_URL", str(raised.exception))

    def test_a_plaintext_base_address_is_refused(self) -> None:
        with self.assertRaises(driver.Abort) as raised:
            probe.client_from_environment(
                {"COOLIFY_BASE_URL": "http://coolify.example", "COOLIFY_API_CREDENTIAL": "x" * 20}
            )
        self.assertIn("https", str(raised.exception))

    def test_a_missing_credential_is_refused(self) -> None:
        with self.assertRaises(driver.Abort) as raised:
            probe.client_from_environment({"COOLIFY_BASE_URL": "https://coolify.example"})
        self.assertIn("COOLIFY_API_CREDENTIAL", str(raised.exception))

    def test_both_operations_are_reachable_from_the_command_line(self) -> None:
        self.assertEqual(sorted(probe.OPERATIONS), ["address", "probe"])
        for name in probe.OPERATIONS:
            self.assertEqual(probe.parse_arguments([name]).operation, name)


if __name__ == "__main__":
    unittest.main()
