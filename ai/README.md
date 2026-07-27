# AI program

How AdaptEng's own AI agent creates value **inside** the operating system —
safely, cheaply, and only where it beats a deterministic workflow.

The guiding sequence is deliberate: **structure first, AI second.** The governed
spine (adapter + id/reservation authority + governed n8n workflows) is live
(AUT-001, WEB-002). AI plugs into that spine at specific, governed points — it
never becomes a new ungoverned write path.

**Current business-AI readiness: REJECT_LIVE.** Control-plane main
`affe6ea1e4d522be0df0641e98a08e20a84549ae` contains deterministic
AG-001/002/003/006/007 contracts only. It has no business worker, real provider
or Drive runtime, and the production audit reproduced envelope,
`no_external_action`/synthetic-approval and over-cap/negative-budget P0
bypasses. AG-008 owns deterministic fixes; automation-platform still owns the
persistent Postgres cost authority, EU Vertex and Drive adapters, orchestration,
approval composition and deployment. This is not deployed/working business AI.

## Files

| File | What it covers |
|---|---|
| [`insertion-points.md`](insertion-points.md) | The concrete places AI plugs in, in priority order, and the governance gate at each. |
| [`ai-001-pilot-intake.md`](ai-001-pilot-intake.md) | Fixed CASE-vs-ART pilot contract, REJECT_LIVE gates and what may run next. |
| [`guardrails.md`](guardrails.md) | Non-negotiable safety rules for any AI action (pending-only, no publish, PII, fail-closed). |
| [`model-choices.md`](model-choices.md) | Chosen model + verified current prices + why it's optimal under EU residency. |
| [`cost-controls.md`](cost-controls.md) | Budgets, caps, ledger, and kill criteria. |

## One-paragraph mental model

The intended **agent** uses `ai-dev-loop-control-plane` for bounded task and
evidence contracts, then persistent automation-platform services for provider,
cost, approval and Drive execution. The target first business value is a
**draft** — schema-valid, side-effect-free, landing as *pending* in Baserow for
Ivan to accept/edit/reject. No AI path may publish, send or overwrite a
human-owned field. This remains a target design until the REJECT_LIVE blockers
close. Value is then measured (accept rate, time saved); if it is not useful
within cost, it is paused (see `ARCHITECTURE.md` §12 kill criteria).
