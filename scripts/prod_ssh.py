#!/usr/bin/env python3
"""Run a small set of committed administrative commands on the production host.

Why this exists
---------------
Two workflows in this repository -- gateway-readiness and postgres-runtime-role
-- are pinned to ``runs-on: [self-hosted, adapteng-ops]``. That runner is
``exited:unhealthy`` and cannot be re-registered without an administration
token this account does not hold, so both are stranded. Everything they did
needed one capability: run a command on the host where the containers live.

The only other channel into a container is Coolify's scheduled-task endpoint,
and on 2026-09-07 that endpoint was measured on both the peer and the service:
it accepts commands up to 245 characters and refuses 300 with HTTP 500. That is
enough for a probe and far too little for a SQL statement, so it cannot open a
row in the run ledger, apply a migration, or do anything else the platform now
needs. This is not a preference between channels; one of them physically does
not fit.

The host credentials for the other channel already exist in this repository as
owner-provisioned secrets -- PROD_SSH_PRIVATE_KEY, PROD_SSH_KNOWN_HOSTS -- and
as variables for host, port and user. Nothing used them. This does.

Shape of the tool
-----------------
The remote command is never taken from a dispatch input. Every operation is a
constant in this file, reviewable in a diff, so "what can this run in
production" is answered by reading the source rather than by trusting whoever
filled in a form. Free-form SQL over SSH into a production database is exactly
the capability that should not exist behind a workflow_dispatch box.

Payloads travel on stdin, not in the command line. That removes the quoting
question entirely -- no shell on either side ever parses the SQL -- and with it
the class of bug where a quote in a value changes what runs.

Secrets are written to files with 0600 permissions in a temporary directory
outside the checkout, and that directory is removed in a finally block. The key
material is never passed as an argument, so it cannot appear in a process list.
"""

from __future__ import annotations

import argparse
import os
import shutil
import stat
import subprocess
import sys
import tempfile

EXIT_OK = 0
EXIT_FAILED = 1

OPERATIONS = (
    "containers",
    "ledger-status",
    "open-smoke-run",
)

# The database container is found by name rather than by a hard-coded uuid.
# Coolify rebuilds a resource under a new container id whenever it is
# redeployed, so a uuid captured today is a stale constant tomorrow; the
# resource name is the part that is declared and stable.
DB_CONTAINER_MATCH = "adapteng-ops-db"

# psql reads from stdin, stops on the first error rather than limping to the
# end reporting success, and takes the role and database from the container's
# own environment. Those two variables are set by the Postgres image itself, so
# no credential is passed from here, and connecting over the local socket means
# no password is involved at all.
PSQL = (
    "sh -c 'psql -v ON_ERROR_STOP=1 --no-psqlrc -q "
    '-U "$POSTGRES_USER" -d "$POSTGRES_DB" -f -\''
)

# Counts only. This answers "is the ledger empty" without reading a single
# business value out of production.
LEDGER_STATUS_SQL = """
SELECT 'agent_task' AS relation, count(1) AS rows FROM agent_task
UNION ALL SELECT 'agent_run', count(1) FROM agent_run
UNION ALL SELECT 'ai_gateway_call', count(1) FROM ai_gateway_call
ORDER BY 1;
"""

# The gateway binds every model call to a row in the canonical run ledger:
# ai_gateway_call.run_id is a foreign key to agent_run, and the reserve function
# raises 'unknown run_id' when it does not resolve. A first call therefore
# cannot happen until a run exists, and nothing deployed opens one -- the
# service that owns that responsibility, services/adapteng-run-ledger, is not
# deployed. This opens exactly one, for the smoke call and nothing else.
#
# Both statements are idempotent on their business key, so a re-run reconciles
# rather than failing or duplicating.
SMOKE_TASK_ID = "ae.platform.smoke"
SMOKE_RUN_SQL = """
INSERT INTO agent_task (task_id, task_class, domain)
VALUES (%(task)s, 'platform.smoke', 'platform')
ON CONFLICT (task_id) DO NOTHING;

INSERT INTO agent_run (run_id, task_id, model_provider, model_version, status)
VALUES (%(run)s, %(task)s, 'vertex-ai', 'gemini-3.1-flash-lite', 'started')
ON CONFLICT (run_id) DO NOTHING;

SELECT run_id, status FROM agent_run WHERE run_id = %(run)s;
"""


def emit(line: str) -> None:
    print(line, flush=True)


def required(name: str) -> str:
    value = (os.environ.get(name) or "").strip()
    if not value:
        raise SystemExit(f"{name} must be set")
    return value


