#!/usr/bin/env python3
"""Read-only evidence for the governed-agent-pilot canary (Phase 7).

Reports what the database actually holds for the canary's fixed task_id/
run_id -- the run ledger row agent-runtime should have written, and every
ai_gateway_call row against that run_id -- so the mission's evidence report
and its idempotency check are backed by real rows rather than a green
checkmark in the n8n UI. Writes nothing; the first and second (idempotency
replay) runs both call this, and the comparison between the two is done by
the caller, not here.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import postgres_runtime_role as base  # noqa: E402

EXIT_OK = base.EXIT_OK
Abort = base.Abort
emit = base.emit
sql_literal = base.sql_literal
scalar = base.scalar

# Fixed by the canary workflow itself (Build Synthetic Fixture Identity),
# never generated per execution -- that fixed pair is what makes a replay an
# idempotency check instead of a second, distinct run.
TASK_ID = "pilot-canary-2026-09-task-001"
RUN_ID = "pilot-canary-2026-09-run-001"


def operate_evidence(target) -> int:
    emit("--- canary-evidence")
    emit(f"    task_id={TASK_ID} run_id={RUN_ID}")

    task_present = scalar(
        target, f"SELECT count(*) FROM agent_task WHERE task_id = {sql_literal(TASK_ID)};"
    )
    emit(f"    agent_task rows: {task_present}")

    run_present = scalar(
        target, f"SELECT count(*) FROM agent_run WHERE run_id = {sql_literal(RUN_ID)};"
    )
    if run_present == "1":
        status = scalar(
            target, f"SELECT status FROM agent_run WHERE run_id = {sql_literal(RUN_ID)};"
        )
        started = scalar(
            target,
            f"SELECT started_at::text FROM agent_run WHERE run_id = {sql_literal(RUN_ID)};",
        )
        ended = scalar(
            target,
            "SELECT coalesce(ended_at::text, '(not set)') FROM agent_run "
            f"WHERE run_id = {sql_literal(RUN_ID)};",
        )
        emit(f"    agent_run: status={status!r} started_at={started} ended_at={ended}")
    else:
        emit(f"    agent_run rows: {run_present}")

    call_count = scalar(
        target, f"SELECT count(*) FROM ai_gateway_call WHERE run_id = {sql_literal(RUN_ID)};"
    )
    emit(f"    ai_gateway_call rows for this run_id: {call_count}")
    if call_count != "0":
        finalized_count = scalar(
            target,
            "SELECT count(*) FROM ai_gateway_call "
            f"WHERE run_id = {sql_literal(RUN_ID)} AND finalized_at IS NOT NULL;",
        )
        provider_model = scalar(
            target,
            "SELECT string_agg(distinct provider || '/' || model, ', ') FROM ai_gateway_call "
            f"WHERE run_id = {sql_literal(RUN_ID)};",
        )
        total_actual_eur = scalar(
            target,
            "SELECT coalesce(sum(actual_eur_amount), 0)::text FROM ai_gateway_call "
            f"WHERE run_id = {sql_literal(RUN_ID)};",
        )
        call_ids = scalar(
            target,
            "SELECT string_agg(call_id, ', ' ORDER BY reserved_at) FROM ai_gateway_call "
            f"WHERE run_id = {sql_literal(RUN_ID)};",
        )
        emit(f"    finalized: {finalized_count} of {call_count}")
        emit(f"    provider/model: {provider_model}")
        emit(f"    total actual_eur_amount: {total_actual_eur}")
        emit(f"    call_id(s): {call_ids}")

    emit("")
    emit(
        f"RESULT canary-evidence ok agent_task={task_present} agent_run={run_present} "
        f"ai_gateway_call={call_count}"
    )
    return EXIT_OK


def parse_arguments(argv: list[str]):
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--transport", choices=("network", "docker"), default="network")
    parser.add_argument("--container", default=None)
    parser.add_argument("--db-host", default=os.environ.get("PG_ADMIN_HOST", ""))
    parser.add_argument("--db-port", type=int, default=int(os.environ.get("PG_ADMIN_PORT", "5432")))
    parser.add_argument("--db-user", default=os.environ.get("PG_ADMIN_USER", ""))
    parser.add_argument("--admin-sslmode", default=os.environ.get("PG_ADMIN_SSLMODE", "prefer"))
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    arguments = parse_arguments(argv)
    try:
        target = base.choose_target(arguments)
        return operate_evidence(target)
    except Abort as abort:
        emit(f"ABORT {abort}")
        return abort.code


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
