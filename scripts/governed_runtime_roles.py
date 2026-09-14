#!/usr/bin/env python3
"""Provision the two least-privilege Postgres roles the governed pilot needs.

The agent runtime and the drive adapter each connect to `adapteng_ops` as their
own dedicated role, the same way the AI gateway already does. Rather than
duplicate scripts/postgres_runtime_role.py's plumbing -- container/network
routing, Coolify discovery, credential generation, the publish-and-reread
contract that makes a write's success mean something -- this module imports it
and declares only what differs per role: which table(s), which grants, which
Coolify application and which environment key the DSN is published under.

Two operations, in the order they must be used, exactly as in the base module:

    recon      read-only. Reports whether the tables this role needs already
               exist. Writes nothing.
    provision  creates the role if absent, applies the grants, runs a
               verification that both proves the intended access works and
               that access to every other table is still denied, then posts
               the DSN to Coolify. The verification runs inside a transaction
               that is rolled back, not committed, so no row it writes
               outlives the check.
"""

from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import postgres_runtime_role as base  # noqa: E402

DATABASE = base.DATABASE
EXIT_OK = base.EXIT_OK
Abort = base.Abort
emit = base.emit
sql_literal = base.sql_literal
scalar = base.scalar
psql = base.psql


class Profile:
    """Everything that distinguishes one governed role from another."""

    def __init__(
        self,
        key: str,
        role: str,
        env_key: str,
        app_uuid_env: str,
        tables: tuple[str, ...],
        grant_sql,
        verification_sql,
    ) -> None:
        self.key = key
        self.role = role
        self.env_key = env_key
        self.app_uuid_env = app_uuid_env
        self.tables = tables
        self.grant_sql = grant_sql
        self.verification_sql = verification_sql


# --------------------------------------------------------------------------- #
# governed-agent-runtime: services/adapteng-agent-runtime's ledger role.
#
# The service upserts into exactly two tables (app/adapters/ledger_postgres.py:
# _AGENT_TASK_SQL / _AGENT_RUN_SQL), never agent_outcome, via
# INSERT ... ON CONFLICT ... DO UPDATE ... RETURNING, which needs SELECT,
# INSERT and UPDATE -- there are no wrapper functions here the way the AI
# gateway's ledger has, so the grant is on the tables directly.
# --------------------------------------------------------------------------- #

AGENT_RUNTIME_ROLE = "governed_agent_runtime"
AGENT_RUNTIME_TABLES = ("agent_task", "agent_run")


def agent_runtime_grant_sql() -> str:
    lines = [f"REVOKE ALL ON SCHEMA public FROM {AGENT_RUNTIME_ROLE};",
             f"GRANT USAGE ON SCHEMA public TO {AGENT_RUNTIME_ROLE};"]
    for table in AGENT_RUNTIME_TABLES:
        lines.append(f"REVOKE ALL ON TABLE {table} FROM {AGENT_RUNTIME_ROLE};")
        lines.append(f"GRANT SELECT, INSERT, UPDATE ON TABLE {table} TO {AGENT_RUNTIME_ROLE};")
    return "\n".join(lines) + "\n"


def agent_runtime_verification_sql() -> str:
    return f"""
BEGIN;
SET ROLE {AGENT_RUNTIME_ROLE};
INSERT INTO agent_task (task_id, task_class) VALUES ('__verify__', 'verify');
INSERT INTO agent_run (run_id, task_id, model_provider, model_version, status)
    VALUES ('__verify__', '__verify__', 'verify', 'verify', 'started');
UPDATE agent_run SET status = 'succeeded' WHERE run_id = '__verify__';
DO $$
BEGIN
    BEGIN
        PERFORM 1 FROM ai_gateway_call LIMIT 1;
        RAISE EXCEPTION '4b FAILED: SELECT on ai_gateway_call succeeded';
    EXCEPTION WHEN insufficient_privilege THEN
        RAISE NOTICE '4b OK: ai_gateway_call denied';
    END;
    BEGIN
        PERFORM 1 FROM drive_bridge_replay_reservations LIMIT 1;
        RAISE EXCEPTION '4c FAILED: SELECT on drive_bridge_replay_reservations succeeded';
    EXCEPTION WHEN insufficient_privilege THEN
        RAISE NOTICE '4c OK: drive_bridge_replay_reservations denied';
    END;
END
$$;
RESET ROLE;
ROLLBACK;
"""


# --------------------------------------------------------------------------- #
# drive-bridge-replay: services/adapteng-drive-adapter's replay-reservation
# role, scoped to one table (governed_bridge.py's read-reserve-complete
# sequence: SELECT ... FOR UPDATE, INSERT, then UPDATE of completed/
# completed_at only). Migration 008_drive_bridge_replay_reservations.sql
# already revokes UPDATE of the identity/payload columns from PUBLIC; granting
# UPDATE on only (completed, completed_at) to this role is what that revoke was
# written for.
# --------------------------------------------------------------------------- #

