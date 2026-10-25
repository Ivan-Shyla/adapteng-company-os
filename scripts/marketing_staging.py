#!/usr/bin/env python3
"""Provision the isolated Marketing staging schema, role and n8n credential.

One reviewed operation, not a SQL console. The operation is chosen from a fixed
list; every SQL statement and every remote command is a constant in this file or
a migration whose commit and sha256 are pinned below. Nothing can be passed in
at dispatch time except the operation name and a confirmation phrase.

What it will create, and nothing else:
  * schema   marketing_staging        (in the existing adapteng_ops database)
  * role     marketing_staging_app    (LOGIN only; no superuser, no createdb,
                                       no createrole, no inheritance, 5 connections)
  * the tables of migrations 009 and 010, inside that schema only
  * one n8n credential and six inactive workflows bound to it

Why it cannot touch production data
  * the migrations run inside one transaction with search_path pinned to the new
    schema, and the same transaction compares the object count of every other
    schema before and after and aborts on any difference;
  * the same transaction proves the role has no privilege on any relation or
    sequence outside its schema, cannot create in any other schema, and cannot
    execute any SECURITY DEFINER function outside it; any finding aborts the
    transaction, so nothing is committed;
  * the migration text is fetched at a pinned commit, checked against a pinned
    sha256, scanned for statements that could leave the schema, and stripped of
    its own BEGIN and COMMIT so the outer transaction stays in control.

Why the password is safe
  * it is generated here (secrets module), registered for log redaction and
    announced to GitHub as a masked value before it exists anywhere else;
  * it reaches the database on stdin of a docker exec and is stored by the server
    as a SCRAM verifier; it reaches n8n in one HTTPS request body;
  * it is never written to a file, a repository, a command line or a step output.

Transport. The remote side is the same SSH channel as prod_ssh.py. On 2026-09-07
and 2026-09-14 that channel failed from GitHub's runners with "Connection timed
out" on TCP port 22, and the same probe fails from the owner's machine; see
runbooks/marketing-staging-provision.md for the diagnosis and the owner-run
fallback (`owner-sql`), which emits the identical transaction for the Coolify
database terminal.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import secrets
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import coolify_deploy as driver  # noqa: E402
import n8n_deploy  # noqa: E402
import prod_ssh  # noqa: E402

OPERATIONS = ("preflight", "provision-database", "configure-n8n", "import-workflows", "verify")
WRITE_OPERATIONS = frozenset({"provision-database", "configure-n8n", "import-workflows"})
CONFIRMATION = {
    "provision-database": "PROVISION marketing_staging",
    "configure-n8n": "CONFIGURE n8n marketing_staging",
    "import-workflows": "IMPORT workflows marketing_staging",
}
# n8n credential ids are short alphanumeric strings; anything else is refused.
CREDENTIAL_ID = re.compile(r"^[A-Za-z0-9]{8,32}$")

SCHEMA = "marketing_staging"
ROLE = "marketing_staging_app"
DATABASE = "adapteng_ops"
# The Coolify resource uuid is the container name and the internal DNS name
# (registry/environments.yaml: internal_address pgdgbwzsuuxhw55g8v5opjgn:5432/adapteng_ops).
DB_CONTAINER = "pgdgbwzsuuxhw55g8v5opjgn"
DB_PORT = 5432
CREDENTIAL_NAME = "ADAPTENG_MARKETING_STAGING_POSTGRES"
BACKUP_MAX_AGE_HOURS = 36
ROLE_CONNECTION_LIMIT = 5
EXPECTED_TABLES = 7
EXPECTED_VIEWS = 1

PLATFORM_REPOSITORY = "Ivan-Shyla/adapteng-automation-platform"
PIN_COMMIT = "fce7fd87e250b9e2be185d6bdffffa155dc9e2f9"  # head of PR #143 when reviewed
STAGING_DIR = "n8n/exports/manual/marketing-staging-20261025"
MIGRATION_PINS = {
    "database/migrations/009_marketing_source_package_reservation.sql": "bf32637fee17c361eb7d7177ef5320f50fcb81ff591db6cd4adcb6105235ad3b",
    "database/migrations/010_marketing_pipeline_state.sql": "99b546a7118c8df7c5e59dec10b9e4057f090b0df19d54a33b4bde409a8832fa",
}
WORKFLOW_PINS = {
    f"{STAGING_DIR}/stg-mm-20-discover.json": ("STG MM-20 Discover (synthetic publication)", "92f648fbf0809578acf20a2f06201bbdfa216e00145588141def59c9aa40f7c5"),
    f"{STAGING_DIR}/stg-mm-20a-approve.json": ("STG MM-20A Approve Publication (human)", "7f66c78d2df2605dd617c147948d8d04d0cf3856633bc4271412d0a995a32597"),
    f"{STAGING_DIR}/stg-mm-21-fetch-to-drive.json": ("STG MM-21 Fetch to Corporate Drive", "f9532cfd47eb27d7a357424d55ed38fae0b82312bb58813abb2abebdcacd9ecc"),
    f"{STAGING_DIR}/stg-mm-22-draft-article.json": ("STG MM-22 Draft Article (governed AI gateway)", "1098bd8afffadc011a6d49069b7403b0cc366d0614ca1f8f085b06f6f8e42999"),
    f"{STAGING_DIR}/stg-mm-23-drafts.json": ("STG MM-23 Website and LinkedIn Drafts", "b2f842842ea3995e8281faf283c4b8d5b0f2a8d1d005da729393f9dbfa6328b4"),
    f"{STAGING_DIR}/stg-mm-24-wordpress-draft.json": ("STG MM-24 WordPress Draft (staging site only)", "f5cbd891d84f6fb40275395e1be1d37a339a7fb5905e473a459cb3beebef359c"),
}

# Statements a migration may not contain, because each could leave the schema,
# change who may do what, or remove something that exists. Comment lines are
# removed before the scan.
FORBIDDEN_IN_MIGRATION = (
    r"\bsearch_path\b", r"\bSET\s+ROLE\b", r"\bSET\s+SESSION\b", r"\bRESET\s+ROLE\b",
    r"\bCREATE\s+SCHEMA\b", r"\bDROP\s+SCHEMA\b", r"\bDROP\s+TABLE\b", r"\bDROP\s+DATABASE\b",
    r"\bDROP\s+ROLE\b", r"\bCREATE\s+ROLE\b", r"\bALTER\s+ROLE\b", r"\bALTER\s+SYSTEM\b",
    r"\bCREATE\s+EXTENSION\b", r"\bGRANT\b", r"\bCOPY\b", r"\bTRUNCATE\b",
    r"\bpublic\s*\.", r"\badapteng_ops\s*\.", r"\bpg_catalog\s*\.\s*set_config\b", r"^\s*\\",
)

PASSWORD_ALPHABET = re.compile(r"^[A-Za-z0-9_-]{32,64}$")
CONTAINER_NAME = re.compile(r"^[a-z0-9]{10,40}$")


class Refused(driver.Abort):
    """A gate said no. The message says which one and what to do about it."""


def emit(line: str = "") -> None:
    driver.emit(line)


# ---------- pinned source files ----------


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def fetch_pinned(path: str) -> bytes:
    """Read one file at the pinned commit of the private platform repository.

    Uses the existing read-only token when the runner provides it and falls back
    to an authenticated `gh` on the owner's machine. The result is never trusted
    until its sha256 has been compared with the pin.
    """

    url = f"https://api.github.com/repos/{PLATFORM_REPOSITORY}/contents/{urllib.parse.quote(path)}?ref={PIN_COMMIT}"
    read_access = os.environ.get("GITHUB_READ_CREDENTIAL", "").strip()
    if read_access:
        driver.register_redaction(read_access)
        request = urllib.request.Request(url, headers={
            "Accept": "application/vnd.github.raw+json", "Authorization": f"Bearer {read_access}",
            "User-Agent": "adapteng-marketing-staging", "X-GitHub-Api-Version": "2022-11-28",
        })
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return response.read()
        except urllib.error.HTTPError as error:
            raise Refused(f"cannot read {path} at the pinned commit: HTTP {error.code}") from error
        except OSError as error:
            raise Refused(f"cannot reach GitHub: {error.__class__.__name__}") from error
    completed = subprocess.run(  # noqa: S603 - fixed argv, no shell
        ["gh", "api", "-H", "Accept: application/vnd.github.raw+json", f"repos/{PLATFORM_REPOSITORY}/contents/{path}?ref={PIN_COMMIT}"],
        capture_output=True, timeout=60,
    )
    if completed.returncode != 0:
        raise Refused(f"cannot read {path} at the pinned commit (no GITHUB_READ_CREDENTIAL and gh failed)")
    return completed.stdout


def load_pinned(fetch=fetch_pinned) -> tuple[dict[str, str], dict[str, dict]]:
    """Return {migration path: sql text} and {workflow path: definition}, all hash-checked."""

    migrations: dict[str, str] = {}
    for path, expected in MIGRATION_PINS.items():
        data = fetch(path)
        if sha256_hex(data) != expected:
            raise Refused(f"{path} does not match its pinned sha256; refusing to use it")
        if b"\r" in data:
            raise Refused(f"{path} contains carriage returns; refusing to use it")
        migrations[path] = data.decode("utf-8")
    workflows: dict[str, dict] = {}
    for path, (name, expected) in WORKFLOW_PINS.items():
        data = fetch(path)
        if sha256_hex(data) != expected:
            raise Refused(f"{path} does not match its pinned sha256; refusing to use it")
        definition = json.loads(data.decode("utf-8"))
        if definition.get("name") != name or definition.get("active") is not False:
            raise Refused(f"{path} is not the expected inactive workflow {name!r}")
        workflows[path] = definition
    return migrations, workflows


def migration_body(path: str, text: str) -> str:
    """Return the migration with its own transaction wrapper removed, after a scan."""

    code = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("--"))
    for pattern in FORBIDDEN_IN_MIGRATION:
        found = re.search(pattern, code, re.IGNORECASE | re.MULTILINE)
        if found:
            raise Refused(f"{path} contains a statement this operation does not allow: {found.group(0).strip()!r}")
    lines = text.splitlines()
    begin = [i for i, line in enumerate(lines) if line.strip() == "BEGIN;"]
    commit = [i for i, line in enumerate(lines) if line.strip() == "COMMIT;"]
    if len(begin) != 1 or len(commit) != 1 or begin[0] > commit[0]:
        raise Refused(f"{path} must contain exactly one BEGIN; before exactly one COMMIT;")
    return "\n".join(lines[begin[0] + 1 : commit[0]]).strip() + "\n"


# ---------- SQL: all constants ----------

_OBJECT_COUNTS = f"""
SELECT n.nspname::text AS s, count(*)::int AS n
FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname !~ '^pg_' AND n.nspname <> 'information_schema' AND n.nspname <> '{SCHEMA}'
GROUP BY 1
UNION ALL
SELECT 'fn:' || n.nspname::text, count(*)::int
FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
WHERE n.nspname !~ '^pg_' AND n.nspname <> 'information_schema' AND n.nspname <> '{SCHEMA}'
GROUP BY 1
UNION ALL
SELECT 'schemas', count(*)::int FROM pg_namespace
WHERE nspname !~ '^pg_' AND nspname <> 'information_schema' AND nspname <> '{SCHEMA}'
"""

IDENTITY_SQL = """
SELECT 'database|' || current_database();
SELECT 'server_version_num|' || current_setting('server_version_num');
SELECT 'is_superuser|' || current_setting('is_superuser');
SELECT 'current_user|' || current_user;
"""

STATE_SQL = f"""
SELECT 'schema|' || CASE WHEN EXISTS (SELECT 1 FROM pg_namespace WHERE nspname = '{SCHEMA}') THEN 'present' ELSE 'absent' END;
SELECT 'schema_owner|' || coalesce((SELECT pg_get_userbyid(nspowner) FROM pg_namespace WHERE nspname = '{SCHEMA}'), '');
SELECT 'schema_tables|' || count(*) FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace WHERE n.nspname = '{SCHEMA}' AND c.relkind IN ('r', 'p');
SELECT 'schema_views|' || count(*) FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace WHERE n.nspname = '{SCHEMA}' AND c.relkind IN ('v', 'm');
SELECT 'role|' || CASE WHEN EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{ROLE}') THEN 'present' ELSE 'absent' END;
SELECT 'role_attrs_ok|' || coalesce((SELECT (NOT rolsuper AND NOT rolcreatedb AND NOT rolcreaterole AND NOT rolreplication AND NOT rolbypassrls AND rolcanlogin AND NOT rolinherit AND rolconnlimit = {ROLE_CONNECTION_LIMIT})::text FROM pg_roles WHERE rolname = '{ROLE}'), '');
SELECT 'role_memberships|' || count(*) FROM pg_auth_members m JOIN pg_roles r ON r.oid = m.member WHERE r.rolname = '{ROLE}';
SELECT 'role_has_password|' || coalesce((SELECT (rolpassword IS NOT NULL)::text FROM pg_authid WHERE rolname = '{ROLE}'), '');
SELECT 'role_search_path|' || coalesce((SELECT array_to_string(rolconfig, ';') FROM pg_roles WHERE rolname = '{ROLE}'), '');
SELECT 'objects_' || s || '|' || n FROM ({_OBJECT_COUNTS}) counts ORDER BY 1;
"""

PROVISION_HEAD = f"""\\set ON_ERROR_STOP on
BEGIN;
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '120s';
SET LOCAL search_path TO pg_catalog, pg_temp;
CREATE TEMP TABLE _marketing_before AS {_OBJECT_COUNTS};
CREATE SCHEMA {SCHEMA};
CREATE ROLE {ROLE} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS NOINHERIT CONNECTION LIMIT {ROLE_CONNECTION_LIMIT};
ALTER ROLE {ROLE} SET search_path = {SCHEMA}, pg_temp;
ALTER ROLE {ROLE} SET statement_timeout = '30s';
ALTER ROLE {ROLE} SET lock_timeout = '5s';
ALTER ROLE {ROLE} SET idle_in_transaction_session_timeout = '60s';
SET LOCAL search_path TO {SCHEMA}, pg_temp;
"""

PROVISION_TAIL = f"""
SET LOCAL search_path TO pg_catalog, pg_temp;
GRANT USAGE ON SCHEMA {SCHEMA} TO {ROLE};
GRANT SELECT, INSERT, UPDATE ON ALL TABLES IN SCHEMA {SCHEMA} TO {ROLE};
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA {SCHEMA} TO {ROLE};
REVOKE UPDATE ON {SCHEMA}.marketing_publication_approvals FROM {ROLE};
REVOKE INSERT, UPDATE ON {SCHEMA}.marketing_pipeline_config FROM {ROLE};
DO $isolation$
DECLARE
    finding record;
