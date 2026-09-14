#!/usr/bin/env python3
"""Bind adapteng-drive-adapter's Google credential without ever reading it back.

The governed-operations code path (services/adapteng-drive-adapter/app/
config.py::build_governed_client) reads a *different* credential contract
than the base-structure CLI: GOOGLE_SERVICE_ACCOUNT_JSON_B64, not
GOOGLE_SERVICE_ACCOUNT_JSON. This is not a different credential -- it is the
same existing service-account key, base64-encoded, because that config
loader was written to a locked, separate contract (services/adapteng-
drive-adapter/.env.example: "Governed operations have a separate, locked
credential contract. They do NOT read GOOGLE_SERVICE_ACCOUNT_JSON"). Minting
a new Google key was explicitly out of scope unless genuinely unavoidable;
this exists because it is avoidable -- the transformation is base64, not a
new credential, and it happens once, in memory, on the runner that already
holds GOOGLE_SERVICE_ACCOUNT_JSON as a secret, and the encoded result is
written straight to Coolify and never printed.

Operations:
  bind-google-credential   base64-encode the existing service-account
                            material and write it to drive-adapter's Coolify
                            environment
  status                    report what is bound, by shape and length only
"""

from __future__ import annotations

import base64
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import ai_gateway_credentials as gateway_creds  # noqa: E402
import coolify_deploy as driver  # noqa: E402

RESOURCE_NAME = "drive-adapter"
PROJECT_NAME = "adapteng-ops"
ENVIRONMENT_NAME = "production"
ENCODED_KEY = "GOOGLE_SERVICE_ACCOUNT_JSON_B64"


def locate(client: driver.Client) -> str:
    """Find the drive-adapter application, or say plainly that it is not there yet."""

    project = driver.find_project(client, PROJECT_NAME)
    if project is None:
        raise driver.Abort(
            f"project {PROJECT_NAME} does not exist, so there is no application to "
            "bind credentials to; reconcile drive-adapter first"
        )
    environment = driver.find_environment(client, project["uuid"], ENVIRONMENT_NAME)
    if environment is None:
        raise driver.Abort(
            f"environment {ENVIRONMENT_NAME} does not exist in {PROJECT_NAME}; "
            "reconcile drive-adapter first"
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


def operate_bind_google_credential(client: driver.Client, material: str) -> int:
    """Base64-encode the existing service-account key and mount it by value.

    Validates the material is a real service-account key (same check
    ai_gateway_credentials.py's bind-adc already applies to the dedicated
    Vertex key) before encoding it, so a truncated or wrong-typed secret
    fails here rather than at the first Drive call.
    """

    driver.emit(f"--- bind-google-credential {RESOURCE_NAME}")
    gateway_creds.parse_service_account(material)  # raises Abort on a bad shape
    uuid = locate(client)
    driver.emit(f"    application {RESOURCE_NAME}: uuid={uuid}")

    encoded = base64.b64encode(material.encode("utf-8")).decode("ascii")
    driver.register_redaction(encoded)
    driver.emit(f"    encoded material: {gateway_creds.describe_material(encoded)}")

    gateway_creds.write_environment_value(client, uuid, ENCODED_KEY, encoded)

    entries = driver.read_environment_entries(client, uuid)
    present = any(
        isinstance(entry, dict) and entry.get("key") == ENCODED_KEY for entry in entries
    )
    if not present:
        raise driver.Abort(
            f"{ENCODED_KEY} was accepted but is not reported on the application, so "
            "drive-adapter would fail closed at the first governed call while this "
            "reports success"
        )
    driver.emit(f"    env {ENCODED_KEY}: recorded")
    driver.emit("")
    driver.emit(f"    the value is held only by Coolify, under {ENCODED_KEY} on this")
    driver.emit("    application. It is not copied into a repository secret and is")
    driver.emit("    never printed. No new Google credential was created -- this is")
    driver.emit("    the existing GOOGLE_SERVICE_ACCOUNT_JSON material, re-encoded.")
    driver.emit("RESULT bind-google-credential ok")
    return 0


def operate_status(client: driver.Client) -> int:
    """Report what is bound, by presence and shape, never by value."""

    driver.emit(f"--- status {RESOURCE_NAME}")
    uuid = locate(client)
    driver.emit(f"    application {RESOURCE_NAME}: uuid={uuid}")

    entries = driver.read_environment_entries(client, uuid)
    stored = {
        entry.get("key"): entry.get("value")
        for entry in entries
        if isinstance(entry, dict) and entry.get("key")
    }
    driver.emit(f"    env {ENCODED_KEY}: {'present' if ENCODED_KEY in stored else 'ABSENT'}")
    for key in (
        "DRIVE_SERVICE_BEARER_TOKENS",
        "DRIVE_BRIDGE_REPLAY_DATABASE_URL",
        "GOOGLE_WORKSPACE_DELEGATED_USER",
        "GOOGLE_DRIVE_SCOPE",
        "GOOGLE_CLOUD_PROJECT",
    ):
        driver.emit(f"    env {key}: {'present' if key in stored else 'ABSENT'}")

    driver.emit("")
    ready = all(
        key in stored
        for key in (
            ENCODED_KEY,
            "DRIVE_SERVICE_BEARER_TOKENS",
            "DRIVE_BRIDGE_REPLAY_DATABASE_URL",
        )
    )
    driver.emit(f"RESULT status ok credentials={'bound' if ready else 'incomplete'}")
    return 0


def parse_arguments(argv: list[str]):
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("bind-google-credential", "status"))
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    import os

    arguments = parse_arguments(argv)
    try:
        base_url = os.environ.get("COOLIFY_BASE_URL", "")
        credential = os.environ.get("COOLIFY_API_CREDENTIAL", "")
        if not credential:
            raise driver.Abort("no Coolify credential was supplied, so nothing was attempted")
        driver.register_redaction(credential)
        client = driver.Client(base_url, credential)
        if arguments.operation == "bind-google-credential":
            material = os.environ.get("GOOGLE_SERVICE_ACCOUNT_MATERIAL", "")
            if not material:
                raise driver.Abort(
                    "no service-account material was supplied. It must be the "
                    "existing GOOGLE_SERVICE_ACCOUNT_JSON secret passed through the "
                    "environment; nothing was mounted and nothing was changed."
                )
            driver.register_redaction(material)
            return operate_bind_google_credential(client, material)
        return operate_status(client)
    except driver.Abort as abort:
        driver.emit(f"ABORT {abort}")
        return abort.code


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