DRIVE_BRIDGE_REPLAY_ROLE = "drive_bridge_replay_runtime"
DRIVE_BRIDGE_REPLAY_TABLE = "drive_bridge_replay_reservations"


def drive_bridge_replay_grant_sql() -> str:
    return (
        f"REVOKE ALL ON SCHEMA public FROM {DRIVE_BRIDGE_REPLAY_ROLE};\n"
        f"GRANT USAGE ON SCHEMA public TO {DRIVE_BRIDGE_REPLAY_ROLE};\n"
        f"REVOKE ALL ON TABLE {DRIVE_BRIDGE_REPLAY_TABLE} FROM {DRIVE_BRIDGE_REPLAY_ROLE};\n"
        f"GRANT SELECT, INSERT ON TABLE {DRIVE_BRIDGE_REPLAY_TABLE} TO {DRIVE_BRIDGE_REPLAY_ROLE};\n"
        f"GRANT UPDATE (completed, completed_at) ON TABLE {DRIVE_BRIDGE_REPLAY_TABLE} "
        f"TO {DRIVE_BRIDGE_REPLAY_ROLE};\n"
    )


def drive_bridge_replay_verification_sql() -> str:
    digest = "0" * 64
    payload = "1" * 64
    return f"""
BEGIN;
SET ROLE {DRIVE_BRIDGE_REPLAY_ROLE};
INSERT INTO drive_bridge_replay_reservations (key_digest, operation, payload_sha256, target_file_id)
    VALUES ('{digest}', 'verify', '{payload}', 'verifyfileid00000000');
SELECT operation FROM drive_bridge_replay_reservations WHERE key_digest = '{digest}' FOR UPDATE;
UPDATE drive_bridge_replay_reservations SET completed = TRUE, completed_at = now()
    WHERE key_digest = '{digest}';
DO $$
BEGIN
    BEGIN
        UPDATE drive_bridge_replay_reservations SET key_digest = '{'2' * 64}'
            WHERE key_digest = '{digest}';
        RAISE EXCEPTION '4b FAILED: UPDATE of key_digest succeeded';
    EXCEPTION WHEN insufficient_privilege THEN
        RAISE NOTICE '4b OK: UPDATE of key_digest denied (column-scoped grant enforced)';
    END;
    BEGIN
        PERFORM 1 FROM agent_task LIMIT 1;
        RAISE EXCEPTION '4c FAILED: SELECT on agent_task succeeded';
    EXCEPTION WHEN insufficient_privilege THEN
        RAISE NOTICE '4c OK: agent_task denied';
    END;
END
$$;
RESET ROLE;
ROLLBACK;
"""


PROFILES: dict[str, Profile] = {
    "agent-runtime": Profile(
        key="agent-runtime",
        role=AGENT_RUNTIME_ROLE,
        env_key="GOVERNED_AGENT_RUNTIME_DATABASE_URL",
        app_uuid_env="AGENT_RUNTIME_COOLIFY_APP_UUID",
        tables=AGENT_RUNTIME_TABLES,
        grant_sql=agent_runtime_grant_sql,
        verification_sql=agent_runtime_verification_sql,
    ),
    "drive-bridge-replay": Profile(
        key="drive-bridge-replay",
        role=DRIVE_BRIDGE_REPLAY_ROLE,
        env_key="DRIVE_BRIDGE_REPLAY_DATABASE_URL",
        app_uuid_env="DRIVE_ADAPTER_COOLIFY_APP_UUID",
        tables=(DRIVE_BRIDGE_REPLAY_TABLE,),
        grant_sql=drive_bridge_replay_grant_sql,
        verification_sql=drive_bridge_replay_verification_sql,
    ),
}


def role_sql(role: str, role_credential: str, role_exists: bool) -> str:
    """Return the role statement. Identical in shape to base.role_sql, only
    parameterised by role name so one function serves both profiles."""

    literal = sql_literal(role_credential)
    if role_exists:
        statement = f"ALTER ROLE {role} WITH LOGIN PASSWORD {literal};"
    else:
        statement = (
            f"CREATE ROLE {role} WITH LOGIN PASSWORD {literal} "
            "NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS "
            "CONNECTION LIMIT 20;"
        )
    return (
        f"{statement}\n"
        f"ALTER ROLE {role} NOSUPERUSER NOCREATEDB NOCREATEROLE "
        "NOREPLICATION NOBYPASSRLS CONNECTION LIMIT 20;\n"
    )


def build_dsn(role: str, role_credential: str, host: str, port: int, sslmode: str) -> str:
    return f"postgresql://{role}:{role_credential}@{host}:{port}/{DATABASE}?sslmode={sslmode}"


