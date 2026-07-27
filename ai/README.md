# AI program

How AdaptEng's own AI agent creates value **inside** the operating system —
safely, cheaply, and only where it beats a deterministic workflow.

The guiding sequence is deliberate: **structure first, AI second.** The governed
spine (adapter + id/reservation authority + governed n8n workflows) is live
(AUT-001, WEB-002). AI plugs into that spine at specific, governed points — it
never becomes a new ungoverned write path.

## Files

| File | What it covers |
|---|---|
| [`insertion-points.md`](insertion-points.md) | The concrete places AI plugs in, in priority order, and the governance gate at each. |
| [`ai-001-pilot-intake.md`](ai-001-pilot-intake.md) | **Fill-in intake** for the first pilot — the owner inputs that unblock the draft assistant, plus gates and what runs next. |
| [`guardrails.md`](guardrails.md) | Non-negotiable safety rules for any AI action (pending-only, no publish, PII, fail-closed). |
| [`model-choices.md`](model-choices.md) | Chosen model + verified current prices + why it's optimal under EU residency. |
| [`cost-controls.md`](cost-controls.md) | Budgets, caps, ledger, and kill criteria. |

## One-paragraph mental model

Our **agent** is a control plane (`ai-dev-loop-control-plane`) that runs bounded
tasks and produces **evidence-wrapped artifacts**. A **model** (via
`ai-gateway`, EU Vertex) is a pluggable component it calls. The first business
value is a **draft** — schema-valid, side-effect-free, landing as *pending* in
Baserow for Ivan to accept/edit/reject. Nothing the AI does can publish, send,
or overwrite a human-owned field. Value is measured (accept rate, time saved);
if it isn't useful within cost, it is paused (see `ARCHITECTURE.md` §12 kill
criteria).