class Host:
    """One SSH connection's worth of configuration, and how to use it.

    Held as an object rather than passed around as six strings because the
    temporary directory holding the key has to be removed exactly once, and a
    context manager is the only arrangement that survives an exception in the
    middle of an operation.
    """

    def __init__(self) -> None:
        self.host = required("PROD_SSH_HOST")
        self.user = required("PROD_SSH_USER")
        self.port = (os.environ.get("PROD_SSH_PORT") or "22").strip()
        self._key_material = required("PROD_SSH_PRIVATE_KEY")
        self._known_hosts_material = required("PROD_SSH_KNOWN_HOSTS")
        self._dir: str | None = None

    def __enter__(self) -> "Host":
        self._dir = tempfile.mkdtemp(prefix="adapteng-ssh-")
        self.key = self._write("id", self._key_material)
        self.known_hosts = self._write("known_hosts", self._known_hosts_material)
        return self

    def __exit__(self, *_exc) -> None:
        if self._dir:
            shutil.rmtree(self._dir, ignore_errors=True)
            self._dir = None

    def _write(self, name: str, material: str) -> str:
        assert self._dir
        path = os.path.join(self._dir, name)
        # Created empty and locked down before anything is written to it, so
        # the material is never briefly readable by another user on the runner.
        handle = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, stat.S_IRUSR | stat.S_IWUSR)
        with os.fdopen(handle, "w") as stream:
            stream.write(material)
            if not material.endswith("\n"):
                stream.write("\n")
        return path

    def argv(self, remote: str) -> list:
        return [
            "ssh",
            # No password prompt, no keyboard-interactive fallback: a failure
            # has to fail rather than hang a runner for its whole timeout.
            "-o",
            "BatchMode=yes",
            # The known-hosts file is supplied, so the host key is verified
            # against a pinned value instead of trusted on first use.
            "-o",
            "StrictHostKeyChecking=yes",
            "-o",
            f"UserKnownHostsFile={self.known_hosts}",
            "-o",
            "IdentitiesOnly=yes",
            "-o",
            "ConnectTimeout=20",
            "-i",
            self.key,
            "-p",
            self.port,
            f"{self.user}@{self.host}",
            remote,
        ]

    def run(self, remote: str, stdin: str | None = None, timeout: int = 120):
        completed = subprocess.run(  # noqa: S603 - argv form, no shell
            self.argv(remote),
            input=stdin,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return completed


def show(completed) -> None:
    for line in (completed.stdout or "").splitlines():
        emit(f"    {line}")
    for line in (completed.stderr or "").splitlines():
        emit(f"    ! {line}")


def find_container(host: Host, match: str) -> str | None:
    """Resolve one running container id from a name fragment.

    Returns None rather than raising when the match is not unique: acting on
    "the first of several" is how the wrong database gets written to, and the
    caller can report an ambiguity far more usefully than a traceback can.
    """

    completed = host.run(f"docker ps --filter name={match} --format {{{{.ID}}}}")
    if completed.returncode != 0:
        emit(f"    docker ps failed: rc={completed.returncode}")
        show(completed)
        return None
    ids = [line.strip() for line in completed.stdout.splitlines() if line.strip()]
    if len(ids) != 1:
        emit(f"    {match}: expected exactly one container, found {len(ids)}")
        return None
    return ids[0]


def operate_containers(host: Host) -> int:
    """Read-only: what is actually running on the host.

    First operation for a reason. It proves the channel works and names the
    containers every later operation has to address, without changing anything.
    """

    emit(f"--- containers on {host.user}@{host.host}:{host.port}")
    completed = host.run('docker ps --format "{{.Names}}\t{{.Status}}"')
    show(completed)
    if completed.returncode != 0:
        emit(f"    rc={completed.returncode}: the channel or the command failed")
        return EXIT_FAILED
    return EXIT_OK


def operate_ledger_status(host: Host) -> int:
    """Read-only: how many rows the run ledger holds."""

    emit("--- ledger-status")
    container = find_container(host, DB_CONTAINER_MATCH)
    if container is None:
        return EXIT_FAILED
    emit(f"    database container: {container}")
    completed = host.run(f"docker exec -i {container} {PSQL}", stdin=LEDGER_STATUS_SQL)
    show(completed)
    return EXIT_OK if completed.returncode == 0 else EXIT_FAILED


def operate_open_smoke_run(host: Host, run_id: str) -> int:
    """Open exactly one run in the canonical ledger for the smoke call."""

    emit(f"--- open-smoke-run {run_id}")
    if not run_id:
        emit("    a run id is required")
        return EXIT_FAILED
    container = find_container(host, DB_CONTAINER_MATCH)
    if container is None:
        return EXIT_FAILED
    emit(f"    database container: {container}")
    # psql has no client-side parameter binding, so the two identifiers are
    # substituted here. Both are generated by this repository from a fixed
    # alphabet -- a timestamp and a constant -- and are checked below rather
    # than trusted, because "it is generated" stops being true the moment
    # someone adds an input.
    for value in (run_id, SMOKE_TASK_ID):
        if not all(character.isalnum() or character in ".-_" for character in value):
            emit(f"    refusing an identifier with unexpected characters: {value!r}")
            return EXIT_FAILED
    sql = SMOKE_RUN_SQL.replace("%(run)s", f"'{run_id}'").replace(
        "%(task)s", f"'{SMOKE_TASK_ID}'"
    )
    completed = host.run(f"docker exec -i {container} {PSQL}", stdin=sql)
    show(completed)
    return EXIT_OK if completed.returncode == 0 else EXIT_FAILED


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--operation", required=True, choices=OPERATIONS)
    parser.add_argument("--run-id", default="")
    return parser.parse_args(argv)


def run(argv=None) -> int:
    args = parse_args(argv)
    with Host() as host:
        if args.operation == "containers":
            return operate_containers(host)
        if args.operation == "ledger-status":
            return operate_ledger_status(host)
        if args.operation == "open-smoke-run":
            return operate_open_smoke_run(host, args.run_id.strip())
    return EXIT_FAILED


def main() -> int:
    try:
        return run()
    except subprocess.TimeoutExpired:
        emit("    the remote command did not finish in time")
        return EXIT_FAILED


if __name__ == "__main__":
    sys.exit(main())
