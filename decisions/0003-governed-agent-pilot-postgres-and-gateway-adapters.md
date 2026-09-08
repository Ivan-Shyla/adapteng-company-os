# ADR-0003: Governed agent pilot — concrete adapters, deployable surface, inactive canary

- **Status:** Accepted
- **Date:** 2026-09-08
- **Deciders:** Ivan (owner authorization in a Claude Code session, continuing the
  in-progress governed-agent-pilot mission), Claude Sonnet 5 (implementation)
- **Scope:** `adapteng-automation-platform/services/adapteng-agent-runtime`,
  `adapteng-company-os/deploy/agent-runtime.json`, the governed-agent-pilot
  n8n canary

## Context

`adapteng-automation-platform` PR #132 (already merged before this ADR)
shipped a mock-only governed agent runtime: the A00 (route) / A10 (normalize)
/ A20 (draft) contracts, and two structural ports — `RunLedgerPort` and
`GatewayPort` — with no implementation. It deliberately added no network, no
database connection, no HTTP server, and no deployment surface.

Taking the pilot from mock-only code to a real internal pilot needs concrete
implementations of both ports, something deployable to call them through, an
orchestration to drive one end-to-end run, and a reviewed rollback path —
without ever performing paid model inference outside one explicitly bounded,
later-authorized smoke call, and without touching the live website lead
pipeline, personal projects, or any protected/rollout-governed boundary.

## Decision

1. **`PostgresRunLedgerAdapter`** implements `RunLedgerPort` as a
   self-contained module inside `adapteng-agent-runtime` — it does not
   cross-import `services/adapteng-run-ledger`'s package. Every service in
   `adapteng-automation-platform` builds as an isolated container (each
   `Dockerfile` `COPY`s only its own `app/` directory), so a cross-service
   Python import would break at build/deploy time; `services/ai-gateway/app/approval.py`
   already documents this same choice for the same reason. The adapter
   re-implements the identical atomic `INSERT ... ON CONFLICT ... RETURNING
   ..., (xmax = 0) AS inserted` upsert contract against the existing
   `agent_task`/`agent_run` schema (`database/migrations/002_run_ledger.sql`)
   — **no new migration**, since that schema already carries every
   idempotency key (`task_id`, `run_id`) the port's contract needs.
2. **`HttpGatewayAdapter`** implements `GatewayPort` over the deployed
   `ai-gateway`'s `POST /v1/gateway`, using stdlib `urllib` (no new HTTP
   dependency, matching the gateway's own stdlib-only style). Its retry
   policy can retry at most once, and only when no response was ever
   received — never after any response, including an error response — so a
   retry can never trigger a second billable inference call; this is backed
   by the gateway's own `call_id` idempotency (`ai_gateway_call.call_id
   PRIMARY KEY`, `database/migrations/008_ai_gateway_runtime_hardening.sql`).
3. A deployable HTTP surface (`GET /health`, `GET /ready`, `POST /v1/run`)
   mirrors `ai-gateway`'s own `http_app.py` pattern. `POST /v1/run` is gated
   by `GOVERNED_AGENT_RUNTIME_ACTIVE`, which **defaults to inactive and fails
   closed** on any value other than the exact lowercase string `"true"` —
   the pilot cannot do real work anywhere it is deployed until that flag is
   deliberately flipped for the bounded canary window.
4. `adapteng-company-os/deploy/agent-runtime.json` declares the Coolify
   deployment target (private-network-only, no public FQDN, health-checked
   via the image's own `HEALTHCHECK` against `/ready`), mirroring
   `deploy/ai-gateway.json`'s already-reviewed pattern.
5. The n8n orchestration (`n8n/workflows/experimental/MM-32-governed-agent-pilot-canary.json`)
   is manual-trigger-only (`n8n-nodes-base.manualTrigger`) with **fixed**
   `task_id`/`run_id` constants — rerunning it unchanged is the Phase 7
   idempotency proof, not a new logical run. It is version-controlled and
   registered in `n8n/workflow-classification.json`/`workflow-index.json`
   but has **not** been imported into the live self-hosted n8n instance.
6. A reviewed rollback/disable procedure
   (`runbooks/governed-agent-pilot-rollback.md`) was written and merged
   *before* any of Phase 3/4/7 (credential bind, bounded model smoke, real
   canary) executes for real.

## Alternatives considered

- **Import `adapteng-run-ledger`'s package directly from `adapteng-agent-runtime`** —
  rejected: breaks the existing one-service-one-container convention every
  other service in this repository already follows; the sibling package
  would not exist inside `adapteng-agent-runtime`'s built image.
- **Wire `adapteng-agent-runtime` into `adapter-tests.yml`'s existing CI
  matrix** — attempted, then reverted. `adapteng-automation-platform` treats
  the entire `.github/workflows/` tree as protected
  (`scripts/validation/verify_rollout_trust_anchor.py`'s
  `PROTECTED_PREFIXES`), requiring a cryptographically signed approval
  receipt. The trust root (`.github/trust/rollout-policy/allowed_signers`)
  is currently bootstrap-unarmed (zero principal lines) — no one can
  currently produce a valid receipt. Forcing an unsigned change through this
  boundary was rejected as exactly the kind of protected-boundary bypass
  this platform's own governance exists to prevent. See
  `owner/action-items.md` for the exact bootstrap action that unblocks this,
  and `registry/services.yaml`'s `adapteng-agent-runtime` entry for the
  honest current test-evidence verdict (`LOCAL-ONLY-NOT-CI-VERIFIED`).

## Consequences

- **Positive:** the pilot's core logic (adapters, orchestration, deploy
  spec, n8n workflow) is complete, reviewed, merged, and independently
  verified where it can be — 46 local tests pass (3 more are written and
  correct but need a live Postgres, which the new
  `governed-agent-runtime-postgres-semantics` CI job would exercise once the
  rollout-policy gate above is cleared).
- **Negative / cost:** three real prerequisites remain before Phase 3/4/7 can
  execute: (1) the `VERTEX_SERVICE_ACCOUNT_JSON` owner action; (2) extending
  `coolify-deploy.yml` with `adapteng-agent-runtime`'s own three
  externally-provided secrets before its first real Coolify deploy can
  succeed (a 2026-09-08 read-only `inspect` dispatch aborted on exactly this
  gap, before any Coolify API call); (3) the rollout-policy-authorization
  bootstrap, to get this service's tests protected-CI-verified.
- **Neutral / follow-ups:** all three are tracked in `owner/action-items.md`.
  None of them block continuing to prepare (not execute) the remaining
  phases' exact command sequences.

## Compliance

No secret was committed, printed, or read at any point. No live inference
occurred (Phase 2's readiness workflow explicitly logs
`planned_operation=generateContent (not called)`). No protected/rollout-
governed path was modified without a signed receipt — the one attempt was
identified and reverted rather than forced through. No personal-project
files (Job Monitor, English Coach, Kraken) were touched; an unrelated,
pre-existing local git stash containing personal Job Monitor work was
accidentally popped during unrelated investigation, immediately caught, and
restored untouched without loss. EU data residency and spend caps are
unaffected — this ADR authorizes no inference spend beyond the one bounded
smoke call already gated behind Phase 2's PASS and a merged, reviewed PR
implementing its limits.
