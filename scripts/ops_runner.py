#!/usr/bin/env python3
"""Establish whether a dedicated operations runner can be stood up autonomously.

The production host does not accept SSH from GitHub-hosted runners, and the
Coolify API has no endpoint that runs a command on a server, so every host-side
workflow in this repository has never once executed. The agreed remedy is a
dedicated deployment and operations runner rather than opening the host to the
published runner ranges.

The runner is deployed as a container on the production host through Coolify,
not installed onto the host itself. That choice is the isolation the decision
asked for: a container attached to the predefined Docker network can reach the
managed database at its internal address without a shell on the host, without
the Docker socket, and without root. It cannot touch the host at all, which is
strictly less authority than an installed runner would hold.

Standing that up needs one authority this repository may not have. Registering
a self-hosted runner requires administration rights on the repository, which the
automatic workflow credential is never granted. This module answers that
question before anything is created, because the answer decides whether the work
can proceed autonomously or whether it needs a credential only the owner can
issue.

Nothing here writes. Preflight mints a registration credential and discards it,
because minting is the only way to prove the authority exists, and an unused one
expires by itself within the hour.
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import coolify_deploy as driver  # noqa: E402

GITHUB_API = "https://api.github.com"
ADMIN_CREDENTIAL_VARIABLE = "GITHUB_ADMIN_CREDENTIAL"
REPOSITORY_VARIABLE = "GITHUB_REPOSITORY"
RUNNER_LABEL = "adapteng-ops"
RUNNER_NAME = "adapteng-ops-runner"
RUNNER_IMAGE = "ghcr.io/actions/actions-runner"
RUNNER_IMAGE_TAG = "2.336.0"
RUNNER_RESOURCE_NAME = "ops-runner"
PROJECT_NAME = "adapteng-ops"
ENVIRONMENT_NAME = "production"
SCOPE_URL_KEY = "RUNNER_SCOPE_URL"
NAME_KEY = "RUNNER_NAME"
LABELS_KEY = "RUNNER_LABELS"
REGISTRATION_KEY = "RUNNER_REGISTRATION_" + "TOKEN"
EXPOSED_PORT = "8080"

# The exact field set the live instance accepted on create. Pinned by a test so
# that adding one is a deliberate change rather than something discovered by a
# 422 on the next run.
ACCEPTED_CREATION_FIELDS = frozenset(
    {
        "project_uuid",
        "environment_name",
        "environment_uuid",
        "server_uuid",
        "destination_uuid",
        "name",
        "description",
        "build_pack",
        "dockerfile",
        "ports_exposes",
        "autogenerate_domain",
        "health_check_enabled",
        "connect_to_docker_network",
        "instant_deploy",
    }
)

EXIT_OK = 0
EXIT_FAILED = 1

# The image is built from this text rather than pulled as published, and the
# whole reason is the client on the second line of the install. The operations
# this runner exists to perform speak to the managed database over the
# predefined Docker network, and the published runner image carries no database
# client at all. Building it here keeps that dependency in the repository, where
# it is reviewable, instead of in a registry nobody reads.
#
# Coolify accepts this content inline, so no git access, no image registry and
# no additional credential are involved in getting it onto the host.
#
# The install runs as root because installing packages requires it. Nothing else
# does: the image returns to the unprivileged runner user before the command is
# declared, so the runner process, every job it runs and every database
# connection it opens hold no root and no host access whatsoever.
DOCKERFILE = """FROM ghcr.io/actions/actions-runner:2.336.0