def operate_recon(profile: Profile, target) -> int:
    emit(f"--- recon {profile.role}")
    emit(f"    database reached over: {target.describe()}")
    emit(f"    connected as: {scalar(target, 'SELECT current_user;')}")

    missing = []
    for table in profile.tables:
        present = scalar(target, f"SELECT to_regclass({sql_literal(table)}) IS NOT NULL;")
        emit(f"    table {table}: {'present' if present == 't' else 'ABSENT'}")
        if present != "t":
            missing.append(table)

    role_present = scalar(
        target, f"SELECT count(*) FROM pg_roles WHERE rolname = {sql_literal(profile.role)};"
    )
    emit(f"    role {profile.role}: {'present' if role_present == '1' else 'ABSENT'}")

    emit("")
    if missing:
        emit(f"RESULT recon ok ready_to_provision=no missing_tables={len(missing)}")
        return EXIT_OK
    emit(
        f"RESULT recon ok ready_to_provision=yes "
        f"role_exists={'yes' if role_present == '1' else 'no'}"
    )
    return EXIT_OK


def operate_provision(
    profile: Profile,
    target,
    application_uuid: str,
    base_url: str,
    credential: str,
    dsn_host: str,
    dsn_port: int,
    sslmode: str,
) -> int:
    emit(f"--- provision {profile.role}")
    if not credential:
        raise Abort("no Coolify API credential was supplied, so the DSN could not be published")
    if not application_uuid:
        raise Abort(f"no Coolify application uuid was supplied ({profile.app_uuid_env})")
    if not dsn_host:
        raise Abort("no database host was supplied for the DSN")

    emit(f"    database reached over: {target.describe()}")

    measured = base.report_transport_encryption(target)
    if sslmode in base.DEMANDING_SSL_MODES and measured != base.ENCRYPTED:
        raise Abort(
            f"the DSN would declare sslmode={sslmode} against a server that is not "
            "encrypted; either enable TLS or declare a mode this server can honour"
        )

    for table in profile.tables:
        if scalar(target, f"SELECT to_regclass({sql_literal(table)}) IS NOT NULL;") != "t":
            raise Abort(
                f"table {table} does not exist, so the grants would bind to nothing; "
                "run recon and apply the owning migration before provisioning"
            )
    emit(f"    all {len(profile.tables)} table(s) present")

    role_exists = (
        scalar(target, f"SELECT count(*) FROM pg_roles WHERE rolname = {sql_literal(profile.role)};")
        == "1"
    )
    emit(f"    role {profile.role}: {'present, password will be rotated' if role_exists else 'absent, will be created'}")

    role_credential = base.generate_credential()
    psql(target, role_sql(profile.role, role_credential, role_exists))
    emit(f"    role {profile.role}: {'rotated' if role_exists else 'created'}")

    psql(target, profile.grant_sql())
    emit(f"    grants applied on {len(profile.tables)} table(s)")

    _, stdout, stderr = psql(target, profile.verification_sql())
    for line in (stderr or "").splitlines():
        cleaned = line.replace("NOTICE:  ", "").strip()
        if cleaned:
            emit(f"      {cleaned}")
    emit("    verification passed and its rows were rolled back")

    dsn = build_dsn(profile.role, role_credential, dsn_host, dsn_port, sslmode)
    outcome = base.publish_environment_value(base_url, credential, application_uuid, profile.env_key, dsn)
    emit(f"    {profile.env_key}: {outcome} in Coolify and confirmed by re-reading")
    emit("")
    emit(f"RESULT provision ok role={profile.role} rotated={'yes' if role_exists else 'no'} dsn_published=yes")
    return EXIT_OK


def parse_arguments(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("profile", choices=sorted(PROFILES))
    parser.add_argument("operation", choices=("recon", "provision"))
    parser.add_argument("--transport", choices=("network", "docker"), default="network")
    parser.add_argument("--container", default=None)
    parser.add_argument("--db-host", default=os.environ.get("PG_ADMIN_HOST", ""))
    parser.add_argument("--db-port", type=int, default=int(os.environ.get("PG_ADMIN_PORT", "5432")))
    parser.add_argument("--db-user", default=os.environ.get("PG_ADMIN_USER", ""))
    parser.add_argument("--application-uuid", default="")
    parser.add_argument("--dsn-host", default=os.environ.get("PG_DSN_HOST", ""))
    parser.add_argument("--dsn-port", type=int, default=int(os.environ.get("PG_DSN_PORT", "5432")))
    parser.add_argument("--sslmode", default=os.environ.get("PG_SSLMODE", "prefer"))
    parser.add_argument("--admin-sslmode", default=os.environ.get("PG_ADMIN_SSLMODE", "prefer"))
    return parser.parse_args(argv)


def target_namespace(arguments: argparse.Namespace) -> argparse.Namespace:
    return argparse.Namespace(
        transport=arguments.transport,
        container=arguments.container,
        db_host=arguments.db_host,
        db_port=arguments.db_port,
        db_user=arguments.db_user,
        admin_sslmode=arguments.admin_sslmode,
    )


def main(argv: list[str]) -> int:
    arguments = parse_arguments(argv)
    profile = PROFILES[arguments.profile]
    try:
        target = base.choose_target(target_namespace(arguments))
        if arguments.operation == "recon":
            return operate_recon(profile, target)
        application_uuid = arguments.application_uuid or os.environ.get(profile.app_uuid_env, "")
        return operate_provision(
            profile,
            target,
            application_uuid,
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
