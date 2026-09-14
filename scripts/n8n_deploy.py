#!/usr/bin/env python3
"""Drive the self-hosted n8n REST API for the governed-agent-pilot canary.

n8n.adapteng.com is a separate authority from n8n Cloud (91 legacy MM/LM
workflows, a completely different account this script must never touch).
Every operation here targets N8N_BASE_URL by reference and authenticates
with N8N_API_CREDENTIAL by reference; neither the base URL structure nor
this script ever asserts a value for either, and the credential is
registered for redaction before it is used anywhere.

Operations:
  status         confirm reachability and that this is the self-hosted
                 instance, not n8n Cloud, by presence of known workflow ids
  export         GET the full definition of every live workflow and upload
                 it as a build artifact -- evidence of state before import
  import-canary  create-or-update exactly the MM-32 governed-agent-pilot
                 canary workflow, by name, from automation-platform's
                 reviewed n8n/workflows/experimental/ file, and never touch
                 any other workflow. Always leaves it inactive.
  bind-canary-credentials
                 the committed workflow file carries three placeholder
                 httpHeaderAuth credential ids (REPLACE_WITH_*) rather than
                 real ones, matching this project's rule that a credential
                 value never lands in a repository. This operation creates
                 the three real n8n credentials from the tokens the deployed
                 services already trust (read from this repository's own
                 secrets, and for the Baserow adapter's, from its Coolify
                 environment -- never printed), then re-imports the canary
                 with the placeholders replaced by the real ids. n8n's public
                 API offers no way to look a credential up by name, so this
                 is a one-time bootstrap: re-running it creates three more,
                 unused but harmless, credentials rather than reusing the
                 first three.
  run-canary     execute the canary's manual trigger once and report the
                 execution id, or report clearly if this n8n version's
                 public API does not expose that endpoint
  canary-executions
                 read-only. Lists the canary's most recent executions and,
                 for each, prints the "Build Redacted Result" node's output --
                 the one node in this workflow deliberately designed to carry
                 only non-sensitive identifiers (task/run ids, the Baserow
                 business_id, the Drive folder/file ids), never raw model
                 output or credentials. Evidence for the mission report,
                 read back rather than retyped from the n8n UI.

Every write here is scoped to one workflow, matched by name against the
three self-hosted workflows already known (AUT-001, WEB-002, the L1 proof)
plus whatever this operation has itself created -- so a name collision with
an unrelated workflow stops the run rather than overwriting it.
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import coolify_deploy as driver  # noqa: E402  (reuse emit/Abort/EXIT_*/Client/etc.)

API_PREFIX = "/api/v1"
BASE_URL_VARIABLE = "N8N_BASE_URL"
CREDENTIAL_VARIABLE = "N8N_API_CREDENTIAL"
OPERATIONS = (
    "status",
    "export",
    "import-canary",
    "bind-canary-credentials",
    "run-canary",
    "canary-executions",
)
REDACTED_RESULT_NODE = "Build Redacted Result"

# The committed canary file's three placeholder credential ids, and the
# deployed service each corresponds to. Kept next to CREDENTIAL_NAMES so the
# two can never drift apart silently.
PLACEHOLDER_CREDENTIAL_IDS = {
    "agent-runtime": "REPLACE_WITH_AGENT_RUNTIME_BEARER_CREDENTIAL_ID",
    "baserow-adapter": "REPLACE_WITH_BASEROW_ADAPTER_TOKEN_CREDENTIAL_ID",
    "drive-adapter": "REPLACE_WITH_DRIVE_ADAPTER_TOKEN_CREDENTIAL_ID",
}
CREDENTIAL_NAMES = {
    "agent-runtime": "Governed Agent Runtime Bearer (canary)",
    "baserow-adapter": "Baserow Adapter Bearer (canary)",
    "drive-adapter": "Drive Adapter Bearer (canary)",
}
BASEROW_ADAPTER_ENV_KEY = "ADAPTER_SERVICE_TOKEN"

# Known self-hosted workflow ids (runbooks/n8n-operations.md). Their presence
# is the positive signal this is n8n.adapteng.com; their absence, combined
# with a workflow COUNT in the dozens rather than single digits, is the
# signal this has drifted onto n8n Cloud instead.
KNOWN_SELF_HOSTED_WORKFLOW_IDS = frozenset({
    "NsWG1hD8VmIRRwCv",  # AUT-001
    "05ytz5If9kHUOYuA",  # WEB-002
})
# A known n8n Cloud workflow id (registry/services.yaml n8n-cloud section).
# Its PRESENCE is a hard stop: it proves this run is pointed at the wrong
# account regardless of what N8N_BASE_URL claims to be.
KNOWN_N8N_CLOUD_WORKFLOW_ID = "RAPjKSnj6EY7axtb"  # MM-08 Lead Intake & Triage

CANARY_NAME = "MM-32 Governed Agent Pilot Canary"
CANARY_SOURCE_REPO = "Ivan-Shyla/adapteng-automation-platform"
CANARY_SOURCE_PATH = "n8n/workflows/experimental/MM-32-governed-agent-pilot-canary.json"


class N8nClient:
    """A small JSON client for the self-hosted n8n public API.

    Refuses DELETE outright, matching this repository's other API drivers:
    removing a workflow is never reachable from this tool.
    """

    def __init__(self, base_url: str, credential: str, timeout: int = 30) -> None:
        self.base_url = base_url.rstrip("/")
        self._credential = credential
        self._timeout = timeout

    def request(
        self,
        method: str,
        path: str,
        body: dict | list | None = None,
        query: dict | None = None,
    ) -> tuple[int, object]:
        verb = method.upper()
        if verb == "DELETE":
            raise driver.Abort(
                "DELETE is not available in this tool; removing a workflow is an owner action",
                driver.EXIT_MISCONFIGURED,
            )
        url = f"{self.base_url}{API_PREFIX}{path}"
        if query:
            url = f"{url}?{urllib.parse.urlencode(query)}"
        data = None if body is None else json.dumps(body).encode("utf-8")
        message = urllib.request.Request(url, data=data, method=verb)
        message.add_header("X-N8N-API-KEY", self._credential)
        message.add_header("Accept", "application/json")
        message.add_header("User-Agent", "adapteng-n8n-deploy")
        if data is not None:
            message.add_header("Content-Type", "application/json; charset=utf-8")
        try:
            with urllib.request.urlopen(message, timeout=self._timeout) as response:
                raw = response.read().decode("utf-8", "replace")
                return response.status, (json.loads(raw) if raw.strip() else None)
        except urllib.error.HTTPError as error:
            raw = error.read().decode("utf-8", "replace")
            try:
                parsed = json.loads(raw) if raw.strip() else None
            except json.JSONDecodeError:
                parsed = {"message": raw[:300]}
            return error.code, parsed
        except json.JSONDecodeError as error:
            raise driver.Abort(f"the API returned a body that is not JSON: {error}") from error
        except OSError as error:
            raise driver.Unreachable(
                f"the API at {self.base_url} is unreachable: {error.__class__.__name__}"
            ) from error


def call(
    client: N8nClient,
    method: str,
    path: str,
    *,
    body: dict | list | None = None,
    query: dict | None = None,
    expect: tuple[int, ...] = (200,),
) -> object:
    status, parsed = client.request(method, path, body=body, query=query)
    if status not in expect:
        raise driver.Abort(
            f"{method.upper()} {path} returned HTTP {status} "
            f"(expected {list(expect)}): {driver.api_message(parsed)}"
        )
    return parsed


def list_all_workflows(client: N8nClient) -> list[dict]:
    """Page through GET /workflows and return every entry, minimal fields."""

    workflows: list[dict] = []
    cursor: str | None = None
    for _ in range(200):  # hard bound: this instance has a handful of workflows
        query = {"limit": 250}
        if cursor:
            query["cursor"] = cursor
        page = call(client, "GET", "/workflows", query=query)
        if not isinstance(page, dict):
            raise driver.Abort("GET /workflows did not return an object")
        data = page.get("data")
        if not isinstance(data, list):
            raise driver.Abort("GET /workflows response has no data list")
        workflows.extend(item for item in data if isinstance(item, dict))
        cursor = page.get("nextCursor")
        if not cursor:
            break
    else:
        raise driver.Abort("GET /workflows did not terminate after 200 pages")
    return workflows


def operate_status(client: N8nClient) -> int:
    """Confirm reachability and that this is the self-hosted instance."""

    driver.emit(f"--- status {client.base_url}")
    workflows = list_all_workflows(client)
    ids = {item.get("id") for item in workflows}
    driver.emit(f"    workflow count: {len(workflows)}")
    for item in sorted(workflows, key=lambda w: str(w.get("name"))):
        driver.emit(
            f"    id={item.get('id')} active={item.get('active')} "
            f"name={item.get('name')!r}"
        )

    if KNOWN_N8N_CLOUD_WORKFLOW_ID in ids:
        driver.emit("")
        driver.emit(
            f"    PROBLEM: workflow {KNOWN_N8N_CLOUD_WORKFLOW_ID!r} (a known n8n Cloud "
            "workflow) is present. N8N_BASE_URL points at n8n Cloud, not the "
            "self-hosted instance. Refusing to treat this as the right target."
        )
        driver.emit("RESULT status failed reason=wrong_instance_n8n_cloud")
        return driver.EXIT_FAILED

    known_present = KNOWN_SELF_HOSTED_WORKFLOW_IDS & ids
    driver.emit(
        f"    known self-hosted workflows present: {sorted(known_present) or 'NONE'}"
    )
    if not known_present:
        driver.emit("")
        driver.emit(
            "    PROBLEM: neither AUT-001 nor WEB-002 is present. This may still be "
            "the right instance with those workflows renamed or removed, but it "
            "cannot be confirmed automatically -- stopping rather than guessing."
        )
        driver.emit("RESULT status failed reason=self_hosted_markers_absent")
        return driver.EXIT_FAILED

    driver.emit("")
    driver.emit(f"RESULT status ok workflows={len(workflows)} confirmed_self_hosted=true")
    return driver.EXIT_OK


def operate_export(client: N8nClient, output_dir: Path) -> int:
    """Fetch every live workflow's full definition, one JSON file per id.

    This is the preservation step before import: a point-in-time snapshot of
    exactly what the instance held, written as CI-artifact evidence rather
    than committed to any repository (the exported bodies carry credential
    references and internal structure that do not belong in Git history).
    """

    driver.emit(f"--- export {client.base_url}")
    workflows = list_all_workflows(client)
    output_dir.mkdir(parents=True, exist_ok=True)
    written = 0
    for item in workflows:
        workflow_id = item.get("id")
        if not isinstance(workflow_id, str) or not workflow_id:
            continue
        full = call(client, "GET", f"/workflows/{urllib.parse.quote(workflow_id)}")
        target = output_dir / f"{workflow_id}.json"
        target.write_text(json.dumps(full, indent=2, sort_keys=True), encoding="utf-8")
        written += 1
        driver.emit(f"    exported id={workflow_id} name={item.get('name')!r}")

    manifest = {
        "base_url": client.base_url,
        "workflow_count": len(workflows),
        "exported_count": written,
        "workflow_ids": sorted(
            item.get("id") for item in workflows if isinstance(item.get("id"), str)
        ),
    }
    (output_dir / "_manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8"
    )
    driver.emit("")
    driver.emit(f"RESULT export ok exported={written} dir={output_dir}")
    return driver.EXIT_OK


def operate_import_canary(client: N8nClient, workflow_definition: dict) -> int:
    """Create-or-update exactly the MM-32 canary workflow, by name.

    Never touches any workflow whose name does not match CANARY_NAME exactly.
    A brand-new import is a POST; a re-import of a workflow this tool already
    created is a PUT against that same id, round-tripping name/nodes/
    connections/settings only (runbooks/n8n-operations.md's documented
    gotcha: sending id/active/etc. back is rejected). active is never set to
    true by this operation.
    """

    driver.emit(f"--- import-canary {client.base_url}")
    name = workflow_definition.get("name")
    if name != CANARY_NAME:
        raise driver.Abort(
            f"the source file's name is {name!r}, not {CANARY_NAME!r}; refusing to "
            "import a workflow under a name this operation was not told to expect"
        )

    existing = [
        item for item in list_all_workflows(client) if item.get("name") == CANARY_NAME
    ]
    if len(existing) > 1:
        raise driver.Abort(
            f"{len(existing)} workflows are already named {CANARY_NAME!r}; refusing "
            "to guess which one to update"
        )

    body = {
        "name": workflow_definition["name"],
        "nodes": workflow_definition["nodes"],
        "connections": workflow_definition["connections"],
        "settings": workflow_definition.get("settings", {}),
    }

    if existing:
        workflow_id = existing[0]["id"]
        driver.emit(f"    existing canary found: id={workflow_id}; updating in place")
        updated = call(
            client, "PUT", f"/workflows/{urllib.parse.quote(workflow_id)}", body=body
        )
    else:
        driver.emit("    no existing canary; creating")
        updated = call(client, "POST", "/workflows", body=body, expect=(200, 201))
        workflow_id = updated.get("id") if isinstance(updated, dict) else None

    if not isinstance(updated, dict) or updated.get("id") != workflow_id:
        raise driver.Abort("the write succeeded but the read-back id does not match")
    if updated.get("active"):
        raise driver.Abort(
            f"workflow {workflow_id} came back active=true; this operation must "
            "never leave the canary active"
        )

    driver.emit(f"    workflow id={workflow_id} active={updated.get('active')}")
    driver.emit("")
    driver.emit(f"RESULT import-canary ok id={workflow_id} active=false")
    return driver.EXIT_OK


def create_header_credential(client: N8nClient, name: str, header_value: str) -> str:
    """Create one httpHeaderAuth credential and return its id.

    The value is never logged: it travels from wherever it was read straight
    into this request body. n8n's public API does not return `data` back on
    create, so there is nothing to redact in the response either.
    """

    created = call(
        client,
        "POST",
        "/credentials",
        body={
            "name": name,
            "type": "httpHeaderAuth",
            "data": {"name": "Authorization", "value": header_value},
        },
        expect=(200, 201),
    )
    if not isinstance(created, dict) or not created.get("id"):
        raise driver.Abort(f"creating credential {name!r} did not return an id")
    return str(created["id"])


def substitute_credential_ids(
    workflow_definition: dict, real_ids: dict[str, str]
) -> dict:
    """Return a copy of the workflow with each placeholder id swapped for real.

    Structural, not textual: only `node.credentials.httpHeaderAuth.id` fields
    are touched, so nothing else in the definition can be affected by a
    coincidental string match.
    """

    updated = copy.deepcopy(workflow_definition)
    services_seen: set[str] = set()
    for node in updated.get("nodes", []):
        if not isinstance(node, dict):
            continue
        header_auth = node.get("credentials", {}).get("httpHeaderAuth")
        if not isinstance(header_auth, dict):
            continue
        placeholder = header_auth.get("id")
        for service, expected_placeholder in PLACEHOLDER_CREDENTIAL_IDS.items():
            if placeholder == expected_placeholder:
                header_auth["id"] = real_ids[service]
                services_seen.add(service)
    missing = set(PLACEHOLDER_CREDENTIAL_IDS) - services_seen
    if missing:
        raise driver.Abort(
            f"found no node carrying the placeholder credential id for {sorted(missing)}; "
            "the source file's node shape may have changed"
        )
    return updated


def read_baserow_adapter_token(
    coolify_client: "driver.Client", baserow_adapter_uuid: str
) -> str:
    """Read the Baserow adapter's own inbound bearer token out of Coolify.

    This is the one of the three tokens this repository does not hold a copy
    of -- the Baserow adapter predates this repository's deploy-spec
    convention and was provisioned directly in Coolify. Reading it back
    through the Coolify API -- the same API every other credential-binding
    operation in this repository already uses -- avoids inventing a fourth
    place a copy of it could drift out of sync.
    """

    entries = driver.read_environment_entries(coolify_client, baserow_adapter_uuid)
    for entry in entries:
        if isinstance(entry, dict) and entry.get("key") == BASEROW_ADAPTER_ENV_KEY:
            value = entry.get("value")
            if isinstance(value, str) and value:
                driver.register_redaction(value)
                return value
    raise driver.Abort(
        f"{BASEROW_ADAPTER_ENV_KEY} is not set on the Baserow adapter application"
    )


def operate_bind_canary_credentials(
    client: N8nClient,
    workflow_definition: dict,
    coolify_client: "driver.Client",
    baserow_adapter_uuid: str,
    agent_runtime_token: str,
    drive_adapter_token: str,
) -> int:
    """Create the three real credentials the canary needs, then re-import it.

    Bootstrap only -- see the module docstring. Every token is registered for
    redaction the instant it is read and is never assembled into anything
    that gets printed; only credential ids (not secrets) appear in output.
    """

    driver.emit(f"--- bind-canary-credentials {client.base_url}")
    if not agent_runtime_token:
        raise driver.Abort("the agent-runtime bearer token was not supplied")
    if not drive_adapter_token:
        raise driver.Abort("the drive-adapter bearer token was not supplied")
    if not baserow_adapter_uuid:
        raise driver.Abort("no Baserow adapter Coolify application uuid was supplied")

    baserow_token = read_baserow_adapter_token(coolify_client, baserow_adapter_uuid)

    # A comma-separated allowlist is accepted by every one of these services;
    # any single member authenticates, so the first is sufficient here.
    real_ids = {
        "agent-runtime": create_header_credential(
            client,
            CREDENTIAL_NAMES["agent-runtime"],
            f"Bearer {agent_runtime_token.split(',')[0].strip()}",
        ),
        "baserow-adapter": create_header_credential(
            client, CREDENTIAL_NAMES["baserow-adapter"], f"Bearer {baserow_token}"
        ),
        "drive-adapter": create_header_credential(
            client,
            CREDENTIAL_NAMES["drive-adapter"],
            f"Bearer {drive_adapter_token.split(',')[0].strip()}",
        ),
    }
    for service, credential_id in real_ids.items():
        driver.emit(f"    created credential {service}: id={credential_id}")

    updated_definition = substitute_credential_ids(workflow_definition, real_ids)
    driver.emit("    re-importing the canary with the real credential ids bound")
    return operate_import_canary(client, updated_definition)


def operate_run_canary(client: N8nClient) -> int:
    """Execute the canary's manual trigger once, if the API supports it."""

    driver.emit(f"--- run-canary {client.base_url}")
    matches = [
        item for item in list_all_workflows(client) if item.get("name") == CANARY_NAME
    ]
    if len(matches) != 1:
        driver.emit(f"    found {len(matches)} workflows named {CANARY_NAME!r}, want exactly 1")
        driver.emit("RESULT run-canary failed reason=canary_not_found")
        return driver.EXIT_FAILED
    workflow_id = matches[0]["id"]
    driver.emit(f"    canary id={workflow_id}")

    status, parsed = client.request("POST", f"/workflows/{urllib.parse.quote(workflow_id)}/run")
    if status in (404, 405):
        # Confirmed against the live instance: it answers 405 ("POST method
        # not allowed"), not 404, for this path -- the route exists but does
        # not accept execution this way. Both mean the same thing here.
        driver.emit("")
        driver.emit(
            f"    this n8n instance's public API returned {status} for "
            "/workflows/{id}/run. A manual-trigger workflow cannot be executed "
            "through the public REST API on this version; it has to be run "
            "from the n8n UI's own 'Test workflow' button, by the owner."
        )
        driver.emit("RESULT run-canary failed reason=api_execute_unsupported")
        return driver.EXIT_FAILED
    if status not in (200, 201):
        driver.emit(f"    unexpected status {status}: {driver.api_message(parsed)}")
        driver.emit("RESULT run-canary failed reason=unexpected_status")
        return driver.EXIT_FAILED

    execution_id = parsed.get("executionId") if isinstance(parsed, dict) else None
    driver.emit(f"    execution id={execution_id}")
    driver.emit("")
    driver.emit(f"RESULT run-canary ok execution_id={execution_id}")
    return driver.EXIT_OK