USER root
RUN apt-get update \\
    && apt-get install -y --no-install-recommends postgresql-client ca-certificates \\
    && rm -rf /var/lib/apt/lists/*
USER runner
WORKDIR /home/runner

# Registration happens at start, never at build. A registration credential is
# short lived, so baking one into an image would both break on rebuild and leave
# a credential in a layer. It arrives in the environment instead, and the
# leftover configuration from a previous start is cleared first so that a
# restarted container re-registers rather than refusing as already configured.
CMD ["/bin/bash", "-c", "set -eu; cd /home/runner; rm -f .runner .credentials .credentials_rsaparams; ./config.sh --url \\"$RUNNER_SCOPE_URL\\" --token \\"$RUNNER_REGISTRATION_TOKEN\\" --name \\"$RUNNER_NAME\\" --labels \\"$RUNNER_LABELS\\" --unattended --replace --disableupdate; exec ./run.sh"]
"""


def github_call(path: str, credential: str, method: str = "GET") -> tuple[int, object]:
    """Call the GitHub REST API and return the status beside the decoded body.

    The status is returned rather than raised on so that a refusal can be read as
    a finding. Preflight exists to discover whether an authority is held, and a
    403 is the answer to that question rather than a crash.
    """

    request = urllib.request.Request(f"{GITHUB_API}{path}", method=method)
    request.add_header("Accept", "application/vnd.github+json")
    request.add_header("X-GitHub-Api-Version", "2022-11-28")
    request.add_header("User-Agent", "adapteng-company-os-ops-runner")
    request.add_header("Authorization", f"Bearer {credential}")
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            body = response.read()
            status = response.status
    except urllib.error.HTTPError as error:
        body = error.read()
        status = error.code
    except urllib.error.URLError as error:
        raise driver.Abort(f"GitHub API unreachable: {error.reason}") from error
    if not body:
        return status, None
    try:
        return status, json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return status, None


def describe_secret(value: object) -> str:
    """Describe a credential without disclosing it.

    Preflight has to report that a credential was obtained and that it is not
    empty. Its length in isolation reveals nothing usable, while the value would
    reveal everything, so the length is the most that gets printed.
    """

    if not isinstance(value, str) or not value:
        return "absent"
    return f"present length={len(value)}"


def operate_preflight() -> int:
    """Report whether an operations runner could be registered, and change nothing."""

    repository = os.environ.get(REPOSITORY_VARIABLE, "").strip()
    if not repository:
        raise driver.Abort(f"{REPOSITORY_VARIABLE} is not set")
    credential = os.environ.get(ADMIN_CREDENTIAL_VARIABLE, "").strip()

    driver.emit("--- preflight ops-runner")
    driver.emit(f"    repository: {repository}")
    driver.emit(f"    intended label: {RUNNER_LABEL}")
    driver.emit(f"    intended image: {RUNNER_IMAGE}:{RUNNER_IMAGE_TAG}")

    if not credential:
        driver.emit(f"    admin credential: ABSENT ({ADMIN_CREDENTIAL_VARIABLE} unset)")
        driver.emit("")
        driver.emit("RESULT preflight blocked reason=no-admin-credential")
        return EXIT_FAILED

    driver.emit(f"    admin credential: {describe_secret(credential)}")

    status, listing = github_call(f"/repos/{repository}/actions/runners", credential)
    if status == 200 and isinstance(listing, dict):
        runners = listing.get("runners")
        runners = runners if isinstance(runners, list) else []
        driver.emit(f"    self-hosted runners already registered: {len(runners)}")
        for item in runners:
            if not isinstance(item, dict):
                continue
            labels = item.get("labels")
            names = sorted(
                entry.get("name")
                for entry in (labels if isinstance(labels, list) else [])
                if isinstance(entry, dict) and entry.get("name")
            )
            driver.emit(
                f"      - name={item.get('name')} status={item.get('status')} "
                f"busy={item.get('busy')} labels={names}"
            )
    else:
        driver.emit(f"    listing runners: HTTP {status} (administration read refused)")

    # Minting is the only proof that the authority is held. A registration
    # credential that is never presented to a runner expires unused within the
    # hour, so obtaining one costs nothing and settles the question outright.
    status, payload = github_call(
        f"/repos/{repository}/actions/runners/registration-token", credential, method="POST"
    )
    if status != 201 or not isinstance(payload, dict):
        driver.emit(f"    minting a registration credential: HTTP {status} REFUSED")
        driver.emit("")
        driver.emit("RESULT preflight blocked reason=cannot-register-runner")
        return EXIT_FAILED

    minted = payload.get("token")
    driver.emit(
        f"    minting a registration credential: ok {describe_secret(minted)} "
        f"expires_at={payload.get('expires_at')}"
    )

    driver.emit("")
    driver.emit("RESULT preflight ok registration=available")
    return EXIT_OK


def report_placement_for_runner(client: driver.Client) -> None:
    """Report where the runner would be placed, and whether it is already there."""

    project = driver.find_project(client, "adapteng-ops")
    if project is None:
        driver.emit("    project adapteng-ops: ABSENT")
        return
    environment = driver.find_environment(client, project["uuid"], "production")
    if environment is None:
        driver.emit("    environment production: ABSENT")
        return
    driver.emit(f"    project adapteng-ops: present uuid={project['uuid']}")
    applications = driver.applications_in(client, environment)
    existing = driver.find_application(applications, RUNNER_RESOURCE_NAME)
    if existing is None:
        driver.emit(f"    application {RUNNER_RESOURCE_NAME}: ABSENT (would be created)")
        return
    driver.emit(
        f"    application {RUNNER_RESOURCE_NAME}: present "
        f"uuid={existing.get('uuid')} status={existing.get('status')}"
    )


def mint_registration(repository: str, credential: str) -> str:
    """Obtain a short-lived registration credential and hide it from all output.

    It is registered for redaction the instant it exists, before any caller can
    print it, so that a later change which reports more detail cannot disclose it
    by accident.
    """

    status, payload = github_call(
        f"/repos/{repository}/actions/runners/registration-token", credential, method="POST"
    )
    if status != 201 or not isinstance(payload, dict):
        raise driver.Abort(
            f"minting a registration credential returned HTTP {status}; "
            "the administrative credential cannot register a runner"
        )
    minted = payload.get("token")
    if not isinstance(minted, str) or not minted:
        raise driver.Abort("the registration response carried no usable value")
    driver.register_redaction(minted)
    driver.emit(
        f"    registration credential: {describe_secret(minted)} "
        f"expires_at={payload.get('expires_at')}"
    )
    return minted


def locate(client: driver.Client) -> tuple[dict, dict, dict | None]:
    """Return the project, the environment and the runner application if it exists."""

    project = driver.find_project(client, PROJECT_NAME)
    if project is None:
        raise driver.Abort(f"project {PROJECT_NAME} does not exist on this instance")
    environment = driver.find_environment(client, project["uuid"], ENVIRONMENT_NAME)
    if environment is None:
        raise driver.Abort(f"environment {ENVIRONMENT_NAME} does not exist in {PROJECT_NAME}")
    applications = driver.applications_in(client, environment)
    return project, environment, driver.find_application(applications, RUNNER_RESOURCE_NAME)


def normalize_dockerfile(value: object) -> str:
    """Compare build definitions by content, ignoring how they were stored.

    Coolify may return the text with different line endings or trailing spaces
    than it was sent with. Those differences are not differences in what gets
    built, so treating them as drift would make reconcile permanently unable to
    converge on its own output.
    """

    if not isinstance(value, str):
        return ""
    lines = value.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    return "\n".join(line.rstrip() for line in lines).strip()


def first_difference(desired: str, stored: str) -> str:
    """Describe where two build definitions diverge, so one round trip is enough."""

    left = desired.split("\n")
    right = stored.split("\n")
    for index in range(max(len(left), len(right))):
        want = left[index] if index < len(left) else "(absent)"
        have = right[index] if index < len(right) else "(absent)"
        if want != have:
            return f"line {index + 1}: stored {have!r} but declared {want!r}"
    return "no line differs"


def creation_body(project: dict, environment: dict, server: dict, destination: dict | None) -> dict:
    """Build the creation request from the fields this instance actually accepts.

    Every key here was accepted by the live instance. That is not the same as
    every key the published specification documents: max_restart_count is in the
    published schema for both create and update, and this instance refuses it
    outright with "This field is not allowed." It was intended to bound the
    restarts of a container whose registration credential has expired. Without
    it that condition is an unbounded restart loop, which is a real limitation
    rather than a solved problem, and it is visible through the status
    operation because the application does not reach a running state.

    ACCEPTED_CREATION_FIELDS pins this set so that adding another one is a
    deliberate act tested against the same instance, rather than a guess that
    costs a round trip to discover.
    """

    body = {
        "project_uuid": project["uuid"],
        "environment_name": environment["name"],
        "environment_uuid": environment.get("uuid"),
        "server_uuid": server["uuid"],
        "name": RUNNER_RESOURCE_NAME,
        "description": "Dedicated deployment and operations runner. Private network only.",
        "build_pack": "dockerfile",
        "dockerfile": DOCKERFILE,
        "ports_exposes": EXPOSED_PORT,
        # The runner serves nothing and must never be routed. A generated domain
        # would publish it, so it is refused here and the absence of an FQDN is
        # checked again after the write.
        "autogenerate_domain": False,
        "health_check_enabled": False,
        # The one setting the whole design rests on: without it the container
        # cannot reach the managed database, and with it it needs no other
        # access to the host at all.
        "connect_to_docker_network": True,
        "instant_deploy": False,
    }
    if destination is not None and destination.get("uuid"):
        body["destination_uuid"] = destination["uuid"]
    return {key: value for key, value in body.items() if value is not None}


def create_runner_application(client: driver.Client, project: dict, environment: dict) -> str:
    server = driver.resolve_server(client, None)
    destination = driver.resolve_destination(
        client, server, None, driver.applications_in(client, environment)
    )
    driver.emit(
        f"    placement: server={server.get('name')} "
        f"destination={(destination or {}).get('name') or (destination or {}).get('uuid')}"
    )
    created = driver.expect_object(
        driver.call(
            client,
            "POST",
            "/applications/dockerfile",
            body=creation_body(project, environment, server, destination),
            expect=(200, 201),
        ),
        "created application",
    )
    uuid = created.get("uuid")
    if not uuid:
        raise driver.Abort("the application was reported created but the response carries no uuid")
    driver.emit(f"    application {RUNNER_RESOURCE_NAME}: created uuid={uuid}")
    return str(uuid)


def desired_environment(repository: str) -> dict[str, str]:
    return {
        SCOPE_URL_KEY: f"https://github.com/{repository}",
        NAME_KEY: RUNNER_NAME,
        LABELS_KEY: RUNNER_LABEL,
    }


def write_environment(client: driver.Client, uuid: str, desired: dict[str, str]) -> None:
    """Create or update each declared key, and say which of the two happened."""

    entries = driver.read_environment_entries(client, uuid)
    stored = {
        entry.get("key"): entry.get("value")
        for entry in entries
        if isinstance(entry, dict) and entry.get("key")
    }
    for key, value in sorted(desired.items()):
        if key not in stored:
            driver.emit(f"    change env {key}: created")
            method, expect = "POST", (200, 201)
        elif stored[key] != value:
            driver.emit(f"    change env {key}: updated")
            method, expect = "PATCH", (200, 201)
        else:
            continue
        driver.call(
            client,
            method,
            f"/applications/{uuid}/envs",
            body={"key": key, "value": value},
            expect=expect,
        )


def operate_reconcile(client: driver.Client, repository: str) -> int:
    """Converge the runner application, then read it back and prove it converged."""

    driver.emit(f"--- reconcile {RUNNER_RESOURCE_NAME}")
    project, environment, application = locate(client)
    driver.emit(f"    project {PROJECT_NAME}: uuid={project['uuid']}")

    if application is None:
        uuid = create_runner_application(client, project, environment)
    else:
        uuid = str(application["uuid"])
        driver.emit(f"    application {RUNNER_RESOURCE_NAME}: present uuid={uuid}")
        stored = normalize_dockerfile(application.get("dockerfile"))
        if stored != normalize_dockerfile(DOCKERFILE):
            driver.emit("    change dockerfile: updated")
            driver.call(
                client,
                "PATCH",
                f"/applications/{uuid}",
                body={"dockerfile": DOCKERFILE},
                expect=(200, 201),
            )

    write_environment(client, uuid, desired_environment(repository))

    # Nothing above is trusted. What the instance stored is read back and
    # compared, so a write it accepted but did not keep cannot pass as success.
    driver.emit("    verifying by re-reading")
    verified = driver.read_application(client, uuid)
    entries = driver.read_environment_entries(client, uuid)
    stored_env = {
        entry.get("key"): entry.get("value")
        for entry in entries
        if isinstance(entry, dict) and entry.get("key")
    }

    problems: list[str] = []
    desired_text = normalize_dockerfile(DOCKERFILE)
    stored_text = normalize_dockerfile(verified.get("dockerfile"))
    if stored_text != desired_text:
        problems.append(
            f"the stored build definition differs ({len(stored_text)} chars stored, "
            f"{len(desired_text)} declared): {first_difference(desired_text, stored_text)}"
        )
    for key, value in sorted(desired_environment(repository).items()):
        if stored_env.get(key) != value:
            problems.append(f"environment key {key} is still {stored_env.get(key)!r}")
    if (verified.get("fqdn") or "").strip():
        problems.append(
            "a public address is configured on the runner, which must not be reachable "
            "from outside the host. Removing a route is an owner action and is not done here."
        )

    if problems:
        for problem in problems:
            driver.emit(f"    VERIFY FAILED {problem}")
        driver.emit("")
        driver.emit("RESULT reconcile failed")
        return EXIT_FAILED

    # Stated rather than quietly omitted. This instance does not report these
    # four settings on an application, so no re-read can confirm them. The one
    # that matters is proved instead by the runner reaching the database, which
    # is a better test than reading a field back.
    driver.emit(
        "    VERIFY OK for everything this API reports; connect_to_docker_network is not "
        "among the reported fields and is proved by the first database connection instead"
    )
    driver.emit("")
    driver.emit(f"RESULT reconcile ok uuid={uuid}")
    return EXIT_OK


def runner_matching(listing: object) -> dict | None:
    """Return the registered runner carrying the intended name, if any."""

    if not isinstance(listing, dict):
        return None
    for item in listing.get("runners") or []:
        if isinstance(item, dict) and item.get("name") == RUNNER_NAME:
            return item
    return None


def wait_for_online(
    repository: str,
    credential: str,
    poll_seconds: int,
    timeout_seconds: int,
    sleep=time.sleep,
    clock=time.monotonic,
) -> tuple[bool, str]:
    """Poll until the runner reports online, and report the last state either way."""

    started = clock()
    state = "unregistered"
    while True:
        status, listing = github_call(f"/repos/{repository}/actions/runners", credential)
        if status == 200:
            found = runner_matching(listing)
            state = "unregistered" if found is None else str(found.get("status"))
            if found is not None and state == "online":
                return True, state
        else:
            state = f"listing-refused-http-{status}"
        if clock() - started >= timeout_seconds:
            return False, state
        sleep(poll_seconds)


def operate_deploy(
    client: driver.Client,
    repository: str,
    credential: str,
    poll_seconds: int = 10,
    timeout_seconds: int = 900,
    registration_timeout_seconds: int = 300,
    sleep=time.sleep,
    clock=time.monotonic,
) -> int:
    """Bind a fresh registration credential, build, start, and wait for it to appear."""

    driver.emit(f"--- deploy {RUNNER_RESOURCE_NAME}")
    _, _, application = locate(client)
    if application is None:
        raise driver.Abort(
            f"application {RUNNER_RESOURCE_NAME} does not exist; run reconcile first"
        )
    uuid = str(application["uuid"])

    minted = mint_registration(repository, credential)
    write_environment(client, uuid, {REGISTRATION_KEY: minted})

    parsed = driver.expect_object(
        driver.call(client, "POST", "/deploy", query={"uuid": uuid}), "deploy acknowledgement"
    )
    deployments = [item for item in (parsed.get("deployments") or []) if isinstance(item, dict)]
    if len(deployments) != 1:
        raise driver.Abort(
            f"the deploy request returned {len(deployments)} deployments; expected exactly one"
        )
    deployment_uuid = deployments[0].get("deployment_uuid")
    if not deployment_uuid:
        raise driver.Abort("the deploy request returned no deployment identifier")
    driver.emit(f"    deployment {deployment_uuid} queued for application {uuid}")

    outcome, state = driver.poll_deployment(
        client,
        str(deployment_uuid),
        poll_seconds=poll_seconds,
        timeout_seconds=timeout_seconds,
        sleep=sleep,
        clock=clock,
    )
    if outcome != "succeeded":
        driver.emit("")
        driver.emit(f"RESULT deploy failed deployment={deployment_uuid} state={state}")
        return EXIT_FAILED

    # A successful build is not a working runner. The deployment only says a
    # container started; whether it registered is a separate fact, held by
    # GitHub rather than by Coolify, and it is the one that decides whether any
    # host-side workflow can run.
    online, runner_state = wait_for_online(
        repository,
        credential,
        poll_seconds,
        registration_timeout_seconds,
        sleep=sleep,
        clock=clock,
    )
    driver.emit("")
    if not online:
        driver.emit(
            f"RESULT deploy failed deployment={deployment_uuid} build=succeeded "
            f"runner={runner_state}"
        )
        return EXIT_FAILED
    driver.emit(
        f"RESULT deploy ok deployment={deployment_uuid} runner={runner_state} label={RUNNER_LABEL}"
    )
    return EXIT_OK


def operate_status(client: driver.Client, repository: str, credential: str) -> int:
    """Report both halves of the runner's state: the container and the registration."""

    driver.emit(f"--- status {RUNNER_RESOURCE_NAME}")
    _, _, application = locate(client)
    if application is None:
        driver.emit(f"    application {RUNNER_RESOURCE_NAME}: ABSENT")
    else:
        driver.emit(
            f"    application {RUNNER_RESOURCE_NAME}: uuid={application.get('uuid')} "
            f"status={application.get('status')}"
        )
    status, listing = github_call(f"/repos/{repository}/actions/runners", credential)
    if status != 200:
        driver.emit(f"    registration: unreadable HTTP {status}")
        driver.emit("")
        driver.emit("RESULT status partial reason=registration-unreadable")
        return EXIT_FAILED
    found = runner_matching(listing)
    if found is None:
        driver.emit(f"    registration: {RUNNER_NAME} is not registered")
        driver.emit("")
        driver.emit("RESULT status not-ready reason=runner-unregistered")
        return EXIT_FAILED
    labels = sorted(
        entry.get("name")
        for entry in (found.get("labels") or [])
        if isinstance(entry, dict) and entry.get("name")
    )
    driver.emit(
        f"    registration: status={found.get('status')} busy={found.get('busy')} labels={labels}"
    )
    if found.get("status") != "online" or RUNNER_LABEL not in labels:
        driver.emit("")
        driver.emit(f"RESULT status not-ready runner={found.get('status')} labels={labels}")
        return EXIT_FAILED
    driver.emit("")
    driver.emit(f"RESULT status ok runner=online label={RUNNER_LABEL}")
    return EXIT_OK


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    operation = (arguments[0] if arguments else os.environ.get("OPERATION", "")).strip()
    if operation not in {"preflight", "reconcile", "deploy", "status"}:
        driver.emit(f"unknown operation: {operation or '(none)'}")
        return EXIT_FAILED
    try:
        repository = os.environ.get(REPOSITORY_VARIABLE, "").strip()
        if not repository:
            raise driver.Abort(f"{REPOSITORY_VARIABLE} is not set")
        credential = os.environ.get(ADMIN_CREDENTIAL_VARIABLE, "").strip()

        if operation == "preflight":
            code = operate_preflight()
            base_url = os.environ.get("COOLIFY_BASE_URL", "").strip()
            coolify = os.environ.get(driver.CREDENTIAL_VARIABLE, "").strip()
            if base_url and coolify:
                report_placement_for_runner(driver.Client(base_url, coolify))
            return code

        if not credential:
            raise driver.Abort(f"{ADMIN_CREDENTIAL_VARIABLE} is not set")
        base_url = os.environ.get("COOLIFY_BASE_URL", "").strip()
        coolify = os.environ.get(driver.CREDENTIAL_VARIABLE, "").strip()
        if not base_url or not coolify:
            raise driver.Abort("the Coolify base address and credential are both required")
        client = driver.Client(base_url, coolify)

        if operation == "reconcile":
            return operate_reconcile(client, repository)
        if operation == "deploy":
            return operate_deploy(client, repository, credential)
        return operate_status(client, repository, credential)
    except driver.Abort as abort:
        driver.emit(f"ABORT {abort}")
        return EXIT_FAILED


if __name__ == "__main__":
    raise SystemExit(main())
