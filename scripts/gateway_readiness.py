#!/usr/bin/env python3
"""Prove the deployed AI Gateway is reachable and that its database works.

Coolify reporting ``running:healthy`` is a weaker statement than it looks. The
health check it runs is the container's own, against ``GET /health``, and that
endpoint is deliberately dependency-free: it answers "is this process
listening?" and touches nothing. A gateway with no database, no credentials and
no route to anything answers it exactly the same way as a working one.

``GET /ready`` is the endpoint that means something. It verifies database
connectivity and configuration, and returns 503 when either is wrong. So the
deployment's real verification is a readiness call, and it has to come from
somewhere on the private network, because the gateway has no public address and
must not acquire one.

That somewhere is this runner. It is an application on the same Coolify
destination as the gateway, so it reaches the container by its network alias.
Running the probe here rather than from a hosted runner is what lets the service
stay unpublished.

Nothing here authenticates, and that is a property of the service rather than an
omission: ``/health`` and ``/ready`` are answered before the Authorization
header is read, and only ``POST /v1/gateway`` requires a credential. So this
probe cannot cause an inference call, cannot spend money, and needs no secret
beyond the Coolify credential it uses to find the container.

Operations
----------
``probe``   read-only. Reports liveness, then readiness, and fails if readiness
            is anything but 200. This is the deployment gate.
``address`` read-only. Reports which network aliases answer, without judging
            them. For when the probe cannot connect and the question is whether
            the name is wrong or the service is.
"""

from __future__ import annotations

import argparse
import os
import socket
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import coolify_deploy as driver  # noqa: E402

# How long to wait for one HTTP answer. Readiness opens a database connection,
# so it is legitimately slower than liveness; this is generous enough that a
# slow answer is not read as a dead one, and short enough that an unroutable
# address fails inside the job's budget rather than at its timeout.
REQUEST_TIMEOUT_SECONDS = 20

# A container that has just started may answer /health before its database pool
# is up. Retrying readiness distinguishes "not ready yet" from "not ready", which
# are different findings with different owners.
READINESS_ATTEMPTS = 6
READINESS_INTERVAL_SECONDS = 10

# A name already known to resolve from this runner, used only as a control. The
# managed database is the natural one: the runtime role job reaches it by uuid,
# so its resolvability here is established rather than assumed.
REFERENCE_HOST_VARIABLE = "READINESS_REFERENCE_HOST"


class ProbeResult:
    """One HTTP answer, or the reason there was not one."""

    def __init__(self, status: int | None, body: str, error: str = "") -> None:
        self.status = status
        self.body = body
        self.error = error

    @property
    def answered(self) -> bool:
        return self.status is not None