BEGIN
    FOR finding IN
        SELECT b.s, b.n AS before_count, coalesce(a.n, 0) AS after_count
        FROM _marketing_before b
        LEFT JOIN ({_OBJECT_COUNTS}) a ON a.s = b.s
        WHERE b.n IS DISTINCT FROM coalesce(a.n, 0)
    LOOP
        RAISE EXCEPTION 'isolation: % changed from % to %', finding.s, finding.before_count, finding.after_count;
    END LOOP;
    FOR finding IN
        SELECT n.nspname, c.relname
        FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname NOT IN ('{SCHEMA}', 'pg_catalog', 'information_schema') AND n.nspname !~ '^pg_'
          AND c.relkind IN ('r', 'p', 'v', 'm', 'f')
          AND has_table_privilege('{ROLE}', c.oid, 'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER')
    LOOP
        RAISE EXCEPTION 'isolation: the role can reach %.%', finding.nspname, finding.relname;
    END LOOP;
    FOR finding IN
        SELECT n.nspname, c.relname
        FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname NOT IN ('{SCHEMA}', 'pg_catalog', 'information_schema') AND n.nspname !~ '^pg_'
          AND CASE WHEN c.relkind = 'S' THEN has_sequence_privilege('{ROLE}', c.oid, 'USAGE,SELECT,UPDATE') ELSE false END
    LOOP
        RAISE EXCEPTION 'isolation: the role can use sequence %.%', finding.nspname, finding.relname;
    END LOOP;
    FOR finding IN
        SELECT nspname FROM pg_namespace
        WHERE nspname <> '{SCHEMA}' AND nspname !~ '^pg_' AND nspname <> 'information_schema'
          AND has_schema_privilege('{ROLE}', oid, 'CREATE')
    LOOP
        RAISE EXCEPTION 'isolation: the role can create objects in schema %', finding.nspname;
    END LOOP;
    FOR finding IN
        SELECT n.nspname, p.proname
        FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
        WHERE p.prosecdef AND n.nspname NOT IN ('{SCHEMA}', 'pg_catalog', 'information_schema')
          AND has_function_privilege('{ROLE}', p.oid, 'EXECUTE')
    LOOP
        RAISE EXCEPTION 'isolation: the role can execute the SECURITY DEFINER function %.%', finding.nspname, finding.proname;
    END LOOP;
    IF has_database_privilege('{ROLE}', current_database(), 'CREATE') THEN
        RAISE EXCEPTION 'isolation: the role can create schemas in this database';
    END IF;
    IF EXISTS (SELECT 1 FROM pg_auth_members m JOIN pg_roles r ON r.oid = m.member WHERE r.rolname = '{ROLE}') THEN
        RAISE EXCEPTION 'isolation: the role is a member of another role';
    END IF;
    IF (SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = '{SCHEMA}' AND c.relkind IN ('r', 'p')) <> {EXPECTED_TABLES} THEN
        RAISE EXCEPTION 'isolation: the schema does not hold exactly {EXPECTED_TABLES} tables';
    END IF;