def extract_redacted_result(execution_detail: dict) -> dict | None:
    """Pull the one node's output this operation is allowed to show.

    n8n's execution-detail shape nests a node's output at
    resultData.runData[nodeName][0].data.main[0][0].json -- read defensively,
    since every level is a place the shape could differ across versions, and
    "could not find it" must degrade to None, never to guessing at another
    node's data.
    """

    try:
        run_data = execution_detail["data"]["resultData"]["runData"]
        node_runs = run_data.get(REDACTED_RESULT_NODE)
        if not node_runs:
            return None
        main = node_runs[0]["data"]["main"]
        items = main[0]
        if not items:
            return None
        value = items[0].get("json")
        return value if isinstance(value, dict) else None
    except (KeyError, IndexError, TypeError):
        return None


def operate_canary_executions(client: N8nClient, limit: int) -> int:
    """List the canary's most recent executions and their redacted result."""

    driver.emit(f"--- canary-executions {client.base_url}")
    matches = [
        item for item in list_all_workflows(client) if item.get("name") == CANARY_NAME
    ]
    if len(matches) != 1:
        driver.emit(f"    found {len(matches)} workflows named {CANARY_NAME!r}, want exactly 1")
        driver.emit("RESULT canary-executions failed reason=canary_not_found")
        return driver.EXIT_FAILED
    workflow_id = matches[0]["id"]
    driver.emit(f"    canary id={workflow_id}")

    listing = call(client, "GET", "/executions", query={"workflowId": workflow_id, "limit": limit})
    if not isinstance(listing, dict):
        raise driver.Abort("GET /executions did not return an object")
    executions = [item for item in (listing.get("data") or []) if isinstance(item, dict)]
    driver.emit(f"    executions found: {len(executions)}")

    for summary in executions:
        execution_id = summary.get("id")
        detail = call(
            client,
            "GET",
            f"/executions/{urllib.parse.quote(str(execution_id))}",
            query={"includeData": "true"},
        )
        redacted = extract_redacted_result(detail) if isinstance(detail, dict) else None
        driver.emit(
            f"    execution id={execution_id} status={summary.get('status')} "
            f"startedAt={summary.get('startedAt')} stoppedAt={summary.get('stoppedAt')}"
        )
        if redacted is None:
            driver.emit(f"      {REDACTED_RESULT_NODE!r} output: not available")
        else:
            for key in sorted(redacted):
                driver.emit(f"      {key}: {redacted[key]}")

    driver.emit("")
    driver.emit(f"RESULT canary-executions ok count={len(executions)}")
    return driver.EXIT_OK


