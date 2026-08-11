#!/usr/bin/env python3
"""Tests for the gateway readiness probe.

The probe's job is to distinguish outcomes that all look like "it did not work":
not on the network, listening but not ready, ready. Those have different owners,
so the tests are mostly about which verdict is reported rather than whether the
run passed.
"""

from __future__ import annotations

import io
import os
import socket
import unittest
from contextlib import redirect_stdout
from pathlib import Path

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


def everything_resolves(_name: str) -> tuple[list[str], str]:
    return ["10.0.0.4"], ""


def run(operation, http: FakeHttp, spec=None, resolver=None, peers=None) -> tuple[int, str]:
    """Run one operation with the transport and the resolver faked, and nothing else.

    Both seams are faked by default rather than only on request. A test that
    falls through to the real resolver passes or fails on the machine's DNS,
    which is the flake shape this workstream has spent a week cataloguing, and
    it would be introduced here by omission rather than by decision.
    """

    spec = spec or load_spec()
    real_fetch = probe.fetch
    real_locate = probe.locate_application
    real_locate_peers = probe.locate_application_and_peers
    real_resolve = probe.resolve
    probe.fetch = http
    probe.locate_application = lambda client, spec: APPLICATION
    probe.locate_application_and_peers = lambda client, spec: (APPLICATION, list(peers or []))
    probe.resolve = resolver or everything_resolves
    try:
        buffer = io.StringIO()
        extra = {"sleep": lambda _seconds: None} if operation is probe.operate_probe else {}
        with redirect_stdout(buffer):
            code = operation(object(), spec, **extra)
        return code, buffer.getvalue()
    finally:
        probe.fetch = real_fetch
        probe.locate_application = real_locate
        probe.locate_application_and_peers = real_locate_peers
        probe.resolve = real_resolve


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


class ReferenceTests(unittest.TestCase):
    """The control that decides whether an unresolvable gateway is the gateway's fault.

    Without it, a runner that had lost the shared network produces exactly the
    same report as a gateway that was never attached to it, and those are fixed
    in different places by different people.
    """

    def setUp(self) -> None:
        self.original = os.environ.get(probe.REFERENCE_HOST_VARIABLE)
        self.addCleanup(self.restore)

    def restore(self) -> None:
        if self.original is None:
            os.environ.pop(probe.REFERENCE_HOST_VARIABLE, None)
        else:
            os.environ[probe.REFERENCE_HOST_VARIABLE] = self.original

    def set_reference(self, value: str) -> None:
        os.environ[probe.REFERENCE_HOST_VARIABLE] = value

    def test_a_resolvable_reference_blames_the_gateway_not_the_runner(self) -> None:
        self.set_reference("database-uuid")
        only_reference = lambda name: (["10.0.0.9"], "") if name == "database-uuid" else ([], "gaierror")
        _, report = run(probe.operate_probe, FakeHttp({}), resolver=only_reference)
        self.assertIn("this runner is on the shared network", report)
        self.assertIn("not attached", report)

    def test_an_unresolvable_reference_blames_the_runner_and_withholds_a_verdict(self) -> None:
        """'The gateway is broken' would be an assertion about something never reached."""

        self.set_reference("database-uuid")
        nothing = lambda _name: ([], "gaierror: Temporary failure in name resolution")
        _, report = run(probe.operate_probe, FakeHttp({}), resolver=nothing)
        self.assertIn("this runner has lost the shared network", report)
        self.assertIn("unknown rather than bad", report)

    def test_without_a_peer_no_side_is_blamed(self) -> None:
        """The database reference is not evidence about an application.

        Coolify places a managed database by a different code path from an
        application, so a resolvable database licenses no claim about whether an
        application ought to resolve. With no peer to compare against, the only
        honest verdict is that the question is open.
        """

        self.set_reference("database-uuid")
        only_reference = (
            lambda name: (["10.0.0.9"], "") if name == "database-uuid" else ([], "gaierror")
        )
        code, report = run(probe.operate_address, FakeHttp({}), resolver=only_reference)
        self.assertEqual(code, driver.EXIT_OK)
        self.assertIn("placement=undetermined", report)
        self.assertIn("not a substitute", report)

    def test_address_separates_resolving_from_answering(self) -> None:
        """A name that resolves but refuses is a different fault from one that does not resolve."""

        self.set_reference("")
        only_uuid = lambda name: (["10.0.0.4"], "") if name == "app-uuid-1" else ([], "gaierror")
        code, report = run(probe.operate_address, FakeHttp({}), resolver=only_uuid)
        self.assertEqual(code, driver.EXIT_OK)
        self.assertIn("app-uuid-1: resolves to 10.0.0.4", report)
        self.assertIn("ai-gateway: does not resolve", report)
        self.assertIn("resolved=1 answering=0", report)

    def test_a_reference_is_never_fetched_only_resolved(self) -> None:
        """It is a database. Asking it for HTTP would fail for reasons that mean nothing."""

        self.set_reference("database-uuid")
        http = FakeHttp({})
        run(probe.operate_address, http)
        for url in http.asked:
            self.assertNotIn("database-uuid", url)