END
$isolation$;
COMMIT;
"""

SET_PASSWORD_SQL = f"""\\set ON_ERROR_STOP on
\\set pw '{{pw}}'
BEGIN;
SET LOCAL password_encryption = 'scram-sha-256';
SELECT set_config('marketing_staging.pw', :'pw', true) AS _ \\gset
DO $setpw$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{ROLE}') THEN
        RAISE EXCEPTION 'the role does not exist';
    END IF;
    EXECUTE format('ALTER ROLE %I PASSWORD %L', '{ROLE}', current_setting('marketing_staging.pw'));
END
$setpw$;
COMMIT;
"""

VERIFY_SQL = f"""
SELECT 'outside_relations_reachable|' || count(*) FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname NOT IN ('{SCHEMA}', 'pg_catalog', 'information_schema') AND n.nspname !~ '^pg_'
  AND c.relkind IN ('r', 'p', 'v', 'm', 'f')
  AND has_table_privilege('{ROLE}', c.oid, 'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER');
SELECT 'outside_sequences_usable|' || count(*) FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname NOT IN ('{SCHEMA}', 'pg_catalog', 'information_schema') AND n.nspname !~ '^pg_'
  AND CASE WHEN c.relkind = 'S' THEN has_sequence_privilege('{ROLE}', c.oid, 'USAGE,SELECT,UPDATE') ELSE false END;
