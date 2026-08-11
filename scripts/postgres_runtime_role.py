#!/usr/bin/env python3
"""Provision the least-privilege Postgres role the AI Gateway runs as.

This script runs ON the production host, not on a runner. That is not a
preference: production Postgres accepts connections only from the host, and the
runtime password must never exist anywhere a runner can see it. The password is
generated here, used here, and posted to the local Coolify API from here. It is
never printed, never written to a file that outlives the run, and never returned
to the caller.

Two operations, in the order they must be used:

    recon      read-only. Reports what exists: the container, the database, the
               role, the six functions, the table. Writes nothing.
    provision  creates the role if absent, applies the grants from the runbook
               services/ai-gateway/docs/ai-gateway-postgres-runtime-role.md,
               runs the runbook's own verifications, and posts the DSN to
               Coolify. Refuses to proceed if recon's preconditions do not hold.

The SQL is a transcription of that runbook. Where the runbook says "run this as
superuser", this runs it through the container's postgres superuser over the
local socket, which is the only superuser access this host offers without a
password.
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import shlex
import subprocess
import sys
import urllib.error
import urllib.request

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_USAGE = 2

DATABASE = "adapteng_ops"
ROLE = "ai_gateway_runtime"
LEDGER_TABLE = "ai_gateway_call"

# Signatures copied from the runbook, which copied them from the REVOKE
# statements at the end of database/migrations/008_ai_gateway_runtime_hardening.sql.
# They are matched against pg_proc by identity, so a drifted signature is a
# missing function here rather than a silently different grant.
GRANTED_FUNCTIONS = (
    (
        "ai_gateway_reserve_call",
        "TEXT, TEXT, TEXT, TEXT, TEXT, TEXT, TEXT, "
        "BIGINT, BIGINT, BIGINT, NUMERIC, TEXT, NUMERIC, NUMERIC, "
        "TIMESTAMPTZ, TEXT, TEXT, TEXT, TEXT, TEXT, INTEGER, TIMESTAMPTZ",
    ),
    (
        "ai_gateway_record_usage",
        "TEXT, BIGINT, BIGINT, BIGINT, NUMERIC, NUMERIC, TEXT, TEXT, TIMESTAMPTZ",
    ),
    (
        "ai_gateway_finalize_call",
        "TEXT, TEXT, BIGINT, BIGINT, BIGINT, NUMERIC, NUMERIC, TEXT, TEXT, TEXT, "
        "TIMESTAMPTZ",
    ),
    ("ai_gateway_require_reconciliation", "TEXT, TEXT"),
    ("ai_gateway_mark_expired_leases", "INTEGER, TIMESTAMPTZ"),
    (
        "ai_gateway_reconcile_call",
        "TEXT, TEXT, BIGINT, BIGINT, BIGINT, NUMERIC, NUMERIC, TIMESTAMPTZ",
    ),
)


class Abort(Exception):
    """A refusal to continue. The message says what was refused and why."""

    def __init__(self, message: str, code: int = EXIT_FAILED) -> None:
        super().__init__(message)
        self.code = code


def emit(line: str = "") -> None:
    print(line, flush=True)


# --------------------------------------------------------------------------- #
# Host inspection
# --------------------------------------------------------------------------- #


def run(command: list[str], stdin: str | None = None, check: bool = True):
    """Run a command and return (returncode, stdout, stderr), all decoded.

    stdin carries SQL. It is never logged, because a provisioning statement
    contains the password.
    """

    completed = subprocess.run(
        command,
        input=stdin,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if check and completed.returncode != 0:
        raise Abort(
            f"{shlex.join(command[:3])} failed with exit {completed.returncode}: "
            f"{(completed.stderr or completed.stdout or '').strip()[:400]}"
        )
    return completed.returncode, completed.stdout or "", completed.stderr or ""


def parse_container_table(output: str) -> list[tuple[str, str]]:
    """Parse `docker ps` name/image pairs, ignoring blank and malformed lines."""

    rows: list[tuple[str, str]] = []
    for line in output.splitlines():
        parts = [part.strip() for part in line.split("\t")]
        if len(parts) >= 2 and parts[0] and parts[1]:
            rows.append((parts[0], parts[1]))
    return rows


def postgres_containers(rows: list[tuple[str, str]]) -> list[str]:
    """Return the names of running containers whose image is a Postgres image.

    The image is the discriminator, not the name. A container called
    "adapteng-ops-db" proves nothing about what runs inside it, and a name match
    would also catch things like a pgbackrest sidecar.
    """

    found = []
    for name, image in rows:
        base = image.split("@", 1)[0].rsplit("/", 1)[-1].lower()
        if base.startswith("postgres") or base.startswith("postgis"):
            found.append(name)
    return sorted(found)


def choose_container(names: list[str]) -> str:
    """Return the one Postgres container, refusing to guess between several."""

    if not names:
        raise Abort(
            "no running container uses a Postgres image, so there is no database "
            "to provision against on this host"
        )
    if len(names) > 1:
        raise Abort(
            f"{len(names)} running containers use a Postgres image ({names}). "
            "Which one holds canonical production is a decision, not a lookup; "
            "declare it with --container"
        )
    return names[0]


def find_container(explicit: str | None) -> str:
    if explicit:
        return explicit
    _, stdout, _ = run(["docker", "ps", "--format", "{{.Names}}\t{{.Image}}"])
    return choose_container(postgres_containers(parse_container_table(stdout)))


def psql(container: str, sql: str, database: str = DATABASE, check: bool = True):
    """Run SQL as the container's postgres superuser over the local socket.

    -At gives unaligned, untitled output so a result is a bare value that can be
    compared exactly. ON_ERROR_STOP makes a failed statement a failed command
    rather than a zero exit with an error printed to stderr.
    """

    command = [
        "docker",
        "exec",
        "-i",
        "-u",
        "postgres",
        container,
        "psql",
        "-v",
        "ON_ERROR_STOP=1",
        "-At",
        "-d",
        database,
        "-f",
        "-",
    ]
    return run(command, stdin=sql, check=check)


def scalar(container: str, sql: str, database: str = DATABASE) -> str:
    _, stdout, _ = psql(container, sql, database=database)
    return stdout.strip()


def function_exists_sql(name: str, signature: str) -> str:
    """Return SQL that reports whether one exact function identity exists.

    to_regprocedure returns NULL rather than raising when nothing matches, so a
    drifted signature reads as absent instead of aborting the whole survey.
    """

    identity = f"{name}({signature})"
    return f"SELECT to_regprocedure({sql_literal(identity)}) IS NOT NULL;"


def sql_literal(value: str) -> str:
    """Quote a value as a SQL string literal, doubling any embedded quote.

    Used for identifiers-as-text and for the generated password. The password is
    the reason this exists: it must reach Postgres intact and must never be
    assembled by string formatting that could break on a quote.
    """

    return "'" + value.replace("'", "''") + "'"


# --------------------------------------------------------------------------- #
# Recon
# --------------------------------------------------------------------------- #


def operate_recon(container: str | None = None) -> int:
    emit("--- recon ai_gateway_runtime")
    _, hostname, _ = run(["hostname"])
    emit(f"    host: {hostname.strip()}")

    name = find_container(container)
    emit(f"    postgres container: {name}")

    emit(f"    server version: {scalar(name, 'SHOW server_version;', database='postgres')}")
    databases = scalar(
        name,
        f"SELECT count(*) FROM pg_database WHERE datname = {sql_literal(DATABASE)};",
        database="postgres",
    )
    if databases != "1":
        raise Abort(
            f"database {DATABASE} does not exist on this server, so this is not "
            "the canonical production database; refusing to guess another"
        )
    emit(f"    database {DATABASE}: present")
    emit(f"    connected as: {scalar(name, 'SELECT current_user;')}")
    emit(f"    superuser: {scalar(name, 'SELECT usesuper FROM pg_user WHERE usename = current_user;')}")

    role_present = scalar(
        name, f"SELECT count(*) FROM pg_roles WHERE rolname = {sql_literal(ROLE)};"
    )
    emit(f"    role {ROLE}: {'present' if role_present == '1' else 'ABSENT'}")
    if role_present == "1":
        attributes = scalar(
            name,
            "SELECT rolsuper::text || ' ' || rolcreatedb::text || ' ' || "
            "rolcreaterole::text || ' ' || rolcanlogin::text || ' ' || "
            "rolconnlimit::text FROM pg_roles WHERE rolname = "
            f"{sql_literal(ROLE)};",
        )
        emit(f"      super createdb createrole canlogin connlimit: {attributes}")

    table_present = scalar(
        name, f"SELECT to_regclass({sql_literal(LEDGER_TABLE)}) IS NOT NULL;"
    )
    emit(f"    table {LEDGER_TABLE}: {'present' if table_present == 't' else 'ABSENT'}")

    emit("    functions migration 008a must have defined:")
    missing = []
    for function_name, signature in GRANTED_FUNCTIONS:
        present = scalar(name, function_exists_sql(function_name, signature))
        emit(f"      {function_name}: {'present' if present == 't' else 'ABSENT'}")
        if present != "t":
            missing.append(function_name)

    emit("")
    if missing or table_present != "t":
        emit(
            "RESULT recon ok ready_to_provision=no "
            f"missing_functions={len(missing)} table_present={table_present == 't'}"
        )
        return EXIT_OK
    emit(
        "RESULT recon ok ready_to_provision=yes "
        f"role_exists={'yes' if role_present == '1' else 'no'}"
    )
    return EXIT_OK


# --------------------------------------------------------------------------- #
# Provision
# --------------------------------------------------------------------------- #


def generate_credential() -> str:
    """Return a password with no character that needs escaping in a DSN.

    token_urlsafe can emit '-' and '_' only, both safe in a URL userinfo field,
    so the DSN needs no percent-encoding and cannot be mis-parsed by a client
    that handles encoding differently from this script.
    """

    return secrets.token_urlsafe(48)


def role_sql(role_credential: str, role_exists: bool) -> str:
    """Return the role statement. An existing role has its password rotated.

    Rotation is deliberate: this run is about to publish a DSN, and publishing
    one built from a password this run does not know is not possible. The
    attribute reset runs either way, as the runbook's belt-and-braces step.
    """

    literal = sql_literal(role_credential)
    if role_exists:
        statement = f"ALTER ROLE {ROLE} WITH LOGIN PASSWORD {literal};"
    else:
        statement = (
            f"CREATE ROLE {ROLE} WITH LOGIN PASSWORD {literal} "
            "NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS "
            "CONNECTION LIMIT 20;"
        )
    return (
        f"{statement}\n"
        f"ALTER ROLE {ROLE} NOSUPERUSER NOCREATEDB NOCREATEROLE "
        "NOREPLICATION NOBYPASSRLS CONNECTION LIMIT 20;\n"
    )


def grant_sql() -> str:
    """Return the deny-by-default and EXECUTE grants, transcribed from the runbook."""

    lines = [
        f"REVOKE ALL ON SCHEMA public FROM {ROLE};",
        f"GRANT USAGE ON SCHEMA public TO {ROLE};",
        f"REVOKE ALL ON TABLE {LEDGER_TABLE} FROM {ROLE};",
    ]
    for name, signature in GRANTED_FUNCTIONS:
        lines.append(f"GRANT EXECUTE ON FUNCTION {name}({signature}) TO {ROLE};")
    return "\n".join(lines) + "\n"


def verification_sql() -> str:
    """Return the runbook's verifications 4a-4d as one fail-loud script.

    Each block raises rather than notices on the wrong outcome, so a failure is
    a non-zero psql exit instead of a line in the output that a caller has to
    remember to read.
    """

    tables = "'agent_run', 'agent_task', 'approval_outbox', 'approval_request'"
    return f"""