def fetch(url: str, timeout: int = REQUEST_TIMEOUT_SECONDS) -> ProbeResult:
    """GET one URL, treating every outcome as data rather than an exception.

    A non-2xx answer is a result, not a failure: 503 from ``/ready`` is the whole
    point of asking. Only the absence of an answer is an error, and it is
    reported as the exception's own text so a DNS failure and a refused
    connection stay distinguishable - they have different causes and different
    owners, and collapsing them into "unreachable" is what makes an outage take
    an afternoon.
    """

    request = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return ProbeResult(response.status, response.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as exc:
        body = ""
        try:
            body = exc.read().decode("utf-8", "replace")
        except Exception:  # noqa: BLE001 - a body is a bonus, not a requirement
            body = ""
        return ProbeResult(exc.code, body)
    except urllib.error.URLError as exc:
        return ProbeResult(None, "", f"{type(exc.reason).__name__}: {exc.reason}")
    except OSError as exc:
        return ProbeResult(None, "", f"{type(exc).__name__}: {exc}")


def summarize(result: ProbeResult) -> str:
    """Describe an answer in one line, without quoting an unbounded body."""

    if not result.answered:
        return f"no answer ({result.error})"
    body = result.body.strip().replace("\n", " ")
    if len(body) > 200:
        body = body[:200] + "..."
    return f"HTTP {result.status} {body}" if body else f"HTTP {result.status}"


def candidate_addresses(application: dict, spec: dict) -> list[str]:
    """Names this container might answer to, most specific first.

    Coolify aliases a container by its resource uuid, and that is the name the
    managed Postgres answered to, so it leads. The declared resource name is
    tried next because Coolify also aliases by name and the uuid is the thing
    most likely to change if the resource is ever recreated. Any explicitly
    configured aliases come last: they are the operator's addition and should not
    silently take precedence over the platform's own.
    """

    names: list[str] = []
    uuid = str(application.get("uuid") or "").strip()
    if uuid:
        names.append(uuid)
    declared = str(spec["target"]["resource_name"]).strip()
    if declared and declared not in names:
        names.append(declared)
    raw = application.get("custom_network_aliases")
    if isinstance(raw, str):
        extra = [item.strip() for item in raw.replace("\n", ",").split(",")]
    elif isinstance(raw, list):
        extra = [str(item).strip() for item in raw]
    else:
        extra = []
    for item in extra:
        if item and item not in names:
            names.append(item)
    return names


def locate_application_and_peers(
    client: driver.Client, spec: dict
) -> tuple[dict, list[dict]]:
    """Return the gateway and the other applications sharing its placement.

    The peers matter more than they look. Coolify places an application on the
    destination the environment already uses, so every application here reached
    the network through the same code path the gateway did. That makes a peer
    the only control that isolates the gateway. A managed database is placed by
    a different path and can sit on a different network, so its resolvability
    cannot license any claim about whether an application ought to resolve.
    """

    target = spec["target"]
    project = driver.find_project(client, target["project"])
    if project is None:
        raise driver.Abort(f"project {target['project']} was not found")
    environment = driver.find_environment(client, project["uuid"], target["environment"])
    if environment is None:
        raise driver.Abort(f"environment {target['environment']} was not found")
    applications = driver.applications_in(client, environment)
    application = driver.find_application(applications, target["resource_name"])
    if application is None:
        raise driver.Abort(
            f"application {target['resource_name']} does not exist; deploy it first"
        )
    uuid = application.get("uuid")
    peers = [item for item in applications if item.get("uuid") != uuid]
    return application, peers


def locate_application(client: driver.Client, spec: dict) -> dict:
    return locate_application_and_peers(client, spec)[0]


def running_peers(peers: list[dict]) -> list[dict]:
    """Keep only the peers that can serve as controls.

    Docker drops a stopped container's name from the network's DNS, so a peer
    that is not running would fail to resolve for a reason that has nothing to
    do with placement. Counting one as a control would manufacture the very
    conclusion this comparison exists to test.
    """

    return [
        item
        for item in peers
        if str(item.get("status") or "").startswith("running")
    ]


def attribute_placement(
    gateway_resolved: int, peers_total: int, peers_resolved: int
) -> tuple[str, str]:
    """Say which side an unresolvable gateway belongs to, or refuse to say.

    Three outcomes are genuinely different and were previously reported alike.
    A peer resolving puts the fault on the gateway. No peer resolving puts it on
    this runner's placement and leaves the gateway unknown. No peer at all
    leaves the question open, and saying so is worth more than a guess that
    reads like a finding.
    """

    if gateway_resolved:
        return (
            "gateway_resolves",
            "The gateway resolves from here, so placement is not the open question.",
        )
    if peers_total == 0:
        return (
            "undetermined",
            "No running peer application exists to compare against, so an "
            "unresolvable gateway cannot be attributed to its own attachment "
            "rather than to this runner's placement. The managed database is "
            "not a substitute: it is placed by a different code path and may "
            "sit on a different network.",
        )
    if peers_resolved:
        return (
            "gateway_detached",
            f"{peers_resolved} of {peers_total} running peer applications "
            "resolve from here. Peers reach the network through the same code "
            "path as the gateway, so the gateway alone being unresolvable is "
            "its own attachment and not this runner's placement.",
        )
    return (
        "runner_off_application_network",
        f"None of the {peers_total} running peer applications resolve from "
        "here either, so this runner is not on the network the applications "
        "are placed on. The gateway's attachment is unknown until that is "
        "fixed, and a database that does resolve shows only that this runner "
        "shares the database's network.",
    )


def resolve_base(client: driver.Client, spec: dict) -> tuple[str, list[tuple[str, ProbeResult]]]:
    """Find an address that answers liveness, and report everything tried.

    The attempts are returned even on success. When the first candidate fails and
    the second works, that difference is the finding - it means the alias this
    deployment relies on is not the one that answers - and discarding it would
    turn a configuration fact into a silent fallback.
    """

    application = locate_application(client, spec)
    port = int(spec["network"]["internal_port"])
    attempts: list[tuple[str, ProbeResult]] = []
    for name in candidate_addresses(application, spec):
        url = f"http://{name}:{port}/health"
        result = fetch(url)
        attempts.append((name, result))
        if result.status == 200:
            return f"http://{name}:{port}", attempts
    return "", attempts


def resolve(name: str) -> tuple[list[str], str]:
    """Resolve a name to addresses without connecting to anything.

    Kept separate from fetching because the two failures have different owners
    and only one of them is about the gateway. A name that does not resolve says
    the container is not on this network - nothing about whether the service
    works. A name that resolves but refuses the connection says the opposite.
    Resolution also works against things that speak no HTTP, which is what lets
    a database be used as the reference point below.
    """

    try:
        answers = socket.getaddrinfo(name, None, proto=socket.IPPROTO_TCP)
    except OSError as error:
        return [], f"{type(error).__name__}: {error}"
    addresses = []
    for entry in answers:
        address = str(entry[4][0])
        if address not in addresses:
            addresses.append(address)
    return addresses, ""


def operate_address(client: driver.Client, spec: dict) -> int:
    driver.emit("--- address ai-gateway")
    application, peers = locate_application_and_peers(client, spec)
    port = int(spec["network"]["internal_port"])
    driver.emit(f"    container port: {port}")
    answered = 0
    resolved = 0
    for name in candidate_addresses(application, spec):
        addresses, error = resolve(name)
        if addresses:
            resolved += 1
            driver.emit(f"    {name}: resolves to {', '.join(addresses)}")
            result = fetch(f"http://{name}:{port}/health")
            driver.emit(f"        liveness: {summarize(result)}")
            if result.status == 200:
                answered += 1
        else:
            driver.emit(f"    {name}: does not resolve ({error})")

    # Peers are the control. Each one was placed on the network by the same code
    # path as the gateway, so the comparison separates "the gateway is not
    # attached" from "this runner cannot see any application", which look
    # identical from the gateway's name alone and are fixed in different places.
    usable = running_peers(peers)
    peers_resolved = 0
    for peer in usable:
        name = str(peer.get("uuid") or "").strip()
        if not name:
            continue
        label = peer.get("name") or name
        addresses, error = resolve(name)
        if addresses:
            peers_resolved += 1
            driver.emit(f"    peer {label} ({name}): resolves to {', '.join(addresses)}")
        else:
            driver.emit(f"    peer {label} ({name}): does not resolve ({error})")
    if not usable:
        driver.emit(
            "    peers: no other running application in this environment to "
            "compare against"
        )

    verdict, sentence = attribute_placement(resolved, len(usable), peers_resolved)
    driver.emit(f"    PLACEMENT {verdict}: {sentence}")

    reference = (os.environ.get(REFERENCE_HOST_VARIABLE) or "").strip()
    if reference:
        addresses, error = resolve(reference)
        if addresses:
            driver.emit(
                f"    reference {reference}: resolves to {', '.join(addresses)}"
            )
            driver.emit(
                "        This runner shares the managed database's network, "
                "which is all it shows. A database is placed by a different "
                "code path from an application, so it cannot stand in for a "
                "peer when attributing an unresolvable gateway."
            )
        else:
            driver.emit(f"    reference {reference}: does not resolve ({error})")
            driver.emit(
                "        The reference is known to have been reachable from "
                "here, so this runner has lost even the database's network."
            )
    else:
        driver.emit(
            f"    reference: none given ({REFERENCE_HOST_VARIABLE} is empty)"
        )

    driver.emit("")
    driver.emit(
        f"RESULT address ok resolved={resolved} answering={answered} "
        f"peers={peers_resolved}/{len(usable)} placement={verdict}"
    )
    return driver.EXIT_OK


def operate_probe(client: driver.Client, spec: dict, sleep=None) -> int:
    import time

    sleep = sleep or time.sleep
    driver.emit("--- probe ai-gateway")
    base, attempts = resolve_base(client, spec)
    for name, result in attempts:
        driver.emit(f"    liveness at {name}: {summarize(result)}")
    if not base:
        driver.emit("")
        driver.emit(
            "    no alias answered. This is reachability, not readiness: either "
            "this runner is not on the gateway's network or the container is not "
            "listening. Both are visible from the Coolify placement."
        )
        reference = (os.environ.get(REFERENCE_HOST_VARIABLE) or "").strip()
        if reference:
            addresses, error = resolve(reference)
            if addresses:
                driver.emit(
                    f"    the reference name {reference} does resolve from here, "
                    "so this runner is on the shared network and the gateway is "
                    "not attached to it. That is the gateway's placement, not "
                    "this job's."
                )
            else:
                driver.emit(
                    f"    the reference name {reference} does not resolve either "
                    f"({error}), so this runner has lost the shared network and "
                    "the gateway's own state is unknown rather than bad."
                )
        driver.emit("RESULT probe failed reachable=no")
        return driver.EXIT_FAILED

    driver.emit(f"    liveness confirmed at {base}")

    # Readiness is retried and liveness is not, deliberately. A process that is
    # listening either answers /health now or is not the thing being probed;
    # whereas a pool that is still opening is a normal state seconds after a
    # deploy, and reporting it as a failure would make a healthy rollout look
    # broken depending on when the job happened to run.
    last = ProbeResult(None, "", "not attempted")
    for attempt in range(1, READINESS_ATTEMPTS + 1):
        last = fetch(f"{base}/ready")
        driver.emit(f"    readiness attempt {attempt}: {summarize(last)}")
        if last.status == 200:
            break
        if attempt < READINESS_ATTEMPTS:
            sleep(READINESS_INTERVAL_SECONDS)

    driver.emit("")
    if last.status != 200:
        driver.emit(
            "    The process is listening and the network reaches it, so this is "
            "the gateway's own verdict on its dependencies rather than a "
            "deployment problem. /ready returns 503 when the database is "
            "unreachable or the configuration is incomplete; the service logs the "
            "reason and deliberately does not put it in the response."
        )
        driver.emit(f"RESULT probe failed reachable=yes ready=no last={last.status}")
        return driver.EXIT_FAILED

    driver.emit(
        "    Readiness passed, which is the database proof: /ready opens a "
        "database connection and /health does not. No credential was presented "
        "and no model was called - both endpoints answer before the "
        "Authorization header is read."
    )
    driver.emit("RESULT probe ok reachable=yes ready=yes")
    return driver.EXIT_OK


OPERATIONS = {"probe": operate_probe, "address": operate_address}


def client_from_environment(environ=None) -> driver.Client:
    """Build the API client from the same two variables the deploy driver uses.

    The driver reads these inside its own entry point, which also loads a spec
    and dispatches an operation, so it cannot be called from here without
    performing a deployment. The two checks are repeated rather than the entry
    point reshaped: this probe must not be able to write, and the surest way to
    guarantee that is for it never to enter the code that can.
    """

    environ = os.environ if environ is None else environ
    base_url = (environ.get(driver.BASE_URL_VARIABLE) or "").strip().rstrip("/")
    if not base_url:
        raise driver.Abort(
            f"{driver.BASE_URL_VARIABLE} is empty; the gateway cannot be located",
            driver.EXIT_MISCONFIGURED,
        )
    if not base_url.startswith("https://"):
        raise driver.Abort(
            f"{driver.BASE_URL_VARIABLE} must be an https address so the access "
            "value is not sent in the clear",
            driver.EXIT_MISCONFIGURED,
        )
    credential = (environ.get(driver.CREDENTIAL_VARIABLE) or "").strip()
    if not credential:
        raise driver.Abort(
            f"{driver.CREDENTIAL_VARIABLE} is empty; nothing can be read",
            driver.EXIT_MISCONFIGURED,
        )
    driver.register_redaction(credential)
    return driver.Client(base_url, credential)


def parse_arguments(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=sorted(OPERATIONS))
    parser.add_argument(
        "--service",
        default="ai-gateway",
        help="Spec file under deploy/, named without its extension",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    arguments = parse_arguments(sys.argv[1:] if argv is None else argv)
    try:
        spec = driver.load_spec(driver.spec_path(arguments.service))
        client = client_from_environment()
        return OPERATIONS[arguments.operation](client, spec)
    except driver.Abort as exc:
        driver.emit(f"ABORT {exc}")
        return exc.code


if __name__ == "__main__":
    raise SystemExit(main())
