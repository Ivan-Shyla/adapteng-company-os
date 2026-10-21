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
  export     write the snapshot under --output-dir
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
OPERATIONS = ("preflight", "export")

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
        return operate_export(client, Path(arguments.output_dir), arguments.since)
    except driver.Abort as abort:
        driver.emit(f"ABORT {abort}")
        return abort.code


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
