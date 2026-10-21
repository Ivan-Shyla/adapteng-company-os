#!/usr/bin/env python3
"""Export a bounded, read-only snapshot of n8n Cloud for the content migration.

n8n Cloud (the legacy Marketing Machine and Lead Monitor workflows) is a
different authority from the self-hosted instance driven by n8n_deploy.py. This
tool talks to Cloud only, with its own base URL and key, and never to the
self-hosted instance.

It can only issue GET requests, and only for the paths in ``check_request``:

  * workflow list and each workflow definition
  * execution metadata for the two workflows whose runs have to be lined up with
    Drive file creation times (MM-20 and MM-21), always with includeData=false
  * the list of data tables and the rows of eight named tables

Anything else is refused before a request is made. Execution payloads are never
requested. Workflow definitions are written without pinned test data or static
data, with credential references reduced to id and name, and with secret-shaped
values masked. Masking is best effort and is counted in the manifest; it is not
a substitute for treating the output as confidential.

Operations:
  preflight  confirm the key works and that this is n8n Cloud, report which of the
             eight tables exist, and write nothing
  evidence   write nothing; print aggregate, non-sensitive facts about MM-20 and
             MM-21 (graph shape, run times, Publication_Radar status counts) and
             never any title, abstract, note, parameter value or payload. Safe
             for a public log.
  export     write the full snapshot under --output-dir. Refused unless the
             destination is declared private (EXPORT_DESTINATION_PRIVATE=true):
             the snapshot holds business data and must never become a build
             artifact of a public repository.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import coolify_deploy as driver  # noqa: E402
import n8n_deploy  # noqa: E402

ALLOWLIST_VERSION = "1"
API_PREFIX = "/api/v1"
BASE_URL_VARIABLE = "N8N_CLOUD_BASE_URL"
CREDENTIAL_VARIABLE = "N8N_CLOUD_API_CREDENTIAL"
OPERATIONS = ("preflight", "evidence", "export")
PRIVATE_DESTINATION_VARIABLE = "EXPORT_DESTINATION_PRIVATE"

# Execution metadata is requested for these two workflows only.
CORRELATED_WORKFLOWS = {
    "gJMEoOMUI0uS0D1Y": "MM-20 Publication Scout",
    "kB21JwdMDZt7nT5a": "MM-21 Publication Fetcher",
}
EXECUTION_FIELDS = (
    "id",
    "workflowId",
    "mode",
    "status",
    "finished",
    "startedAt",
    "stoppedAt",
    "retryOf",
    "waitTill",
)
EXPORT_TABLES = (
    "Raw_Media",
    "Content_Cases",
    "Photo_Content_Queue",
    "Publication_Radar",
    "Publication_Analysis",
    "Website_Content",
    "Website_Content_Sync",
    "LinkedIn_Drafts",
)
# The docs call the endpoint /datatables; other references say /data-tables. The
# export tries these two fixed paths in order and uses the first that answers.
DATA_TABLE_PATHS = ("/data-tables", "/datatables")
DEFAULT_SINCE = "2026-06-01T00:00:00Z"
PAGE_LIMIT = 100
MAX_PAGES = 200
MAX_ROWS_PER_TABLE = 20000

ID = r"[A-Za-z0-9_-]{4,64}"
TABLE_BASES = "|".join(re.escape(path) for path in DATA_TABLE_PATHS)
PATH_RULES = (
    (re.compile(r"^/workflows$"), {"limit", "cursor"}),
    (re.compile(rf"^/workflows/{ID}$"), set()),
    (re.compile(r"^/executions$"), {"includeData", "workflowId", "limit", "cursor"}),
    (re.compile(rf"^(?:{TABLE_BASES})$"), {"limit", "cursor"}),
    (re.compile(rf"^(?:{TABLE_BASES})/{ID}/rows$"), {"limit", "cursor"}),
)

SECRET_KEY = re.compile(
    r"(authorization|api[-_ ]?key|apikey|x-api|token|secret|password|passwd|bearer|"
    r"cookie|private[-_ ]?key)",
    re.IGNORECASE,
)
SECRET_SHAPED = re.compile(r"^(?:[A-Za-z0-9_\-.=+/]{16,})$")
SECRET_IN_TEXT = (
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{12,}"),
    re.compile(r"eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
)
REDACTED = "[redacted]"
DROPPED_WORKFLOW_KEYS = ("pinData", "staticData")


class Cloud:
    """A GET-only client for the n8n Cloud public API."""

    def __init__(self, base_url: str, credential: str, timeout: int = 30) -> None:
        self.base_url = check_base_url(base_url)
        self._credential = credential
        self._timeout = timeout

    def get(self, path: str, query: dict | None = None) -> tuple[int, object]:
        check_request(path, query)
        url = f"{self.base_url}{API_PREFIX}{path}"
        if query:
            url = f"{url}?{urllib.parse.urlencode(query)}"
        message = urllib.request.Request(url, method="GET")
        message.add_header("X-N8N-API-KEY", self._credential)
        message.add_header("Accept", "application/json")
        message.add_header("User-Agent", "adapteng-n8n-cloud-export")
        try:
            with urllib.request.urlopen(message, timeout=self._timeout) as response:
                raw = response.read().decode("utf-8", "replace")
                return response.status, (json.loads(raw) if raw.strip() else None)
        except urllib.error.HTTPError as error:
            raw = error.read().decode("utf-8", "replace")
            try:
                parsed = json.loads(raw) if raw.strip() else None
            except json.JSONDecodeError:
                parsed = {"message": raw[:200]}
            return error.code, parsed
        except json.JSONDecodeError as error:
            raise driver.Abort(f"the API returned a body that is not JSON: {error}") from error
        except OSError as error:
            raise driver.Unreachable(
                f"n8n Cloud is unreachable: {error.__class__.__name__}"
            ) from error


def check_base_url(base_url: str) -> str:
    """Accept only an https n8n Cloud address; never the self-hosted instance."""

    parsed = urllib.parse.urlsplit(base_url.strip())
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or not host:
        raise driver.Abort(f"{BASE_URL_VARIABLE} must be an https address", driver.EXIT_MISCONFIGURED)
    if host == "n8n.adapteng.com" or not host.endswith(".n8n.cloud"):
        raise driver.Abort(
            f"{BASE_URL_VARIABLE} is not an n8n Cloud address; refusing to run",
            driver.EXIT_MISCONFIGURED,
        )
    if parsed.path not in ("", "/") or parsed.query:
        raise driver.Abort(
            f"{BASE_URL_VARIABLE} must be the bare origin, without a path or query",
            driver.EXIT_MISCONFIGURED,
        )
    return f"https://{host}"


def check_request(path: str, query: dict | None = None) -> None:
    """Refuse every request that is not on the allowlist."""

    query = dict(query or {})
    for pattern, allowed in PATH_RULES:
        if not pattern.match(path):
            continue
        extra = set(query) - allowed
        if extra:
            raise driver.Abort(f"query parameter not allowed for {path}: {sorted(extra)}")
        if path == "/executions":
            if str(query.get("includeData")).lower() != "false":
                raise driver.Abort("executions must be requested with includeData=false")
            if query.get("workflowId") not in CORRELATED_WORKFLOWS:
                raise driver.Abort("executions may only be read for MM-20 and MM-21")
        return
    raise driver.Abort(f"path is not on the export allowlist: {path}")


def call(client: Cloud, path: str, query: dict | None = None) -> object:
    status, parsed = client.get(path, query)
    if status == 401:
        raise driver.Abort(
            "HTTP 401: the Cloud API key was rejected (expired, deleted or wrong account)"
        )
    if status != 200:
        raise driver.Abort(f"GET {path} returned HTTP {status}: {driver.api_message(parsed)}")
    return parsed


def paged(client: Cloud, path: str, query: dict | None = None):
    """Yield every item of a cursor-paged list, with a hard page bound."""

    cursor = None
    for _ in range(MAX_PAGES):
        page_query = {"limit": PAGE_LIMIT, **(query or {})}
        if cursor:
            page_query["cursor"] = cursor
        page = call(client, path, page_query)
        if not isinstance(page, dict) or not isinstance(page.get("data"), list):
            raise driver.Abort(f"GET {path} did not return a data list")
        yield from (item for item in page["data"] if isinstance(item, dict))
        cursor = page.get("nextCursor")
        if not cursor:
            return
    raise driver.Abort(f"GET {path} did not finish within {MAX_PAGES} pages")


def confirm_cloud(workflows: list[dict]) -> None:
    ids = {item.get("id") for item in workflows}
    if ids & n8n_deploy.KNOWN_SELF_HOSTED_WORKFLOW_IDS:
        raise driver.Abort(
            "self-hosted workflows are present; this is not n8n Cloud. Nothing was exported.",
            driver.EXIT_MISCONFIGURED,
        )
    if n8n_deploy.KNOWN_N8N_CLOUD_WORKFLOW_ID not in ids:
        raise driver.Abort(
            "the known Cloud workflow MM-08 is absent, so this cannot be confirmed as the "
            "legacy Cloud account. Nothing was exported.",
            driver.EXIT_MISCONFIGURED,
        )


def resolve_tables(client: Cloud) -> tuple[str, dict[str, str], list[str]]:
    """Return the data-table path that answered, name->id for the eight, and missing names."""

    last_error = None
    for base in DATA_TABLE_PATHS:
        status, parsed = client.get(base, {"limit": PAGE_LIMIT})
        if status == 401:
            raise driver.Abort("HTTP 401: the Cloud API key was rejected")
        if status == 200 and isinstance(parsed, dict) and isinstance(parsed.get("data"), list):
            found: dict[str, list[str]] = {}
            for item in paged(client, base):
                name = item.get("name")
                if name in EXPORT_TABLES and isinstance(item.get("id"), str):
                    found.setdefault(name, []).append(item["id"])
            duplicates = sorted(name for name, ids in found.items() if len(ids) > 1)
            if duplicates:
                raise driver.Abort(f"more than one data table is named {duplicates}; stopping")
            mapping = {name: ids[0] for name, ids in found.items()}
            return base, mapping, [name for name in EXPORT_TABLES if name not in mapping]
        last_error = status
    raise driver.Abort(f"neither data table path answered with a list (last HTTP {last_error})")


def mask_text(value: str) -> tuple[str, int]:
    count = 0
    for pattern in SECRET_IN_TEXT:
        value, hits = pattern.subn(REDACTED, value)
        count += hits
    return value, count


def looks_secret(value: str) -> bool:
    if value.startswith("=") or not value:
        return False
    if re.match(r"(?i)^(bearer|basic)\s+\S{8,}", value):
        return True
    return bool(
        SECRET_SHAPED.match(value) and re.search(r"[A-Za-z]", value) and re.search(r"\d", value)
    )


def scrub(value: object, key: str = "") -> tuple[object, int]:
    """Mask secret-shaped values in nested JSON; return the value and a count."""

    count = 0
    if isinstance(value, dict):
        out = {}
        name = value.get("name")
        pair = isinstance(name, str) and "value" in value and bool(SECRET_KEY.search(name))
        for child_key, child in value.items():
            if pair and child_key == "value" and isinstance(child, str) and looks_secret(child):
                out[child_key] = REDACTED
                count += 1
                continue
            cleaned, hits = scrub(child, child_key)
            out[child_key] = cleaned
            count += hits
        return out, count
    if isinstance(value, list):
        out_list = []
        for child in value:
            cleaned, hits = scrub(child, key)
            out_list.append(cleaned)
            count += hits
        return out_list, count
    if isinstance(value, str):
        if SECRET_KEY.search(key) and looks_secret(value):
            return REDACTED, 1
        return mask_text(value)
    return value, 0


def clean_workflow(definition: dict) -> tuple[dict, int]:
    """Drop pinned and static data, keep credential id and name only, mask secrets."""

    cleaned = {k: v for k, v in definition.items() if k not in DROPPED_WORKFLOW_KEYS}
    nodes = []
    for node in cleaned.get("nodes", []) or []:
        node = dict(node)
        references = node.get("credentials")
        if isinstance(references, dict):
            node["credentials"] = {
                kind: {"id": ref.get("id"), "name": ref.get("name")}
                for kind, ref in references.items()
                if isinstance(ref, dict)
            }
        nodes.append(node)
    cleaned["nodes"] = nodes
    scrubbed, count = scrub(cleaned)
    return scrubbed, count


def execution_metadata(item: dict) -> dict:
    return {field: item.get(field) for field in EXECUTION_FIELDS if field in item}


def executions_since(client: Cloud, workflow_id: str, since: str) -> list[dict]:
    """Newest-first metadata for one workflow, stopping once older than ``since``."""

    cutoff = parse_time(since)
    kept: list[dict] = []
    for item in paged(
        client, "/executions", {"includeData": "false", "workflowId": workflow_id}
    ):
        started = item.get("startedAt")
        if isinstance(started, str) and parse_time(started) < cutoff:
            break
        kept.append(execution_metadata(item))
    return kept


def parse_time(value: str) -> datetime:
    moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def write_json(path: Path, payload: object, files: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False)
    path.write_text(text, encoding="utf-8")
    files[path.as_posix()] = hashlib.sha256(text.encode("utf-8")).hexdigest()


def operate_preflight(client: Cloud) -> int:
    driver.emit(f"--- preflight {client.base_url}")
    workflows = list(paged(client, "/workflows"))
    confirm_cloud(workflows)
    base, mapping, missing = resolve_tables(client)
    driver.emit(f"    workflows: {len(workflows)}")
    driver.emit(f"    data table path: {base}")
    driver.emit(f"    export tables present: {sorted(mapping)}")
    driver.emit(f"    export tables missing: {missing or 'none'}")
    outcome = "ok" if not missing else "partial"
    driver.emit(f"RESULT preflight {outcome} workflows={len(workflows)} missing_tables={len(missing)}")
    return driver.EXIT_OK if not missing else driver.EXIT_FAILED


RADAR_TABLE = "Publication_Radar"
SAFE_NAME = re.compile(r"^[A-Za-z0-9_.-]{3,120}$")
SAFE_STATUS = re.compile(r"^[a-z_]{3,40}$")
SAFE_LABEL = re.compile(r"^[^\n]{1,80}$")
SAFE_TIME = re.compile(r"^[0-9T:.+Z-]{10,40}$")
SAFE_WORD = re.compile(r"^[A-Za-z]{2,30}$")
SCHEDULE_KEYS = (
    "field", "daysInterval", "weeksInterval", "monthsInterval", "hoursInterval",
    "minutesInterval", "triggerAtDay", "triggerAtHour", "triggerAtMinute",
    "triggerAtDayOfMonth",
)
MAX_LISTED_EXECUTIONS = 60
MAX_LISTED_RADAR_ROWS = 60


def safe_text(value: object, pattern: re.Pattern = SAFE_NAME) -> str:
    text = str(value) if value is not None else ""
    return text if pattern.match(text) else "[not printed]"


def column_names(node_parameters: dict) -> list[tuple[str, bool]]:
    columns = node_parameters.get("columns")
    mapping = columns.get("value") if isinstance(columns, dict) else None
    if not isinstance(mapping, dict):
        return []
    return [
        (safe_text(name), isinstance(value, str) and value.startswith("="))
        for name, value in mapping.items()
    ]


def table_label(node_parameters: dict) -> str:
    ref = node_parameters.get("dataTableId")
    if isinstance(ref, dict):
        return safe_text(ref.get("cachedResultName") or ref.get("value"))
    return "[not printed]"


def is_root_folder(node_parameters: dict) -> bool:
    ref = node_parameters.get("folderId")
    value = ref.get("value") if isinstance(ref, dict) else ref
    return value == "root"


def workflow_shape(definition: dict) -> list[str]:
    """Describe a workflow's structure without printing any parameter value.

    Only node names and types, schedule numbers, the named data table, column
    names with whether each is mapped from the incoming item, whether a Drive
    node writes to the root folder, and the ids of workflows it calls.
    """

    lines = [
        f"  name: {safe_text(definition.get('name'), SAFE_LABEL)}  active: {definition.get('active')}"
    ]
    for node in definition.get("nodes", []) or []:
        kind = str(node.get("type", "")).split(".")[-1]
        params = node.get("parameters") if isinstance(node.get("parameters"), dict) else {}
        flag = " (disabled)" if node.get("disabled") else ""
        detail = ""
        if kind == "scheduleTrigger":
            rules = (params.get("rule") or {}).get("interval") or []
            kept = [
                {k: r.get(k) for k in SCHEDULE_KEYS if k in r}
                for r in rules
                if isinstance(r, dict)
            ]
            detail = " schedule=" + json.dumps(kept, sort_keys=True)
        elif kind == "executeWorkflow":
            target = params.get("workflowId")
            target = target.get("value") if isinstance(target, dict) else target
            detail = " calls=" + safe_text(target)
        elif kind == "dataTable":
            cols = ", ".join(
                f"{name}{'(from item)' if mapped else ''}" for name, mapped in column_names(params)
            )
            detail = (
                f" op={safe_text(params.get('operation'), SAFE_STATUS)}"
                f" table={table_label(params)} columns=[{cols}]"
            )
        elif kind == "googleDrive":
            detail = (
                f" op={safe_text(params.get('operation'), SAFE_WORD)}"
                f" folder_is_root={is_root_folder(params)}"
            )
        lines.append(f"  node: {safe_text(node.get('name'), SAFE_LABEL)} [{kind}]{flag}{detail}")
    connections = definition.get("connections") or {}
    for source, outputs in sorted(connections.items()):
        for group in (outputs or {}).get("main", []) or []:
            for edge in group or []:
                lines.append(f"  edge: {safe_text(source, SAFE_LABEL)} -> {safe_text(edge.get('node'), SAFE_LABEL)}")
    return lines


def operate_evidence(client: Cloud, since: str) -> int:
    driver.emit(f"--- evidence {client.base_url}")
    workflows = list(paged(client, "/workflows"))
    confirm_cloud(workflows)
    driver.emit(f"workflows: {len(workflows)} active: {sum(1 for w in workflows if w.get('active'))}")

    for workflow_id, label in CORRELATED_WORKFLOWS.items():
        definition = call(client, f"/workflows/{urllib.parse.quote(workflow_id)}")
        driver.emit(f"== {label} ({workflow_id})")
        for line in workflow_shape(definition if isinstance(definition, dict) else {}):
            driver.emit(line)
        runs = executions_since(client, workflow_id, since)
        driver.emit(f"  executions since {since}: {len(runs)}")
        by_slot: dict[str, int] = {}
        for run in runs:
            started = run.get("startedAt")
            if isinstance(started, str):
                moment = parse_time(started)
                slot = f"{moment.strftime('%a')} {moment.hour:02d}:00Z"
                by_slot[slot] = by_slot.get(slot, 0) + 1
        driver.emit("  start slots (UTC): " + json.dumps(by_slot, sort_keys=True))
        for run in runs[:MAX_LISTED_EXECUTIONS]:
            driver.emit(
                f"  run {safe_text(run.get('id'))} mode={safe_text(run.get('mode'), SAFE_STATUS)} "
                f"status={safe_text(run.get('status'), SAFE_STATUS)} "
                f"start={safe_text(run.get('startedAt'), SAFE_TIME)} "
                f"stop={safe_text(run.get('stoppedAt'), SAFE_TIME)}"
            )

    base, mapping, _missing = resolve_tables(client)
    table_id = mapping.get(RADAR_TABLE)
    if table_id is None:
        driver.emit(f"{RADAR_TABLE}: table not found")
        driver.emit("RESULT evidence partial radar=missing")
        return driver.EXIT_FAILED
    rows = list(paged(client, f"{base}/{table_id}/rows"))
    statuses: dict[str, int] = {}
    keys: dict[str, int] = {}
    listing = []
    for row in rows:
        status = safe_text(row.get("status"), SAFE_STATUS)
        statuses[status] = statuses.get(status, 0) + 1
        key = row.get("dedupe_key")
        if isinstance(key, str):
            keys[key] = keys.get(key, 0) + 1
        listing.append(
            (
                safe_text(row.get("pub_id")),
                status,
                not str(row.get("drive_file_id") or "").strip(),
                not str(row.get("analysis_id") or "").strip(),
                safe_text(row.get("discovered_at"), SAFE_TIME),
            )
        )
    driver.emit(f"== {RADAR_TABLE}: rows={len(rows)}")
    driver.emit("  by status: " + json.dumps(statuses, sort_keys=True))
    driver.emit(f"  dedupe_key values repeated: {sum(1 for n in keys.values() if n > 1)}")
    reset = [
        item
        for item in listing
        if item[2] and item[3] and item[1] in ("approved_for_fetch", "approved_for_analysis")
    ]
    driver.emit(f"  rows with empty drive_file_id and analysis_id in an approved status: {len(reset)}")
    for pub_id, status, no_drive, no_analysis, found in listing[:MAX_LISTED_RADAR_ROWS]:
        driver.emit(
            f"  row {pub_id} status={status} drive_file_id_empty={no_drive} "
            f"analysis_id_empty={no_analysis} discovered={found}"
        )
    driver.emit(f"RESULT evidence ok rows={len(rows)}")
    return driver.EXIT_OK


def operate_export(client: Cloud, output_dir: Path, since: str) -> int:
    driver.emit(f"--- export {client.base_url}")
    workflows = list(paged(client, "/workflows"))
    confirm_cloud(workflows)
    base, mapping, missing = resolve_tables(client)

    files: dict[str, str] = {}
    redactions: dict[str, int] = {}
    for item in workflows:
        workflow_id = item.get("id")
        if not isinstance(workflow_id, str):
            continue
        definition = call(client, f"/workflows/{urllib.parse.quote(workflow_id)}")
        if not isinstance(definition, dict):
            raise driver.Abort(f"workflow {workflow_id} did not return an object")
        cleaned, hits = clean_workflow(definition)
        if hits:
            redactions[workflow_id] = hits
        write_json(output_dir / "workflows" / f"{workflow_id}.json", cleaned, files)
    driver.emit(f"    workflow definitions written: {len(workflows)}")

    execution_counts = {}
    for workflow_id, label in CORRELATED_WORKFLOWS.items():
        rows = executions_since(client, workflow_id, since)
        execution_counts[workflow_id] = len(rows)
        write_json(output_dir / "executions" / f"{workflow_id}.json", rows, files)
        driver.emit(f"    executions since {since} for {label}: {len(rows)}")

    table_counts = {}
    for name in EXPORT_TABLES:
        table_id = mapping.get(name)
        if table_id is None:
            continue
        rows = []
        for row in paged(client, f"{base}/{table_id}/rows"):
            cleaned_row, hits = scrub(row)
            if hits:
                redactions[f"table:{name}"] = redactions.get(f"table:{name}", 0) + hits
            rows.append(cleaned_row)
            if len(rows) > MAX_ROWS_PER_TABLE:
                raise driver.Abort(f"table {name} exceeds {MAX_ROWS_PER_TABLE} rows; stopping")
        table_counts[name] = len(rows)
        write_json(output_dir / "data-tables" / f"{name}.json", {"name": name, "rows": rows}, files)
        driver.emit(f"    table {name}: {len(rows)} rows")

    manifest = {
        "allowlist_version": ALLOWLIST_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "host": urllib.parse.urlsplit(client.base_url).hostname,
        "executions_since": since,
        "workflow_count": len(workflows),
        "execution_counts": execution_counts,
        "table_row_counts": table_counts,
        "tables_missing": missing,
        "redaction_counts": redactions,
        "sha256": files,
    }
    write_json(output_dir / "_manifest.json", manifest, {})
    outcome = "ok" if not missing else "partial"
    driver.emit(
        f"RESULT export {outcome} workflows={len(workflows)} tables={len(table_counts)} "
        f"missing_tables={len(missing)} redactions={sum(redactions.values())}"
    )
    return driver.EXIT_OK if not missing else driver.EXIT_FAILED


def parse_arguments(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=OPERATIONS)
    parser.add_argument("--output-dir", default="n8n-cloud-export")
    parser.add_argument("--since", default=DEFAULT_SINCE)
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    arguments = parse_arguments(argv)
    try:
        base_url = os.environ.get(BASE_URL_VARIABLE, "")
        credential = os.environ.get(CREDENTIAL_VARIABLE, "")
        if not base_url:
            raise driver.Abort(f"{BASE_URL_VARIABLE} is not set; nothing was attempted", driver.EXIT_MISCONFIGURED)
        if not credential:
            raise driver.Abort(f"{CREDENTIAL_VARIABLE} is not set; nothing was attempted", driver.EXIT_MISCONFIGURED)
        driver.register_redaction(credential)
        client = Cloud(base_url, credential)
        if arguments.operation == "preflight":
            return operate_preflight(client)
        if arguments.operation == "evidence":
            return operate_evidence(client, arguments.since)
        if os.environ.get(PRIVATE_DESTINATION_VARIABLE) != "true":
            raise driver.Abort(
                "export writes business data and is refused unless "
                f"{PRIVATE_DESTINATION_VARIABLE}=true; it must not become a public build artifact",
                driver.EXIT_MISCONFIGURED,
            )
        return operate_export(client, Path(arguments.output_dir), arguments.since)
    except driver.Abort as abort:
        driver.emit(f"ABORT {abort}")
        return abort.code


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
