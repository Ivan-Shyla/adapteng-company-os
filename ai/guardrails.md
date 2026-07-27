# AI guardrails (non-negotiable)

Every AI action in AdaptEng obeys these. They are hard boundaries, not
preferences. They mirror the ratified platform decisions (ADR-0010 AI Gateway,
the approval outbox, ADR-0011 integrity boundary) and `ARCHITECTURE.md` §7.

## 1. Draft-first, pending-only

- The **first** model operation for a skill is a schema-valid, **side-effect-
  free `draft`**.
- The **only** approval-gated action is `external_draft.create`, limited to
  **pending/draft** Baserow state. AI can **never publish, send, email, or post**.
- A human (Ivan) is the **only** final approver for external/high-impact actions.

## 2. Never overwrite human-owned fields

- Human-owned fields are never written by a workflow or agent:
  `Content_Items.content_type`, `Systems_Automations.health` (see
  `registry/data-stores.yaml`). The governed adapter already enforces this
  (`skipped_human_owned`).

## 3. Approval is single-use and fail-closed

- Approval tokens are hash-only, one-time, expiring. A reused `call_id` fails
  closed (HTTP 409) with no second provider call. Decision + outbox are atomic.

## 4. Budget & FX fail closed

- Hard caps: **€0.10/call, €1/day, €10/month** (runtime business artifacts only).
- On cap exhaustion or missing/stale FX rate, the task becomes `pending` — a
  direct-call bypass is forbidden. USD→EUR uses an explicit operator-configured
  rate with an `as_of`; never a silently-assumed dynamic rate.

## 5. Data residency & privacy

- Model access is **EU Vertex multi-region only**. Zero-data-retention-compatible
  configuration must be verified before any real call. No training on
  inputs/outputs. See `ai/model-choices.md`.
- Project cache disabled as a live gate; no implicit caching, no Search/Maps
  grounding, no request/response logging of content, no global-endpoint fallback.

## 6. PII minimization & ledger

- The cost/run ledger stores token counts, price inputs and outcome — **not** raw
  model input/output.
- The integrity reconciler is PII-minimized and has **no AI dependency**; any AI
  summary is a separate downstream reader of its already-PII-safe findings.

## 7. Determinism beats a model

- If a deterministic workflow solves the task as well, use it. AI is added only
  where it measurably beats deterministic handling, and is paused if it doesn't
  (kill criteria, `ARCHITECTURE.md` §12).

## 8. Separation of budgets

- `dev_model_budget` (owner code-mode subscription) is separate from the €10
  runtime cap, so development token use never consumes business runtime budget.
