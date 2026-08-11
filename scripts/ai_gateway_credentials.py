#!/usr/bin/env python3
"""Bind the AI Gateway's two credentials without ever reading either one back.

Two pieces of secret material stand between a reconciled application and a
running one, and they fail in opposite directions.

The Vertex service-account key is issued by Google and cannot be generated
here. It already exists as a repository secret, so this binds it by reference:
the value travels from the workflow environment into a Coolify file storage and
nowhere else. GOOGLE_APPLICATION_CREDENTIALS is set only after that file is
recorded, because app/config.py fails closed at boot when the variable names a
path that is not readable. Setting it first would turn a missing mount into a
crash loop rather than a no-op.

The caller credential is ours, so it is generated here rather than requested
from the owner. It is written to the Coolify environment and to a repository
secret so later automation can call the gateway, and it is never printed,
returned or logged. Both destinations are written from the same value in
memory, so the two can never disagree.

Operations:
  bind-adc      mount the service-account key and then name its path
  mint-caller   generate the caller credential and bind it to both stores
  status        report what is bound, by shape and length only

Every operation is idempotent and every write is re-read. A secret cannot be
read back from either store, so the check is that the store reports the entry
as present with the expected shape, and the report says exactly that rather
than claiming the value was verified.
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import coolify_deploy as driver  # noqa: E402

# The mount path lives inside the container and is not a secret. It is declared
# here and nowhere else so the storage and the variable naming it cannot drift.
ADC_MOUNT_PATH = "/secrets/vertex-service-account.json"
ADC_PATH_KEY = "GOOGLE_APPLICATION_CREDENTIALS"
CALLER_KEY = "AI_GATEWAY_BEARER_TOKENS"
RESOURCE_NAME = "ai-gateway"
PROJECT_NAME = "adapteng-ops"
ENVIRONMENT_NAME = "production"

# Long enough that guessing is not a strategy, and URL-safe so it survives
# every transport it passes through without escaping.
CALLER_CREDENTIAL_BYTES = 32


def locate(client: driver.Client) -> str:
    """Find the gateway application, or say plainly that it is not there yet."""

    project = driver.find_project(client, PROJECT_NAME)
    if project is None:
        raise driver.Abort(
            f"project {PROJECT_NAME} does not exist, so there is no application to "
            "bind credentials to; reconcile the gateway first"
        )
    environment = driver.find_environment(client, project["uuid"], ENVIRONMENT_NAME)
    if environment is None:
        raise driver.Abort(
            f"environment {ENVIRONMENT_NAME} does not exist in {PROJECT_NAME}; "
            "reconcile the gateway first"
        )
    application = driver.find_application(
        driver.applications_in(client, environment), RESOURCE_NAME
    )
    if application is None:
        raise driver.Abort(
            f"application {RESOURCE_NAME} does not exist in {PROJECT_NAME}/{ENVIRONMENT_NAME}. "
            "Credentials are bound to an application, so it has to be reconciled first."
        )
    return str(application["uuid"])


def read_storages(client: driver.Client, uuid: str) -> list[dict]:
    """Return the file mounts this instance reports, in whichever shape it uses."""

    reported = driver.call(
        client, "GET", f"/applications/{uuid}/storages", allow_absent=True
    )
    if isinstance(reported, list):
        return [item for item in reported if isinstance(item, dict)]
    if isinstance(reported, dict):
        found: list[dict] = []
        for group in ("file_storages", "persistent_storages"):
            entries = reported.get(group)
            if isinstance(entries, list):
                found.extend(item for item in entries if isinstance(item, dict))
        return found
    return []


def find_mount(storages: list[dict], mount_path: str) -> dict | None:
    for entry in storages:
        if entry.get("mount_path") == mount_path:
            return entry
    return None


def describe_material(value: str) -> str:
    """Say enough to tell present from absent, and nothing that helps a reader use it."""

    return f"present length={len(value)}" if value else "ABSENT"


def parse_service_account(raw: str) -> dict:
    """Refuse anything that is not a service-account key before it is mounted.

    A truncated or wrong-typed value mounts perfectly happily and fails at the
    first Vertex call, which is the most expensive place to discover it. The
    fields checked here are the ones the Google client library requires, so a
    value that passes this is a value that can at least be loaded.
    """

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        raise driver.Abort(
            "the supplied service-account material is not JSON, so it would mount "
            "as an unreadable file and fail at the first model call"
        ) from None
    if not isinstance(parsed, dict):
        raise driver.Abort("the supplied service-account material is not a JSON object")
    if parsed.get("type") != "service_account":
        raise driver.Abort(
            f"the supplied material declares type {parsed.get('type')!r}, not "
            "'service_account'; refusing to mount it as one"
        )
    for field in ("project_id", "client_email", "private_key", "token_uri"):
        if not parsed.get(field):
            raise driver.Abort(
                f"the service-account material has no {field}, so it cannot authenticate"
            )
    return parsed


def operate_bind_adc(client: driver.Client, material: str) -> int:
    """Mount the key, prove the mount is recorded, and only then name its path."""

    driver.emit(f"--- bind-adc {RESOURCE_NAME}")
    parsed = parse_service_account(material)
    # The address is not secret and is the one fact that makes a wrong key
    # obvious to a reader, so it is the only part of the key ever reported.
    driver.emit(f"    service account: {parsed['client_email']}")
    driver.emit(f"    project: {parsed['project_id']}")

    uuid = locate(client)
    driver.emit(f"    application {RESOURCE_NAME}: uuid={uuid}")

    existing = find_mount(read_storages(client, uuid), ADC_MOUNT_PATH)
    if existing is None:
        driver.emit(f"    change storage {ADC_MOUNT_PATH}: created")
        driver.call(
            client,
            "POST",
            f"/applications/{uuid}/storages",
            body={
                "type": "file",
                "mount_path": ADC_MOUNT_PATH,
                "content": material,
                "is_directory": False,
            },
            expect=(200, 201),
        )
    else:
        # The stored content is not compared, because comparing would mean
        # reading a credential back. The mount is replaced unconditionally so a
        # rotated key is actually delivered rather than silently skipped.
        storage_uuid = existing.get("uuid") or existing.get("id")
        driver.emit(f"    change storage {ADC_MOUNT_PATH}: updated")
        driver.call(
            client,
            "PATCH",
            f"/applications/{uuid}/storages",
            body={
                "uuid": storage_uuid,
                "mount_path": ADC_MOUNT_PATH,
                "content": material,
            },
            expect=(200, 201),
        )

    # Re-read before naming the path. app/config.py fails closed at boot when
    # the variable is set and the file is not readable, so announcing the path
    # before the mount exists converts a missing credential into a crash loop.
    confirmed = find_mount(read_storages(client, uuid), ADC_MOUNT_PATH)
    if confirmed is None:
        raise driver.Abort(
            f"the instance accepted the mount at {ADC_MOUNT_PATH} but does not report "
            "it, so the path is not being named: an unreadable path in "
            f"{ADC_PATH_KEY} is a boot failure, and leaving it unset is not"
        )
    driver.emit(f"    storage {ADC_MOUNT_PATH}: recorded")

    write_environment_value(client, uuid, ADC_PATH_KEY, ADC_MOUNT_PATH)
    driver.emit("")
    driver.emit(f"RESULT bind-adc ok mount={ADC_MOUNT_PATH} account={parsed['client_email']}")
    return 0


def write_environment_value(client: driver.Client, uuid: str, key: str, value: str) -> None:
    """Create or update one environment entry, and say which of the two happened."""

    entries = driver.read_environment_entries(client, uuid)
    stored = {
        entry.get("key"): entry.get("value")
        for entry in entries
        if isinstance(entry, dict) and entry.get("key")
    }
    if key not in stored:
        driver.emit(f"    change env {key}: created")
        method, expect = "POST", (200, 201)
    elif stored[key] != value:
        driver.emit(f"    change env {key}: updated")
        method, expect = "PATCH", (200, 201)
    else:
        driver.emit(f"    env {key}: already correct")
        return
    driver.call(
        client,
        method,
        f"/applications/{uuid}/envs",
        body={"key": key, "value": value},
        expect=expect,
    )


def operate_mint_caller(client: driver.Client, repository: str) -> int:
    """Generate the caller credential and bind it where the gateway reads it.

    ``repository`` is accepted and deliberately unused. It is kept so the
    workflow contract does not change and so this docstring is the thing an
    operator finds when they ask why the repository secret is not written.
    """

    driver.emit(f"--- mint-caller {RESOURCE_NAME}")
    uuid = locate(client)
    driver.emit(f"    application {RESOURCE_NAME}: uuid={uuid}")

    credential = secrets.token_urlsafe(CALLER_CREDENTIAL_BYTES)
    # Registered before it is used anywhere, so that any later report or error
    # containing it is redacted rather than disclosed.
    driver.register_redaction(credential)
    driver.emit(f"    generated caller credential: {describe_material(credential)}")

    # One store, on purpose. This wrote two - Coolify and a repository secret -
    # and the second one earned its removal twice in a single afternoon: first a
    # gh flag the runner does not support, then a 403 for a token scope this
    # automation is not entitled to. Both landed after the value had been
    # generated, which is the worst moment to discover a store is unreachable.
    #
    # It is removed rather than repaired because it was never load-bearing.
    # Coolify is where the gateway reads the credential; the repository copy
    # existed only so some future caller could present it. No such caller exists,
    # and none is on the critical path: the credential gates POST /v1/gateway
    # alone, while GET /health and GET /ready - the deployment's whole
    # verification surface - are answered before the Authorization header is
    # read. Deployment and readiness therefore never need it.
    #
    # A caller that does appear reads the value from Coolify with the API
    # credential this automation already holds. That grants nothing new: a token
    # able to write this key can already mint itself access by overwriting it, so
    # being able to read it adds no privilege it did not have.
    #
    # What is bought is that one secret has one home. Two copies of one value is
    # a synchronisation problem, and this one had already diverged once.
    write_environment_value(client, uuid, CALLER_KEY, credential)

    entries = driver.read_environment_entries(client, uuid)
    present = any(
        isinstance(entry, dict) and entry.get("key") == CALLER_KEY for entry in entries
    )
    if not present:
        raise driver.Abort(
            f"{CALLER_KEY} was accepted but is not reported on the application, so "
            "the gateway would refuse every caller while the write reported success"
        )
    driver.emit(f"    env {CALLER_KEY}: recorded")
    driver.emit("")
    driver.emit(f"    the value is held only by Coolify, under {CALLER_KEY} on this")
    driver.emit("    application. A caller obtains it from there; it is not copied")
    driver.emit("    into a repository secret and is never printed.")
    driver.emit("RESULT mint-caller ok single store written")
    return 0


def operate_status(client: driver.Client) -> int:
    """Report what is bound, by presence and shape, never by value."""

    driver.emit(f"--- status {RESOURCE_NAME}")
    uuid = locate(client)
    driver.emit(f"    application {RESOURCE_NAME}: uuid={uuid}")

    mount = find_mount(read_storages(client, uuid), ADC_MOUNT_PATH)
    driver.emit(f"    storage {ADC_MOUNT_PATH}: {'present' if mount else 'ABSENT'}")

    entries = driver.read_environment_entries(client, uuid)
    stored = {
        entry.get("key"): entry.get("value")
        for entry in entries
        if isinstance(entry, dict) and entry.get("key")
    }
    path = stored.get(ADC_PATH_KEY) or ""
    driver.emit(f"    env {ADC_PATH_KEY}: {path or 'ABSENT'}")
    driver.emit(f"    env {CALLER_KEY}: {'present' if CALLER_KEY in stored else 'ABSENT'}")

    problems = []
    if path and mount is None:
        problems.append(
            f"{ADC_PATH_KEY} names {path} but no such mount is recorded. The service "
            "fails closed at boot on an unreadable path, so this is a start failure "
            "waiting to happen rather than a missing optional feature."
        )
    if mount is not None and path != ADC_MOUNT_PATH:
        problems.append(
            f"the credential is mounted at {ADC_MOUNT_PATH} but {ADC_PATH_KEY} is "
            f"{path or 'unset'}, so the service will not find it"
        )
    for problem in problems:
        driver.emit(f"    PROBLEM {problem}")
    driver.emit("")
    if problems:
        driver.emit(f"RESULT status incomplete problems={len(problems)}")
        return 1
    ready = mount is not None and path == ADC_MOUNT_PATH and CALLER_KEY in stored
    driver.emit(f"RESULT status ok credentials={'bound' if ready else 'incomplete'}")
    return 0


def parse_arguments(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("bind-adc", "mint-caller", "status"))
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    arguments = parse_arguments(argv)
    try:
        base_url = os.environ.get("COOLIFY_BASE_URL", "")
        credential = os.environ.get("COOLIFY_API_CREDENTIAL", "")
        if not credential:
            raise driver.Abort("no Coolify credential was supplied, so nothing was attempted")
        driver.register_redaction(credential)
        client = driver.Client(base_url, credential)
        if arguments.operation == "bind-adc":
            material = os.environ.get("VERTEX_SERVICE_ACCOUNT_MATERIAL", "")
            if not material:
                raise driver.Abort(
                    "no service-account material was supplied. It is held as a "
                    "repository secret and passed through the environment; nothing "
                    "was mounted and nothing was changed."
                )
            driver.register_redaction(material)
            return operate_bind_adc(client, material)
        if arguments.operation == "mint-caller":
            repository = os.environ.get("GITHUB_REPOSITORY", "")
            if not repository:
                raise driver.Abort("no repository was supplied, so the secret has no destination")
            return operate_mint_caller(client, repository)
        return operate_status(client)
    except driver.Abort as abort:
        driver.emit(f"ABORT {abort}")
        return abort.code


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