SELECT 'outside_schemas_creatable|' || count(*) FROM pg_namespace
WHERE nspname <> '{SCHEMA}' AND nspname !~ '^pg_' AND nspname <> 'information_schema' AND has_schema_privilege('{ROLE}', oid, 'CREATE');
SELECT 'outside_secdef_executable|' || count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
WHERE p.prosecdef AND n.nspname NOT IN ('{SCHEMA}', 'pg_catalog', 'information_schema') AND has_function_privilege('{ROLE}', p.oid, 'EXECUTE');
SELECT 'database_create|' || has_database_privilege('{ROLE}', current_database(), 'CREATE')::text;
SELECT 'inside_tables_writable|' || count(*) FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = '{SCHEMA}' AND c.relkind IN ('r', 'p') AND has_table_privilege('{ROLE}', c.oid, 'SELECT,INSERT,UPDATE');
SELECT 'inside_tables_deletable|' || count(*) FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = '{SCHEMA}' AND c.relkind IN ('r', 'p') AND has_table_privilege('{ROLE}', c.oid, 'DELETE,TRUNCATE');
SELECT 'approvals_updatable|' || has_table_privilege('{ROLE}', '{SCHEMA}.marketing_publication_approvals', 'UPDATE')::text;
SELECT 'config_writable|' || has_table_privilege('{ROLE}', '{SCHEMA}.marketing_pipeline_config', 'INSERT,UPDATE')::text;
"""


def provision_sql(migrations: dict[str, str]) -> str:
    """The whole provisioning transaction, assembled from constants and checked migrations."""

    bodies = []
    for path in MIGRATION_PINS:  # fixed order: 009 then 010
        bodies.append(f"-- {path}\n" + migration_body(path, migrations[path]))
    return PROVISION_HEAD + "\n".join(bodies) + PROVISION_TAIL


def owner_sql(migrations: dict[str, str]) -> str:
    """The same transaction for the Coolify database terminal, with a server-made password.

    The password is generated by the server, applied inside the transaction and
    printed once to the owner's own terminal. It never passes through GitHub.
    """

    set_server_password = (
        "\\set ON_ERROR_STOP on\n"
        "SELECT replace(gen_random_uuid()::text || gen_random_uuid()::text, '-', '') AS pw \\gset\n"
        "BEGIN;\nSET LOCAL password_encryption = 'scram-sha-256';\n"
        "SELECT set_config('marketing_staging.pw', :'pw', true) AS _ \\gset\n"
        "DO $setpw$\nBEGIN\n"
        f"    EXECUTE format('ALTER ROLE %I PASSWORD %L', '{ROLE}', current_setting('marketing_staging.pw'));\n"
        "END\n$setpw$;\nCOMMIT;\n"
        "\\echo\n\\echo The password for the n8n credential, shown once:\n\\echo :pw\n\\echo\n"
    )
    return provision_sql(migrations) + set_server_password


# ---------- remote access ----------

PSQL = "sh -c 'psql -v ON_ERROR_STOP=1 --no-psqlrc -q -At -F \"|\" -U \"$POSTGRES_USER\" -d \"$POSTGRES_DB\" -f -'"


def parse_pairs(output: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for line in output.splitlines():
        if "|" in line:
            key, _, value = line.partition("|")
            result[key.strip()] = value.strip()
    return result


def resolve_container(host) -> str:
    """The one running Postgres container named exactly like the Coolify resource."""

    completed = host.run('docker ps --format "{{.Names}}\t{{.Image}}"')
    if completed.returncode != 0:
        timed_out = "timed out" in (completed.stderr or "").lower()
        raise Refused(
            "the SSH channel failed"
            + (": TCP port 22 timed out, so the host is not reachable from here. See "
               "runbooks/marketing-staging-provision.md, section 'The SSH channel'." if timed_out else f" (rc={completed.returncode})")
        )
    rows = [line.split("\t") for line in completed.stdout.splitlines() if "\t" in line]
    matches = [name for name, image in rows if name == DB_CONTAINER and image.rsplit("/", 1)[-1].lower().startswith("postgres")]
    if len(matches) != 1:
        raise Refused(f"expected exactly one running Postgres container named {DB_CONTAINER}, found {len(matches)}")
    if not CONTAINER_NAME.fullmatch(matches[0]):
        raise Refused("the container name has an unexpected shape")
    return matches[0]


def psql(host, container: str, sql: str) -> subprocess.CompletedProcess:
    return host.run(f"docker exec -i {container} {PSQL}", stdin=sql, timeout=240)


def read_pairs(host, container: str, sql: str) -> dict[str, str]:
    completed = psql(host, container, sql)
    if completed.returncode != 0:
        raise Refused(f"a read-only query failed (rc={completed.returncode})")
    return parse_pairs(completed.stdout)


# ---------- gates ----------


def classify(state: dict[str, str]) -> str:
    """absent | provisioned-no-password | exact | drifted. Anything else is not touched."""

    schema, role = state.get("schema"), state.get("role")
    if schema == "absent" and role == "absent":
        return "absent"
    if schema != "present" or role != "present":
        return "drifted"
    ok = (
        state.get("schema_tables") == str(EXPECTED_TABLES)
        and state.get("schema_views") == str(EXPECTED_VIEWS)
        and state.get("role_attrs_ok") == "true"
        and state.get("role_memberships") == "0"
        and SCHEMA in state.get("role_search_path", "")
    )
    if not ok:
        return "drifted"
    return "exact" if state.get("role_has_password") == "true" else "provisioned-no-password"


def identity_gate(identity: dict[str, str]) -> None:
    if identity.get("database") != DATABASE:
        raise Refused(f"connected to database {identity.get('database')!r}, expected {DATABASE!r}")
    if int(identity.get("server_version_num", "0")) < 160000:
        raise Refused("PostgreSQL 16 or newer is required")
    if identity.get("is_superuser") != "on":
        raise Refused("the container's administrative role is not a superuser; this operation will not guess")


def backup_gate(client, max_age_hours: float = BACKUP_MAX_AGE_HOURS) -> None:
    emit(f"--- backup recovery point must be at most {max_age_hours:g} hours old")
    code = driver.operate_backup_check(client, {}, max_age_hours=max_age_hours)
    if code != driver.EXIT_OK:
        raise Refused("the newest successful backup is missing or too old; no write is attempted")


def open_gates(host, backup_client=None) -> tuple[str, str, dict[str, str]]:
    container = resolve_container(host)
    emit(f"    postgres container: exactly one match ({len(container)} characters)")
    identity = read_pairs(host, container, IDENTITY_SQL)
    identity_gate(identity)
    emit(f"    database {identity['database']}, server {identity['server_version_num']}, administrative role is a superuser")
    state = read_pairs(host, container, STATE_SQL)
    status = classify(state)
    emit(f"    staging state: {status} (schema={state.get('schema')}, role={state.get('role')}, tables={state.get('schema_tables')})")
    for key in sorted(k for k in state if k.startswith("objects_")):
        emit(f"    {key}={state[key]}")
    return container, status, state


# ---------- n8n ----------


def staging_ids_in_use(client, own_names: set[str]) -> set[str]:
    """Postgres credential ids used by workflows that are not the staging ones."""

    used: set[str] = set()
    for item in n8n_deploy.list_all_workflows(client):
        if item.get("name") in own_names:
            continue
        full = n8n_deploy.call(client, "GET", f"/workflows/{urllib.parse.quote(str(item['id']))}")
        for node in (full.get("nodes") or []) if isinstance(full, dict) else []:
            ref = (node.get("credentials") or {}).get("postgres")
            if isinstance(ref, dict) and ref.get("id"):
                used.add(str(ref["id"]))
    return used


def bind_postgres(definition: dict, credential_id: str) -> dict:
    """Return the importable body with every Postgres node bound to the staging credential."""

    body = {
        "name": definition["name"],
        "nodes": json.loads(json.dumps(definition["nodes"])),
        "connections": definition["connections"],
        "settings": definition.get("settings", {}),
    }
    for node in body["nodes"]:
        if node.get("type") == "n8n-nodes-base.postgres":
            node["credentials"] = {"postgres": {"id": credential_id, "name": CREDENTIAL_NAME}}
            node.pop("disabled", None)
    return body


def create_postgres_credential(client, pw: str) -> str:
    created = n8n_deploy.call(
        client, "POST", "/credentials",
        body={
            "name": CREDENTIAL_NAME, "type": "postgres",
            "data": {
                "host": DB_CONTAINER, "port": DB_PORT, "database": DATABASE, "user": ROLE, "password": pw,
                "ssl": "disable", "allowUnauthorizedCerts": False, "maxConnections": ROLE_CONNECTION_LIMIT, "sshTunnel": False,
            },
        },
        expect=(200, 201),
    )
    if not isinstance(created, dict) or not created.get("id"):
        raise Refused("n8n did not return a credential id")
    return str(created["id"])


def import_workflows(client, workflows: dict[str, dict], credential_id: str) -> dict[str, str]:
    """Create or update exactly the six staging workflows, inactive, bound to the staging credential."""

    by_name: dict[str, list[dict]] = {}
    for item in n8n_deploy.list_all_workflows(client):
        by_name.setdefault(str(item.get("name")), []).append(item)
    ids: dict[str, str] = {}
    for path, definition in workflows.items():
        name = definition["name"]
        found = by_name.get(name, [])
        if len(found) > 1:
            raise Refused(f"{len(found)} workflows are named {name!r}; refusing to guess")
        body = bind_postgres(definition, credential_id)
        if found:
            workflow_id = str(found[0]["id"])
            if found[0].get("active"):
                raise Refused(f"{name!r} is active; this operation never changes an active workflow")
            executions = n8n_deploy.call(client, "GET", "/executions", query={"workflowId": workflow_id, "limit": 1})
            if isinstance(executions, dict) and executions.get("data"):
                raise Refused(f"{name!r} has executions; refusing to overwrite a workflow that has run")
            result = n8n_deploy.call(client, "PUT", f"/workflows/{urllib.parse.quote(workflow_id)}", body=body)
        else:
            result = n8n_deploy.call(client, "POST", "/workflows", body=body, expect=(200, 201))
            workflow_id = str(result.get("id")) if isinstance(result, dict) else ""
        if not workflow_id or not isinstance(result, dict) or result.get("active"):
            raise Refused(f"{name!r} did not come back inactive")
        ids[name] = workflow_id
        emit(f"    {name}: id={workflow_id} inactive")
    return ids


def readback(client, workflows: dict[str, dict], ids: dict[str, str] | None = None) -> str:
    """Prove the staging workflows are inactive, never ran, and use only the staging credential."""

    named = {d["name"]: d for d in workflows.values()}
    listing = n8n_deploy.list_all_workflows(client)
    chosen: set[str] = set()
    for name, definition in named.items():
        found = [i for i in listing if i.get("name") == name]
        if len(found) != 1:
            raise Refused(f"expected one workflow named {name!r}, found {len(found)}")
        workflow_id = str(found[0]["id"])
        full = n8n_deploy.call(client, "GET", f"/workflows/{urllib.parse.quote(workflow_id)}")
        if full.get("active"):
            raise Refused(f"{name!r} is active")
        if len(full.get("nodes", [])) != len(definition["nodes"]):
            raise Refused(f"{name!r} node count differs from the pinned definition")
        triggers = [n["type"] for n in full["nodes"] if re.search(r"(Trigger|webhook|cron|schedule)", n["type"], re.I)]
        if triggers != ["n8n-nodes-base.manualTrigger"]:
            raise Refused(f"{name!r} has triggers other than the manual one")
        for node in full["nodes"]:
            if node["type"] == "n8n-nodes-base.postgres":
                ref = (node.get("credentials") or {}).get("postgres") or {}
                if ref.get("name") != CREDENTIAL_NAME:
                    raise Refused(f"{name!r}: a Postgres node is not bound to {CREDENTIAL_NAME}")
                chosen.add(str(ref.get("id")))
                if node.get("disabled"):
                    raise Refused(f"{name!r}: a Postgres node is still disabled")
        executions = n8n_deploy.call(client, "GET", "/executions", query={"workflowId": workflow_id, "limit": 1})
        if isinstance(executions, dict) and executions.get("data"):
            raise Refused(f"{name!r} has executions")
    if len(chosen) != 1:
        raise Refused(f"the staging workflows use {len(chosen)} different Postgres credentials, expected exactly one")
    credential_id = chosen.pop()
    others = staging_ids_in_use(client, set(named))
    if credential_id in others:
        raise Refused("the staging credential id is also used by a workflow that is not a staging one")
    emit(f"    six workflows inactive, manual only, never run; one Postgres credential ({CREDENTIAL_NAME}) used by none of the other workflows")
    return credential_id


# ---------- operations ----------


def make_n8n_client():
    base_url = os.environ.get("N8N_BASE_URL", "").strip()
    key = os.environ.get("N8N_API_CREDENTIAL", "").strip()
    if not base_url or not key:
        raise Refused("N8N_BASE_URL and N8N_API_CREDENTIAL are required")
    driver.register_redaction(key)
    return n8n_deploy.N8nClient(base_url, key)


def make_backup_client():
    url = os.environ.get("COOLIFY_URL", "").strip()
    key = os.environ.get("COOLIFY_API_TOKEN", "").strip()
    if not url or not key:
        raise Refused("COOLIFY_URL and COOLIFY_API_TOKEN are required for the backup gate")
    driver.register_redaction(key)
    return driver.Client(url, key)


def require_confirmation(operation: str, confirm: str) -> None:
    if operation in WRITE_OPERATIONS and confirm != CONFIRMATION[operation]:
        raise Refused(f"{operation} needs the confirmation phrase {CONFIRMATION[operation]!r}")


def op_preflight(host, fetch=fetch_pinned, backup_client=None, n8n_client=None) -> int:
    emit("--- preflight (read-only)")
    _, status, _ = open_gates(host)
    migrations, workflows = load_pinned(fetch)
    for path in MIGRATION_PINS:
        migration_body(path, migrations[path])
    emit(f"    pinned commit {PIN_COMMIT[:12]}: 2 migrations and 6 workflows match their sha256, migrations pass the statement scan")
    if backup_client is not None:
        backup_gate(backup_client)
    if n8n_client is not None:
        listing = n8n_deploy.list_all_workflows(n8n_client)
        ids = {item.get("id") for item in listing}
        if n8n_deploy.KNOWN_N8N_CLOUD_WORKFLOW_ID in ids or not (n8n_deploy.KNOWN_SELF_HOSTED_WORKFLOW_IDS & ids):
            raise Refused("N8N_BASE_URL does not look like the self-hosted instance")
        emit("    n8n: self-hosted instance confirmed")
    if status == "drifted":
        raise Refused("the staging schema or role exists in an unexpected shape; it is not repaired automatically")
    emit(f"RESULT preflight ok state={status}")
    return driver.EXIT_OK


def op_provision_database(host, fetch=fetch_pinned, backup_client=None) -> int:
    emit("--- provision-database")
    container, status, _ = open_gates(host)
    if status == "drifted":
        raise Refused("the staging schema or role exists in an unexpected shape; it is not repaired automatically")
    if status != "absent":
        emit(f"RESULT provision-database ok state={status} (nothing to do)")
        return driver.EXIT_OK
    migrations, _ = load_pinned(fetch)
    sql = provision_sql(migrations)
    if backup_client is None:
        raise Refused("the backup gate is mandatory for a write")
    backup_gate(backup_client)
    emit("    running one transaction: schema, role, migrations 009 and 010, grants, isolation proof")
    completed = psql(host, container, sql)
    for line in (completed.stderr or "").splitlines():
        emit(f"    ! {line}")
    if completed.returncode != 0:
        raise Refused("the transaction failed and was rolled back; nothing was committed")
    after = classify(read_pairs(host, container, STATE_SQL))
    if after != "provisioned-no-password":
        raise Refused(f"unexpected state after provisioning: {after}")
    emit("RESULT provision-database ok state=provisioned-no-password")
    return driver.EXIT_OK


def op_verify(host, fetch=fetch_pinned, n8n_client=None) -> int:
    emit("--- verify (read-only)")
    container, status, _ = open_gates(host)
    if status not in ("provisioned-no-password", "exact"):
        raise Refused(f"nothing to verify: state is {status}")
    proof = read_pairs(host, container, VERIFY_SQL)
    for key in ("outside_relations_reachable", "outside_sequences_usable", "outside_schemas_creatable", "outside_secdef_executable", "inside_tables_deletable"):
        emit(f"    {key}={proof.get(key)}")
        if proof.get(key) != "0":
            raise Refused(f"isolation proof failed: {key}={proof.get(key)}")
    for key in ("approvals_updatable", "config_writable"):
        emit(f"    {key}={proof.get(key)}")
        if proof.get(key) != "false":
            raise Refused(f"isolation proof failed: {key}={proof.get(key)}")
    if proof.get("database_create") != "false" or proof.get("inside_tables_writable") != str(EXPECTED_TABLES):
        raise Refused("isolation proof failed: database-level or in-schema privileges are not as expected")
    emit("    the role reaches nothing outside the staging schema and can write only inside it")
    if n8n_client is not None:
        _, workflows = load_pinned(fetch)
        readback(n8n_client, workflows)
    emit("RESULT verify ok")
    return driver.EXIT_OK


def new_password() -> str:
    pw = secrets.token_urlsafe(36)[:48]
    if not PASSWORD_ALPHABET.fullmatch(pw):
        raise Refused("generated a password outside the expected alphabet")
    # Tell GitHub to mask it before it exists anywhere else, then register it here too.
    print(f"::add-mask::{pw}", flush=True)
    driver.register_redaction(pw)
    return pw


def op_configure_n8n(host, fetch=fetch_pinned, backup_client=None, n8n_client=None, password_factory=new_password) -> int:
    emit("--- configure-n8n")
    container, status, _ = open_gates(host)
    if status not in ("provisioned-no-password", "exact"):
        raise Refused(f"the database is {status}; run provision-database first")
    if n8n_client is None or backup_client is None:
        raise Refused("the backup gate and the n8n client are mandatory for a write")
    listing = n8n_deploy.list_all_workflows(n8n_client)
    ids = {item.get("id") for item in listing}
    if n8n_deploy.KNOWN_N8N_CLOUD_WORKFLOW_ID in ids or not (n8n_deploy.KNOWN_SELF_HOSTED_WORKFLOW_IDS & ids):
        raise Refused("N8N_BASE_URL does not look like the self-hosted instance")
    _, workflows = load_pinned(fetch)
    backup_gate(backup_client)
    pw = password_factory()
    completed = psql(host, container, SET_PASSWORD_SQL.replace("{pw}", pw))
    if completed.returncode != 0:
        raise Refused("setting the role password failed; the credential was not created")
    emit("    role password set (stored as a SCRAM verifier; value not shown)")
    credential_id = create_postgres_credential(n8n_client, pw)
    emit(f"    n8n credential {CREDENTIAL_NAME}: id={credential_id}")
    emit("    if an older credential with this name exists, delete the older one in the n8n UI")
    import_workflows(n8n_client, workflows, credential_id)
    readback(n8n_client, workflows)
    emit("RESULT configure-n8n ok")
    return driver.EXIT_OK


def op_import_workflows(credential_id: str, fetch=fetch_pinned, n8n_client=None) -> int:
    """Import the six workflows against a credential the owner created in the n8n UI.

    This is the path for when the SSH channel is down: the role password was made
    on the database terminal and typed into the n8n credential form by the owner,
    so this job never sees it. It needs no SSH and no database access. n8n cannot
    be asked which credential an id belongs to, so the readback refuses an id that
    any non-staging workflow already uses, and the owner confirms in the editor
    that the Postgres nodes show the staging credential's name.
    """

    emit("--- import-workflows")
    if not CREDENTIAL_ID.fullmatch(credential_id or ""):
        raise Refused("credential_id must be the 8 to 32 letters and digits of the n8n credential id")
    if n8n_client is None:
        raise Refused("the n8n client is mandatory for a write")
    listing = n8n_deploy.list_all_workflows(n8n_client)
    ids = {item.get("id") for item in listing}
    if n8n_deploy.KNOWN_N8N_CLOUD_WORKFLOW_ID in ids or not (n8n_deploy.KNOWN_SELF_HOSTED_WORKFLOW_IDS & ids):
        raise Refused("N8N_BASE_URL does not look like the self-hosted instance")
    _, workflows = load_pinned(fetch)
    own = {d["name"] for d in workflows.values()}
    if credential_id in staging_ids_in_use(n8n_client, own):
        raise Refused("that credential id is used by a workflow that is not a staging one; refusing to bind staging to it")
    import_workflows(n8n_client, workflows, credential_id)
    readback(n8n_client, workflows)
    emit("    now open one staging workflow in the n8n editor: every Postgres node must show the credential you created")
    emit("RESULT import-workflows ok")
    return driver.EXIT_OK


def run(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--operation", required=True, choices=OPERATIONS + ("owner-sql",))
    parser.add_argument("--confirm", default="")
    parser.add_argument("--credential-id", default="")
    parser.add_argument("--out", default="")
    args = parser.parse_args(argv)
    try:
        if args.operation == "owner-sql":
            migrations, _ = load_pinned()
            text = owner_sql(migrations)
            if not args.out:
                raise Refused("--out is required for owner-sql")
            Path(args.out).write_text(text, encoding="utf-8", newline="\n")
            emit(f"wrote {len(text)} characters; run it once in the Coolify terminal of the database resource")
            return driver.EXIT_OK
        require_confirmation(args.operation, args.confirm)
        if args.operation == "import-workflows":
            return op_import_workflows(args.credential_id.strip(), n8n_client=make_n8n_client())
        with prod_ssh.Host() as host:
            if args.operation == "preflight":
                return op_preflight(host, backup_client=make_backup_client(), n8n_client=make_n8n_client())
            if args.operation == "provision-database":
                return op_provision_database(host, backup_client=make_backup_client())
            if args.operation == "verify":
                return op_verify(host, n8n_client=make_n8n_client())
            return op_configure_n8n(host, backup_client=make_backup_client(), n8n_client=make_n8n_client())
    except driver.Abort as abort:
        emit(f"ABORT {abort}")
        return abort.code
    except subprocess.TimeoutExpired:
        emit("ABORT the remote command did not finish in time")
        return driver.EXIT_FAILED


if __name__ == "__main__":
    sys.exit(run())
