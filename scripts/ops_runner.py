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

EXIT_OK = 0
EXIT_FAILED = 1


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


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    operation = (arguments[0] if arguments else os.environ.get("OPERATION", "")).strip()
    if operation != "preflight":
        driver.emit(f"unknown operation: {operation or '(none)'}")
        return EXIT_FAILED
    try:
        code = operate_preflight()
        base_url = os.environ.get("COOLIFY_BASE_URL", "").strip()
        credential = os.environ.get(driver.CREDENTIAL_VARIABLE, "").strip()
        if base_url and credential:
            client = driver.Client(base_url, credential)
            report_placement_for_runner(client)
        return code
    except driver.Abort as abort:
        driver.emit(f"ABORT {abort}")
        return EXIT_FAILED


if __name__ == "__main__":
    raise SystemExit(main())
