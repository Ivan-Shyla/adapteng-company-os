# AI program

How AdaptEng's own AI agent creates value **inside** the operating system —
safely, cheaply, and only where it beats a deterministic workflow.

The guiding sequence is deliberate: **structure first, AI second.** The governed
spine (adapter + id/reservation authority + governed n8n workflows) is live
(AUT-001, WEB-002). AI plugs into that spine at specific, governed points — it
never becomes a new ungoverned write path.

**Current business-AI readiness: NOT DEPLOYED.** The `ai-gateway` service is
**implemented and tested, not deployed** — that is the whole of its status, and
it is neither cost-blocked nor blocked on FX configuration. Its suite is green on
`adapteng-automation-platform` `main`: workflow run
[`31214858400`](https://github.com/Ivan-Shyla/adapteng-automation-platform/actions/runs/31214858400)
(*AI Gateway Tests*, head `d6ab6322983af42e355dedea4de6d0d21752de59`,
2026-08-07T20:12:40Z) concluded `success` with all five jobs green: unit tests on
`ubuntu-latest` **and** `windows-latest`, PostgreSQL semantics, supply-chain
gates and repo validation. Its persistent cost schema is in production too —
migrations 005 and `008_ai_gateway_runtime_hardening.sql` are among the nine
logical units the owner's post-rollout production check found exact (see
[`owner/action-items.md`](../owner/action-items.md); **do not replay them**).

What is genuinely missing is **deployment and the runtime wiring around it**: no
`ai-gateway` container runs in Coolify, so the tested persistent cost authority
is not yet the live authority, and the real EU Vertex client, Drive adapters,
orchestration and approval composition are not proven against a running service.
No live model call has occurred. Until the service is deployed and those paths
are proven, this is not working business AI — but the reason is deployment, not
cost, not FX, and no longer a set of open deterministic defects.

## Files

| File | What it covers |
|---|---|
| [`insertion-points.md`](insertion-points.md) | The concrete places AI plugs in, in priority order, and the governance gate at each. |
| [`ai-001-pilot-intake.md`](ai-001-pilot-intake.md) | Fixed CASE-vs-ART pilot contract, the remaining deployment gates and what may run next. |
| [`guardrails.md`](guardrails.md) | Non-negotiable safety rules for any AI action (pending-only, no publish, PII, fail-closed). |
| [`model-choices.md`](model-choices.md) | Chosen model + verified current prices + why it's optimal under EU residency. |
| [`cost-controls.md`](cost-controls.md) | Budgets, caps, ledger, and kill criteria. |

## One-paragraph mental model

The intended **agent** uses `ai-dev-loop-control-plane` for bounded task and
evidence contracts, then persistent automation-platform services for provider,
cost, approval and Drive execution. The target first business value is a
**draft** — schema-valid, side-effect-free, landing as *pending* in Baserow for
Ivan to accept/edit/reject. No AI path may publish, send or overwrite a
human-owned field. This remains a target design until the gateway is deployed and
its runtime paths are proven. Value is then measured (accept rate, time saved);
if it is not useful within cost, it is paused (see `ARCHITECTURE.md` §12 kill
criteria).