class ResolutionTests(unittest.TestCase):
    """Resolution is faked rather than performed.

    A test that does a real lookup passes or fails on the resolver's mood, and a
    captive DNS that answers everything would silently turn the negative case
    into a false pass. Both are the flake shape this workstream has spent a week
    cataloguing, so neither is introduced here.
    """

    def setUp(self) -> None:
        self.real = probe.socket.getaddrinfo
        self.addCleanup(setattr, probe.socket, "getaddrinfo", self.real)

    def test_resolution_reports_addresses_without_connecting(self) -> None:
        probe.socket.getaddrinfo = lambda *args, **kwargs: [
            (2, 1, 6, "", ("10.0.0.4", 0)),
            (2, 1, 6, "", ("10.0.0.4", 0)),
            (2, 1, 6, "", ("10.0.0.5", 0)),
        ]
        addresses, error = probe.resolve("anything")
        self.assertEqual(error, "")
        self.assertEqual(addresses, ["10.0.0.4", "10.0.0.5"])

    def test_an_unknown_name_is_returned_as_a_reason_rather_than_raised(self) -> None:
        def refuse(*_args, **_kwargs):
            raise socket.gaierror(-3, "Temporary failure in name resolution")

        probe.socket.getaddrinfo = refuse
        addresses, error = probe.resolve("anything")
        self.assertEqual(addresses, [])
        self.assertIn("gaierror", error)
        self.assertIn("name resolution", error)


class AddressTests(unittest.TestCase):
    def test_address_reports_every_candidate_without_failing(self) -> None:
        """A diagnostic that aborts is useless at the moment it is needed."""

        http = FakeHttp({"http://ai-gateway:8081/health": [(200, "")]})
        code, report = run(probe.operate_address, http)
        self.assertEqual(code, driver.EXIT_OK)
        self.assertIn("answering=1", report)
        self.assertIn("app-uuid-1", report)


