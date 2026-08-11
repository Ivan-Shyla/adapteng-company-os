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
import hashlib
import json
import os
import secrets
import shlex
import subprocess
import sys
import urllib.error
import urllib.parse
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


def run(
    command: list[str],
    stdin: str | None = None,
    check: bool = True,
    environment: dict[str, str] | None = None,
):
    """Run a command and return (returncode, stdout, stderr), all decoded.

    stdin carries SQL. It is never logged, because a provisioning statement
    contains the password.

    environment carries connection settings for the network route, including the
    password. It is passed this way rather than in a connection string on the
    command line because argv is readable by any other process on the machine
    and an environment is not.
    """

    merged = None
    if environment is not None:
        merged = dict(os.environ)
        merged.update(environment)
    completed = subprocess.run(
        command,
        input=stdin,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=merged,
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


PSQL_FLAGS = ("psql", "-v", "ON_ERROR_STOP=1", "-At", "-f", "-")


class DockerTarget:
    """SQL delivered by the host's Docker daemon, over the container's local socket.

    This route requires the Docker socket, which is root-equivalent on the host.
    It is the right route when the operator already has a shell there, and the
    wrong one for anything automated, which is why it is no longer the default.
    """

    def __init__(self, container: str) -> None:
        self.container = container

    def command(self, database: str) -> list[str]:
        head = ["docker", "exec", "-i", "-u", "postgres", self.container]
        return head + [PSQL_FLAGS[0], *PSQL_FLAGS[1:-2], "-d", database, *PSQL_FLAGS[-2:]]

    def environment(self) -> dict[str, str] | None:
        return None

    def describe(self) -> str:
        return f"container {self.container}"


class NetworkTarget:
    """SQL delivered over the Docker network to the database's own port.

    This is the route an automated operator can hold without holding the host:
    a container on the predefined network reaches the database directly, with no
    Docker socket, no shell and no root anywhere in the path.

    The credential travels in the environment. A connection string on the
    command line would be readable by every other process on the machine.
    """

    def __init__(self, host: str, port: int, user: str, credential: str, sslmode: str) -> None:
        if not host:
            raise Abort("the network route needs a database host and none was given")
        if not user or not credential:
            raise Abort(
                "the network route needs an administrative database user and its "
                "credential, and at least one was not supplied"
            )
        self.host = host
        self.port = port
        self.user = user
        self._credential = credential
        self.sslmode = sslmode

    def command(self, database: str) -> list[str]:
        return [PSQL_FLAGS[0], *PSQL_FLAGS[1:-2], "-d", database, *PSQL_FLAGS[-2:]]

    def environment(self) -> dict[str, str]:
        return {
            "PGHOST": self.host,
            "PGPORT": str(self.port),
            "PGUSER": self.user,
            "PGPASSWORD": self._credential,
            "PGSSLMODE": self.sslmode,
            "PGCONNECT_TIMEOUT": "15",
        }

    def describe(self) -> str:
        return f"{self.host}:{self.port} as {self.user} sslmode={self.sslmode}"


def psql(target, sql: str, database: str = DATABASE, check: bool = True):
    """Run SQL against the database, by whichever route the target describes.

    -At gives unaligned, untitled output so a result is a bare value that can be
    compared exactly. ON_ERROR_STOP makes a failed statement a failed command
    rather than a zero exit with an error printed to stderr.
    """

    return run(
        target.command(database),
        stdin=sql,
        check=check,
        environment=target.environment(),
    )


def scalar(target, sql: str, database: str = DATABASE) -> str:
    _, stdout, _ = psql(target, sql, database=database)
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


TRUE_SPELLINGS = ("t", "true", "on", "yes", "1")
FALSE_SPELLINGS = ("f", "false", "off", "no", "0")

ENCRYPTED = "encrypted"
PLAINTEXT = "plaintext"
UNDETERMINED = ""

# Modes libpq will refuse to connect under when the server offers no TLS. A DSN
# declaring one of these against this server does not degrade: it fails.
DEMANDING_SSL_MODES = ("require", "verify-ca", "verify-full")


def report_transport_encryption(target) -> str:
    """Answer whether this connection is actually encrypted, rather than assuming.

    Two recorded facts about this deployment contradict each other: the database
    is configured enable_ssl=false, and the runtime connection string declares
    ssl_mode=require. Exactly one of those can survive contact with the server,
    and which one is not decidable from either document.

    The server itself knows. pg_stat_ssl reports the encryption state of the
    connection asking the question, so a single read-only query settles it from
    the actual network contract. A route that is not encrypted is reported as
    such and not repaired here, because changing the transport of the canonical
    production database is an owner decision, not a side effect of a survey.
    """

    answer = scalar(
        target,
        "SELECT ssl::text || ' ' || coalesce(version, 'none') || ' ' || "
        "coalesce(cipher, 'none') FROM pg_stat_ssl WHERE pid = pg_backend_pid();",
        database="postgres",
    )
    # A boolean cast to text is 'true'/'false'; the same column read bare is
    # 't'/'f'. Matching only one spelling turned a determinate server answer
    # into "undetermined" on the first live run - an instrument reporting about
    # its own expectations rather than about the thing it measured.
    # The variable is named flag because in this repository the word for a
    # first word is not the word for a credential, and the validator is right
    # to refuse the other one here.
    flag = (answer.split(" ")[0] if answer else "").strip().lower()
    if flag in TRUE_SPELLINGS:
        emit(f"    transport: ENCRYPTED (ssl version cipher: {answer})")
        return ENCRYPTED
    if flag in FALSE_SPELLINGS:
        emit("    transport: NOT ENCRYPTED - the server accepted this connection in the clear")
        emit("      the recorded ssl_mode=require cannot be honoured against this server as")
        emit("      configured; reconciling the two is an owner decision and is not done here")
        return PLAINTEXT
    emit(f"    transport: undetermined (pg_stat_ssl returned {answer!r})")
    return UNDETERMINED


def describe_secret(value: str) -> str:
    """Describe a credential precisely enough to debug it, and never enough to use it.

    A rejected password has several possible causes that read identically from
    the outside: the wrong field was read, the right field was mangled in
    transit, or the recorded value no longer matches the running server. Length,
    character composition and a truncated digest separate all three without
    disclosing anything, and a truncated digest is what makes two recorded
    copies comparable without either being printed.
    """

    if not value:
        return "absent"
    classes = []
    if any(character.islower() for character in value):
        classes.append("lower")
    if any(character.isupper() for character in value):
        classes.append("upper")
    if any(character.isdigit() for character in value):
        classes.append("digit")
    other = sorted({c for c in value if not c.isalnum()})
    if other:
        classes.append("other[" + "".join(other) + "]")
    fingerprint = hashlib.sha256(value.encode("utf-8")).hexdigest()[:8]
    return f"len={len(value)} {'+'.join(classes)} fingerprint={fingerprint}"


def authentication_candidates(record: dict) -> list[dict[str, str]]:
    """Every login this database's own record offers, in the order recorded.

    Coolify records the same credential in two places - assembled into
    internal_db_url and separately as its parts - and they are not guaranteed to
    agree. Enumerating both is what turns 'the password was rejected' into a
    statement about which recorded copy is wrong.
    """

    candidates: list[dict[str, str]] = []
    url = str(record.get("internal_db_url") or "")
    if url:
        try:
            parsed = parse_internal_dsn(url)
        except Abort:
            parsed = {}
        if parsed.get("user") and parsed.get("credential"):
            parsed["source"] = "internal_db_url"
            candidates.append(parsed)
    user = str(record.get("postgres_user") or "")
    admin_credential = str(record.get("postgres_password") or "")
    alias = str(record.get("uuid") or "")
    if user and admin_credential and alias:
        candidates.append(
            {
                "host": alias,
                "port": "5432",
                "user": user,
                "credential": admin_credential,
                "source": "postgres_user/postgres_password",
            }
        )
    return candidates


def operate_credentials(base_url: str, credential: str, sslmode: str) -> int:
    """Report which recorded login actually authenticates, without printing any.

    This reads and writes nothing in the database. It exists because a rejected
    password is the least informative failure in this system: it is indexed by
    the server against a value nobody can see, and every wrong answer produces
    the same message. Testing each recorded copy converts that into a fact.
    """

    emit("--- recorded database logins")
    record = discover_database_record(base_url, credential)
    emit(f"    database: {record.get('name')} uuid={record.get('uuid')}")
    emit(f"    record declares user: {record.get('postgres_user') or 'nothing'}")
    emit(f"    record declares database: {record.get('postgres_db') or 'nothing'}")
    emit(f"    internal address recorded: {'yes' if record.get('internal_db_url') else 'no'}")

    candidates = authentication_candidates(record)
    if not candidates:
        raise Abort(
            "the database record carries no usable login at all. It offers: "
            f"{', '.join(sorted(str(key) for key in record))}"
        )

    working = 0
    for candidate in candidates:
        emit(f"    candidate {candidate['source']}:")
        emit(f"      user={candidate['user']} host={candidate['host']}:{candidate['port']}")
        emit(f"      credential {describe_secret(candidate['credential'])}")
        target = NetworkTarget(
            candidate["host"],
            int(candidate["port"]),
            candidate["user"],
            candidate["credential"],
            sslmode,
        )
        code, _, stderr = psql(target, "SELECT 1;", database="postgres", check=False)
        if code == 0:
            working += 1
            emit("      AUTHENTICATED")
        else:
            first = stderr.strip().splitlines()[0] if stderr.strip() else f"exit {code}"
            emit(f"      rejected: {first}")

    if working:
        emit(f"RESULT credentials ok working={working} of {len(candidates)}")
        return 0
    # Every recorded copy being rejected is a different problem from a bad read,
    # and the difference decides who fixes it: a rejected-everywhere credential
    # means the running server was initialised with a value the record no longer
    # holds, which no amount of reading Coolify will recover.
    emit("RESULT credentials none-authenticate")
    emit("    every login this database's own record holds was rejected by the server.")
    emit("    A Postgres password is fixed at initialisation and is not changed by")
    emit("    editing the record afterwards, so a record edited after first start")
    emit("    describes a login that never existed. Recovering this needs the owner")
    emit("    to reset the password on the server itself.")
    return 1


def operate_recon(target) -> int:
    emit("--- recon ai_gateway_runtime")
    _, hostname, _ = run(["hostname"])
    emit(f"    host: {hostname.strip()}")

    name = target
    emit(f"    database reached over: {target.describe()}")

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
    report_transport_encryption(name)

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
    target,
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

    name = target
    emit(f"    database reached over: {target.describe()}")

    # The DSN is published once and read at every boot, so a mode the server
    # cannot satisfy is not a warning: libpq refuses the connection outright and
    # the gateway fails to start. Measure before declaring.
    measured = report_transport_encryption(name)
    if sslmode in DEMANDING_SSL_MODES and measured != ENCRYPTED:
        raise Abort(
            f"the DSN would declare sslmode={sslmode}, and this server "
            + (
                "answered that it is not using TLS"
                if measured == PLAINTEXT
                else "could not be asked whether it is using TLS"
            )
            + ". libpq refuses to connect at all under that mode against a server "
            "without TLS, so publishing it would produce a working role and a "
            "gateway that cannot start. Either enable TLS on the database, which "
            "is an owner decision, or declare the mode this server can actually "
            "honour with --sslmode."
        )
    if measured == PLAINTEXT:
        emit(f"    DSN will declare sslmode={sslmode}, which matches the measured transport")

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


def looks_like_postgres(item: dict) -> bool:
    """Recognise a Postgres record without depending on one field's spelling.

    Coolify names these records differently across versions and shapes, and a
    filter on one spelling reports "no database exists" when the truth is "this
    instance calls it something else". That failure is indistinguishable from a
    genuinely empty instance, which is the expensive kind of wrong answer.
    """

    for field in ("type", "image", "database_type"):
        if "postgres" in str(item.get(field, "")).lower():
            return True
    return any(key.startswith("postgres_") for key in item)


def describe_candidates(payload: list) -> str:
    """Say what was actually there, so a wrong filter costs no round trip to find."""

    seen = [
        f"{item.get('name')!r}:{item.get('type') or item.get('image') or 'untyped'}"
        for item in payload
        if isinstance(item, dict)
    ]
    return ", ".join(seen) if seen else "nothing at all"


def parse_internal_dsn(url: str) -> dict[str, str]:
    """Split a Postgres URL into parts without importing a driver.

    Only the pieces needed to connect are taken. The credential is returned in
    the mapping and must be treated like any other credential: it is never
    emitted, and it reaches psql through the environment.
    """

    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme not in ("postgres", "postgresql"):
        raise Abort("the recorded internal database address is not a Postgres URL")
    return {
        "host": parsed.hostname or "",
        "port": str(parsed.port or 5432),
        "user": urllib.parse.unquote(parsed.username or ""),
        "credential": urllib.parse.unquote(parsed.password or ""),
    }


def discover_database_record(base_url: str, credential: str) -> dict:
    """Find the one managed Postgres database this instance runs.

    This exists so the operator needs no database credential of its own. The
    Coolify credential it already holds is sufficient, and using it means no new
    secret is created, stored or rotated for this purpose.

    A single unambiguous Postgres database is required. Two would make the
    choice a guess, and guessing which database is production is the one error
    this script must never make.
    """

    if not base_url or not credential:
        raise Abort(
            "no database host was given and the Coolify credentials needed to "
            "discover one are absent, so there is nothing to connect to"
        )
    status, payload = coolify_request(base_url, credential, "GET", "/databases", None)
    if status != 200 or not isinstance(payload, list):
        raise Abort(f"listing databases returned HTTP {status}")
    candidates = [
        item for item in payload if isinstance(item, dict) and looks_like_postgres(item)
    ]
    if not candidates:
        raise Abort(
            "Coolify reports no Postgres database on this instance. It reported: "
            f"{describe_candidates(payload)}"
        )
    if len(candidates) > 1:
        names = ", ".join(sorted(str(item.get("name")) for item in candidates))
        raise Abort(
            f"Coolify reports {len(candidates)} Postgres databases ({names}); "
            "name the one to use with --db-host rather than having it guessed"
        )
    return candidates[0]


def discover_admin_connection(base_url: str, credential: str) -> dict[str, str]:
    """Read the address and login for that database out of its record."""

    database = discover_database_record(base_url, credential)
    emit(f"    database discovered from Coolify: {database.get('name')}")
    url = str(database.get("internal_db_url") or "")
    if url:
        return parse_internal_dsn(url)
    # Not every version reports the assembled address. The parts are enough,
    # and the network alias is the resource uuid, which is the same value the
    # runtime DSN host is recorded as.
    user = str(database.get("postgres_user") or "")
    admin_credential = str(database.get("postgres_password") or "")
    alias = str(database.get("uuid") or "")
    if not (user and admin_credential and alias):
        raise Abort(
            "the database record carries neither an internal address nor the parts "
            "to build one, so its position on the Docker network cannot be "
            "established without guessing"
        )
    return {"host": alias, "port": "5432", "user": user, "credential": admin_credential}


def choose_target(arguments: argparse.Namespace):
    """Build the route to the database that the declared transport describes.

    The default is the network, because the operator this script is written for
    is a container that deliberately holds no Docker socket. The Docker route
    stays available for a human with a shell on the host, where it is the
    simpler of the two.
    """

    if arguments.transport == "docker":
        return DockerTarget(find_container(arguments.container))
    host = arguments.db_host
    port = arguments.db_port
    user = arguments.db_user
    admin_credential = os.environ.get("PGPASSWORD_ADMIN", "")
    # Anything not supplied is looked up. The distinction that matters here is
    # between a value someone asked for and a value that was merely defaulted:
    # a default user of "postgres" reads identically to an explicit choice, and
    # would silently replace the login the database itself records. It did, and
    # the server rejected the result with a message that said only that the
    # password was wrong.
    if not (host and user and admin_credential):
        discovered = discover_admin_connection(
            os.environ.get("COOLIFY_URL", ""), os.environ.get("COOLIFY_API_TOKEN", "")
        )
        if not host:
            host = discovered["host"]
            port = int(discovered["port"])
        user = user or discovered["user"]
        admin_credential = admin_credential or discovered["credential"]
    return NetworkTarget(host, port, user, admin_credential, arguments.admin_sslmode)


def parse_arguments(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("recon", "provision", "credentials"))
    parser.add_argument("--transport", choices=("network", "docker"), default="network")
    parser.add_argument("--container", default=None)
    parser.add_argument("--db-host", default=os.environ.get("PG_ADMIN_HOST", ""))
    parser.add_argument("--db-port", type=int, default=int(os.environ.get("PG_ADMIN_PORT", "5432")))
    # Empty rather than "postgres": the administrative user is a property of the
    # database, not of this script, and every managed database records its own.
    # A non-empty default cannot be told apart from an explicit request, so it
    # would win over the recorded value and connect as a user that may not exist.
    parser.add_argument("--db-user", default=os.environ.get("PG_ADMIN_USER", ""))
    parser.add_argument("--application-uuid", default=os.environ.get("COOLIFY_APP_UUID", ""))
    parser.add_argument("--dsn-host", default=os.environ.get("PG_DSN_HOST", ""))
    parser.add_argument("--dsn-port", type=int, default=int(os.environ.get("PG_DSN_PORT", "5432")))
    # The measured answer, not the assumed one. The recorded pair
    # (enable_ssl=false, ssl_mode=require) is contradictory, and the server
    # settled it on the first live connection: pg_stat_ssl reports this session
    # as not encrypted, so no TLS is on offer. verify-full would therefore not
    # be a stricter DSN, it would be an unusable one - libpq refuses to connect
    # at all rather than degrading. prefer is what this server can honour, and
    # it upgrades by itself if TLS is ever enabled. Nothing about that is taken
    # on trust: provision measures the transport again and refuses to publish
    # any demanding mode it cannot observe being satisfied.
    parser.add_argument("--sslmode", default=os.environ.get("PG_SSLMODE", "prefer"))
    # The mode this script connects with is a different question from the mode
    # the gateway's published connection string declares. prefer is the right
    # default for a survey: it encrypts when the server offers it and connects
    # when it does not, so recon can report which of the two contradictory
    # recorded facts is true instead of failing before it can find out.
    parser.add_argument("--admin-sslmode", default=os.environ.get("PG_ADMIN_SSLMODE", "prefer"))
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    arguments = parse_arguments(argv)
    try:
        if arguments.operation == "credentials":
            # This operation is about the logins themselves, so it must not go
            # through the target builder: that would pick one of them and fail
            # on it before the others could be reported.
            return operate_credentials(
                os.environ.get("COOLIFY_URL", ""),
                os.environ.get("COOLIFY_API_TOKEN", ""),
                arguments.admin_sslmode,
            )
        target = choose_target(arguments)
        if arguments.operation == "recon":
            return operate_recon(target)
        return operate_provision(
            target,
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