def parse_arguments(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=OPERATIONS)
    parser.add_argument(
        "--export-dir",
        default=None,
        help="directory to write export operation's JSON files into",
    )
    parser.add_argument(
        "--canary-file",
        default=None,
        help="local path to the corrected canary workflow JSON "
        "(import-canary and bind-canary-credentials only)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=5,
        help="max executions to list (canary-executions only)",
    )
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    arguments = parse_arguments(argv)
    try:
        base_url = os.environ.get(BASE_URL_VARIABLE, "")
        credential = os.environ.get(CREDENTIAL_VARIABLE, "")
        if not base_url:
            raise driver.Abort(f"{BASE_URL_VARIABLE} is not set; nothing was attempted")
        if not credential:
            raise driver.Abort(f"{CREDENTIAL_VARIABLE} is not set; nothing was attempted")
        driver.register_redaction(credential)
        client = N8nClient(base_url, credential)

        if arguments.operation == "status":
            return operate_status(client)
        if arguments.operation == "export":
            export_dir = Path(arguments.export_dir or "n8n-export")
            return operate_export(client, export_dir)
        if arguments.operation in ("import-canary", "bind-canary-credentials"):
            if not arguments.canary_file:
                raise driver.Abort(f"--canary-file is required for {arguments.operation}")
            workflow_definition = json.loads(
                Path(arguments.canary_file).read_text(encoding="utf-8")
            )
            if arguments.operation == "import-canary":
                return operate_import_canary(client, workflow_definition)

            coolify_credential = os.environ.get("COOLIFY_API_TOKEN", "")
            if not coolify_credential:
                raise driver.Abort("COOLIFY_API_TOKEN is not set")
            driver.register_redaction(coolify_credential)
            coolify_client = driver.Client(
                os.environ.get("COOLIFY_URL", ""), coolify_credential
            )
            return operate_bind_canary_credentials(
                client,
                workflow_definition,
                coolify_client,
                os.environ.get("BASEROW_ADAPTER_COOLIFY_APP_UUID", ""),
                os.environ.get("GOVERNED_AGENT_RUNTIME_HTTP_BEARER_TOKENS", ""),
                os.environ.get("DRIVE_SERVICE_BEARER_TOKENS", ""),
            )
        if arguments.operation == "canary-executions":
            return operate_canary_executions(client, arguments.limit)
        return operate_run_canary(client)
    except driver.Abort as abort:
        driver.emit(f"ABORT {abort}")
        return abort.code


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