class PeerControlTests(unittest.TestCase):
    """The control that separates a detached gateway from a misplaced runner.

    Every application in the environment reached the network through the same
    Coolify code path, so a peer is the only reference that holds the placement
    variable fixed. Before these, a runner that could see the database but no
    application at all produced the same report as a gateway that was never
    attached, and the two are fixed in different places by different people.
    """

    RUNNING = [
        {"uuid": "peer-uuid-1", "name": "n8n-selfhosted", "status": "running"},
        {"uuid": "peer-uuid-2", "name": "baserow-adapter", "status": "running:healthy"},
    ]

    def test_a_resolving_peer_puts_the_fault_on_the_gateway(self) -> None:
        only_peers = (
            lambda name: (["10.0.1.9"], "") if name.startswith("peer-") else ([], "gaierror")
        )
        code, report = run(
            probe.operate_address, FakeHttp({}), resolver=only_peers, peers=self.RUNNING
        )
        self.assertEqual(code, driver.EXIT_OK)
        self.assertIn("placement=gateway_detached", report)
        self.assertIn("peers=2/2", report)
        self.assertIn("n8n-selfhosted", report)

    def test_no_peer_resolving_puts_the_fault_on_the_runner(self) -> None:
        """The finding this class was added to make reachable at all."""

        only_database = (
            lambda name: (["10.0.1.7"], "") if name == "database-uuid" else ([], "gaierror")
        )
        original = os.environ.get(probe.REFERENCE_HOST_VARIABLE)
        os.environ[probe.REFERENCE_HOST_VARIABLE] = "database-uuid"
        try:
            code, report = run(
                probe.operate_address,
                FakeHttp({}),
                resolver=only_database,
                peers=self.RUNNING,
            )
        finally:
            if original is None:
                os.environ.pop(probe.REFERENCE_HOST_VARIABLE, None)
            else:
                os.environ[probe.REFERENCE_HOST_VARIABLE] = original
        self.assertEqual(code, driver.EXIT_OK)
        self.assertIn("placement=runner_off_application_network", report)
        self.assertIn("peers=0/2", report)
        self.assertIn("attachment is unknown", report)

    def test_a_stopped_peer_is_not_counted_as_a_control(self) -> None:
        """Docker drops a stopped container from DNS, so it proves nothing.

        Counting one would turn a peer that is legitimately absent from the
        network into evidence that this runner is off the application network,
        which is the exact false verdict the control exists to prevent.
        """

        stopped = [{"uuid": "peer-uuid-1", "name": "n8n", "status": "exited:unhealthy"}]
        nothing = lambda _name: ([], "gaierror")
        code, report = run(
            probe.operate_address, FakeHttp({}), resolver=nothing, peers=stopped
        )
        self.assertEqual(code, driver.EXIT_OK)
        self.assertIn("placement=undetermined", report)
        self.assertIn("peers=0/0", report)
        self.assertNotIn("peer n8n", report)

    def test_a_resolving_gateway_ends_the_placement_question(self) -> None:
        code, report = run(probe.operate_address, FakeHttp({}), peers=self.RUNNING)
        self.assertEqual(code, driver.EXIT_OK)
        self.assertIn("placement=gateway_resolves", report)


class AttributionTests(unittest.TestCase):
    def test_every_branch_names_a_different_owner(self) -> None:
        self.assertEqual(probe.attribute_placement(1, 2, 0)[0], "gateway_resolves")
        self.assertEqual(probe.attribute_placement(0, 0, 0)[0], "undetermined")
        self.assertEqual(probe.attribute_placement(0, 2, 1)[0], "gateway_detached")
        self.assertEqual(
            probe.attribute_placement(0, 2, 0)[0], "runner_off_application_network"
        )

    def test_a_resolving_gateway_wins_over_an_absent_peer_set(self) -> None:
        """Order matters: with no peers the verdict would otherwise be undetermined."""

        self.assertEqual(probe.attribute_placement(2, 0, 0)[0], "gateway_resolves")

    def test_only_running_peers_survive_the_filter(self) -> None:
        peers = [
            {"uuid": "a", "status": "running"},
            {"uuid": "b", "status": "running:healthy"},
            {"uuid": "c", "status": "exited:unhealthy"},
            {"uuid": "d", "status": "restarting"},
            {"uuid": "e"},
        ]
        self.assertEqual(
            [item["uuid"] for item in probe.running_peers(peers)], ["a", "b"]
        )