SET ROLE {ROLE};
DO $$
BEGIN
    BEGIN
        INSERT INTO {LEDGER_TABLE} (call_id) VALUES ('should-fail');
        RAISE EXCEPTION '4a FAILED: INSERT on {LEDGER_TABLE} succeeded';
    EXCEPTION WHEN insufficient_privilege THEN
        RAISE NOTICE '4a OK: INSERT denied';
    END;
    BEGIN
        PERFORM 1 FROM {LEDGER_TABLE} LIMIT 1;
        RAISE EXCEPTION '4b FAILED: SELECT on {LEDGER_TABLE} succeeded';
    EXCEPTION WHEN insufficient_privilege THEN
        RAISE NOTICE '4b OK: SELECT denied';
    END;
END
$$;
DO $$
DECLARE
    v_table TEXT;
BEGIN
    FOREACH v_table IN ARRAY ARRAY[{tables}]
    LOOP
        BEGIN
            EXECUTE format('SELECT 1 FROM %I LIMIT 1', v_table);
            RAISE EXCEPTION '4c FAILED: SELECT on % succeeded', v_table;
        EXCEPTION
            WHEN insufficient_privilege THEN
                RAISE NOTICE '4c OK: SELECT on % denied', v_table;
            WHEN undefined_table THEN
                RAISE NOTICE '4c SKIP: % does not exist', v_table;
        END;
    END LOOP;
