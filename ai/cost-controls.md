# Cost controls

Two independent budgets, hard caps, a ledger, and explicit kill criteria. The
goal: AI can only ever cost a little, and only while it's proven useful.

## Two budgets (never mixed)

| Budget | Covers | Cap | Notes |
|---|---|---|---|
| `runtime_model_cap` | Business-artifact runtime (gateway drafts / classify / extract) | **€10 / month** | Plus €0.10/call and €1/day. Fail-closed. Raised only after accepted outputs. |
| `dev_model_budget` | `code_change` mode (agentic programming) | Owner subscription | **Outside** the €10 cap and outside company recurring cost, so a code backlog can't drain runtime budget. |

The old ambiguous "€30 AI cap" (which conflated server + Workspace) is retired.
The €10 figure is **model-only** runtime and is a fail-closed cap, **not**
evidenced spend.

## Hard caps (runtime)

- **€0.10 / call**, **€1 / day**, **€10 / month**.
- On exhaustion → task becomes `pending`; no direct-call bypass.
- FX: explicit operator-configured USD→EUR rate with `as_of`; missing/stale FX
  fails closed.

## Other evidenced costs (context)

- Google Workspace Business Standard ≈ **€13.80/month** (the only evidenced new
  recurring spend). Baserow / n8n / Coolify add no software fee on existing host.
- Storage Box BX11 (~€3.20/month) is **planned, not purchased**.
- GitHub Actions: **$10/month hard stop** enabled (guard the monthly budget when
  scheduling CI-heavy work).

## Ledger

The run/cost ledger records token counts, price inputs and outcome — **never**
raw model input/output. This is what makes "is AI worth it?" answerable.

## Kill / redesign criteria (from `ARCHITECTURE.md` §12)

Pause an AI skill/workflow if any hold:

- Ivan doesn't use the output.
- Review time isn't lower than doing it manually.
- It duplicates another source of truth.
- It produces activity but no client / action / outcome.
- Maintenance exceeds benefit.
- A deterministic workflow does it better.
- Cost grows without new clients or operational value.

## Spend rule (new recurring services)

Approve a new recurring cost only if it: removes a measured limitation; has an
owner and cancellation path; records an actual invoice; is reviewed at 30/90
days; and supports acquisition, delivery, or risk reduction.