class PeerDerivationTests(unittest.TestCase):
    """Exercise the peer list itself, not a stand-in for it.

    Every other test here patches ``locate_application_and_peers`` wholesale, so
    the derivation was invisible to the suite: a mutant that let the gateway
    count itself as a peer survived. That mutant is not cosmetic. With no real
    peer present it turns the honest ``undetermined`` into a confident
    ``runner_off_application_network``, which points the fix at the wrong host.
    """

    def patch(self, applications: list[dict]) -> None:
        real = (driver.find_project, driver.find_environment, driver.applications_in)

        def restore() -> None:
            driver.find_project, driver.find_environment, driver.applications_in = real

        self.addCleanup(restore)
        driver.find_project = lambda client, name: {"uuid": "project-uuid", "name": name}
        driver.find_environment = lambda client, uuid, name: {"uuid": "env-uuid", "name": name}
        driver.applications_in = lambda client, environment: applications

    def test_the_gateway_is_not_counted_as_its_own_peer(self) -> None:
        self.patch(
            [
                {"uuid": "app-uuid-1", "name": "ai-gateway", "status": "running"},
                {"uuid": "peer-uuid-1", "name": "n8n-selfhosted", "status": "running"},
            ]
        )
        application, peers = probe.locate_application_and_peers(object(), load_spec())
        self.assertEqual(application["uuid"], "app-uuid-1")
        self.assertEqual([item["uuid"] for item in peers], ["peer-uuid-1"])

    def test_a_lone_gateway_yields_no_peers_rather_than_itself(self) -> None:
        self.patch([{"uuid": "app-uuid-1", "name": "ai-gateway", "status": "running"}])
        _, peers = probe.locate_application_and_peers(object(), load_spec())
        self.assertEqual(peers, [])

    def test_an_absent_application_aborts_instead_of_reporting_peers(self) -> None:
        self.patch([{"uuid": "peer-uuid-1", "name": "n8n-selfhosted", "status": "running"}])
        with self.assertRaises(driver.Abort):
            probe.locate_application_and_peers(object(), load_spec())


class PositionTests(unittest.TestCase):
    """Where the process is, because the verdict now turns on it.

    A report that can say "this runner is not on the applications' network" and
    cannot say where it is instead sends someone to the host to find out by
    hand, which is the manual step this workstream exists to remove.
    """

    PEERS = [{"uuid": "o145rw7urft00qoj3y9vnrma", "name": "ops-runner", "status": "running"}]

    def test_the_embedded_resolver_says_the_network_is_the_question(self) -> None:
        lines = probe.runner_position([], "abc123", ["127.0.0.11"], True)
        joined = "\n".join(lines)
        self.assertIn("embedded DNS", joined)
        self.assertIn("ought to resolve here", joined)

    def test_a_host_resolver_says_no_container_name_can_resolve(self) -> None:
        lines = probe.runner_position([], "build-01", ["8.8.8.8", "1.1.1.1"], False)
        joined = "\n".join(lines)
        self.assertIn("not Docker's embedded DNS", joined)
        self.assertIn("no application will be visible", joined)
        self.assertIn("not a Docker container", joined)

    def test_an_unreadable_resolver_file_withholds_the_claim(self) -> None:
        lines = probe.runner_position([], "abc123", [], True)
        self.assertIn("cannot be attributed", "\n".join(lines))

    def test_the_process_is_identified_when_it_is_itself_a_peer(self) -> None:
        lines = probe.runner_position(
            self.PEERS, "o145rw7urft00qoj3y9vnrma-063523", ["127.0.0.11"], True
        )
        self.assertIn("inside the Coolify resource ops-runner", "\n".join(lines))

    def test_an_unrelated_hostname_claims_no_identity(self) -> None:
        lines = probe.runner_position(self.PEERS, "fv-az1234-5", ["127.0.0.11"], True)
        self.assertNotIn("inside the Coolify resource", "\n".join(lines))

    def test_nameservers_are_read_from_the_file_and_nothing_else(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "resolv.conf"
            path.write_text(
                "# comment\nsearch example\nnameserver 127.0.0.11\nnameserver 10.0.0.1\noptions ndots:0\n",
                encoding="utf-8",
            )
            self.assertEqual(
                probe.read_nameservers(path), ["127.0.0.11", "10.0.0.1"]
            )

    def test_a_missing_resolver_file_is_not_an_error(self) -> None:
        self.assertEqual(probe.read_nameservers(Path("nowhere-at-all.conf")), [])


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