END
$$;
DO $$
BEGIN
    BEGIN
        PERFORM * FROM ai_gateway_reserve_call(
            '', '', 'extract', 'vertex-ai', 'gemini-3.1-flash-lite', 'eu',
            'https://aiplatform.eu.rep.googleapis.com',
            0, 0, 0, 0, 'USD', 0, 1, now(), 'x', '2026-07-27',
            repeat('0', 64), 'v1', 'caller', 90
        );
        RAISE EXCEPTION '4d FAILED: the call with an empty id did not raise';
    EXCEPTION
        WHEN sqlstate '22023' THEN
            RAISE NOTICE '4d OK: EXECUTE succeeded and the function validated its own input';
        WHEN insufficient_privilege THEN
            RAISE EXCEPTION '4d FAILED: EXECUTE denied, the grant did not take effect';
    END;
END
$$;
RESET ROLE;
"""


def build_dsn(role_credential: str, host: str, port: int, sslmode: str) -> str:
    """Return the DSN in the shape the runbook and the migrations README declare."""

    return (
        f"postgresql://{ROLE}:{role_credential}@{host}:{port}/{DATABASE}?sslmode={sslmode}"
    )


def coolify_request(base_url: str, credential: str, method: str, path: str, payload: dict | None):
    """Return (status, parsed) for one Coolify API call, raising only on transport.

    A non-2xx is returned rather than raised so a caller can distinguish "this
    key does not exist yet" from "the instance refused". The body is never
    logged: it echoes the value that was just sent.
    """

    if not base_url.lower().startswith("https://"):
        raise Abort("the Coolify base address must be https, so a credential cannot be sent in clear")
    endpoint = f"{base_url.rstrip('/')}/api/v1{path}"
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    headers = {
        "Authorization": f"Bearer {credential}",
        "Accept": "application/json",
    }
    if data is not None:
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(endpoint, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            body = response.read().decode("utf-8", "replace")
            try:
                return response.status, json.loads(body) if body else None
            except json.JSONDecodeError:
                return response.status, None
    except urllib.error.HTTPError as error:
        return error.code, None
    except urllib.error.URLError as error:
        raise Abort(f"the Coolify API could not be reached: {error.reason}") from None


def publish_environment_value(
    base_url: str, credential: str, application_uuid: str, key: str, value: str
) -> str:
    """Set one environment value and prove it was stored, without printing it.

    Coolify updates an existing key and creates a missing one through different
    methods, so both are tried. The write is then re-read and compared in memory
    against what was sent; only the outcome is reported. A write this function
    could not confirm is a failure, exactly as in scripts/coolify_deploy.py.
    """

    outcome = ""
    for method, label in (("PATCH", "updated"), ("POST", "created")):
        status, _ = coolify_request(
            base_url,
            credential,
            method,
            f"/applications/{application_uuid}/envs",
            {"key": key, "value": value},
        )
        if 200 <= status < 300:
            outcome = label
            break
        if status not in (404, 409, 422):
            raise Abort(f"setting {key} in Coolify returned HTTP {status}")
    if not outcome:
        raise Abort(f"setting {key} in Coolify was refused by both PATCH and POST")

    status, entries = coolify_request(
        base_url,
        credential,
        "GET",
        f"/applications/{application_uuid}/envs",
        None,
    )
    if status != 200 or not isinstance(entries, list):
        raise Abort(
            f"{key} was accepted but the environment could not be re-read to "
            f"confirm it (HTTP {status}); refusing to report success"
        )
    stored = [item for item in entries if isinstance(item, dict) and item.get("key") == key]
    if not stored:
        raise Abort(f"{key} was accepted but is not present on re-read")
    if not any(item.get("value") == value for item in stored):
        raise Abort(f"{key} is present but does not hold what was sent")
    return outcome


def operate_provision(
    container: str | None,
    application_uuid: str,
    base_url: str,
    credential: str,
    dsn_host: str,
    dsn_port: int,
    sslmode: str,
) -> int:
    emit("--- provision ai_gateway_runtime")
    if not credential:
        raise Abort("no Coolify API credential was supplied, so the DSN could not be published")
    if not application_uuid:
        raise Abort("no Coolify application uuid was supplied")
    if not dsn_host:
        raise Abort(
            "no database host was supplied for the DSN. It is not guessed: the "
            "address the container reaches Postgres on is a placement fact, and "
            "a wrong one produces a role that works and a gateway that cannot connect"
        )

    name = find_container(container)
    emit(f"    postgres container: {name}")

    for function_name, signature in GRANTED_FUNCTIONS:
        if scalar(name, function_exists_sql(function_name, signature)) != "t":
            raise Abort(
                f"{function_name} does not exist with the signature migration 008a "
                "defines, so the grants would bind to nothing; run recon and apply "
                "008a before provisioning"
            )
    emit(f"    all {len(GRANTED_FUNCTIONS)} functions present with the declared signatures")

    role_exists = (
        scalar(name, f"SELECT count(*) FROM pg_roles WHERE rolname = {sql_literal(ROLE)};")
        == "1"
    )
    emit(f"    role {ROLE}: {'present, password will be rotated' if role_exists else 'absent, will be created'}")

    role_credential = generate_credential()
    psql(name, role_sql(role_credential, role_exists))
    emit(f"    role {ROLE}: {'rotated' if role_exists else 'created'}")

    psql(name, grant_sql())
    emit(f"    grants applied: usage on schema public and execute on {len(GRANTED_FUNCTIONS)} functions")

    _, stdout, stderr = psql(name, verification_sql())
    for line in (stderr or "").splitlines():
        cleaned = line.replace("NOTICE:  ", "").strip()
        if cleaned:
            emit(f"      {cleaned}")
    emit("    verifications 4a-4d passed")

    dsn = build_dsn(role_credential, dsn_host, dsn_port, sslmode)
    outcome = publish_environment_value(
        base_url, credential, application_uuid, "AI_GATEWAY_DATABASE_URL", dsn
    )
    emit(f"    AI_GATEWAY_DATABASE_URL: {outcome} in Coolify and confirmed by re-reading")
    emit("")
    emit(
        f"RESULT provision ok role={ROLE} rotated={'yes' if role_exists else 'no'} "
        f"dsn_published=yes"
    )
    return EXIT_OK


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #


def parse_arguments(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("recon", "provision"))
    parser.add_argument("--container", default=None)
    parser.add_argument("--application-uuid", default=os.environ.get("COOLIFY_APP_UUID", ""))
    parser.add_argument("--dsn-host", default=os.environ.get("PG_DSN_HOST", ""))
    parser.add_argument("--dsn-port", type=int, default=int(os.environ.get("PG_DSN_PORT", "5432")))
    parser.add_argument("--sslmode", default=os.environ.get("PG_SSLMODE", "verify-full"))
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    arguments = parse_arguments(argv)
    try:
        if arguments.operation == "recon":
            return operate_recon(arguments.container)
        return operate_provision(
            arguments.container,
            arguments.application_uuid,
            os.environ.get("COOLIFY_URL", ""),
            os.environ.get("COOLIFY_API_TOKEN", ""),
            arguments.dsn_host,
            arguments.dsn_port,
            arguments.sslmode,
        )
    except Abort as abort:
        emit(f"ABORT {abort}")
        return abort.code


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
