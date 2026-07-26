# AI insertion points

Where the AI agent plugs into the live operating system to create value, in
priority order. Each point names the **value**, the **governed path** it rides,
the **gate** that must be satisfied, and its **status**.

Principle: AI attaches to the **already-governed spine** (baserow-adapter +
Postgres authority + governed n8n workflows). It never introduces a new
ungoverned write path, and a human remains the only final approver for
external/high-impact actions.

---

## 1. Content & Case Draft assistant (AI-001) — FIRST value

- **Value:** turn source material + a case/opportunity into a first **draft**
  (article, case write-up) so Ivan edits instead of writing from scratch.
- **Governed path:** `ai-gateway` (EU Vertex) produces a schema-valid, side-
  effect-free `draft` artifact → written as **pending/draft** into Baserow
  `Content_Items` (848) via the governed adapter. The first approval-gated
  action is `external_draft.create`, limited to pending/draft state — it can
  **never publish or send**.
- **Gate (owner):** ratified claims, style guide, and 2–3 source documents;
  `AG-007` quality/citation/safety proof; privacy (ZDR), cache-off and FX gates
  verified. See `owner/action-items.md`.
- **Status:** `AI-001` merged (marketing PR #19, deterministic, 106 tests);
  **no real model call yet**. This is the next AI step once inputs are ratified.

## 2. Lead triage / enrichment on WEB-002 — SECOND

- **Value:** on `lead.created`, infer/normalize `service_line`, language, and a
  suggested priority so the one-day follow-up Action is sharper.
- **Governed path:** an **advisory** classify/extract step feeding the existing
  governed upsert. Output is a suggestion on a non-human-owned field or a note;
  it must not overwrite human-owned fields and must degrade gracefully (lead
  intake already works deterministically without it).
- **Gate:** AI-001 proven first; classify/extract quality measured; stays within
  the runtime cap; never blocks the deterministic pipeline.
- **Status:** design-only. WEB-002 runs deterministically today; AI here is an
  enhancement, not a dependency.

## 3. Integrity findings summary (downstream of INT-001) — THIRD

- **Value:** a plain-language summary of drift findings so review is faster.
- **Governed path:** a **separate, downstream** read of the reconciler's
  **PII-safe findings artifact**. It is explicitly **outside** the reconciler —
  ADR-0011 forbids any AI Gateway dependency inside `integrity-reconciler`.
- **Gate:** INT-001 actually running (owner/approved-PR gated: live schedule,
  credentials incl. Drive service account); summary is advisory only.
- **Status:** blocked upstream (INT-001 not live by design). Do not wire AI into
  the reconciler.

## 4. Agent code mode (ai-dev-loop) — already in use

- **Value:** building/maintaining the platform itself (this work).
- **Budget:** owner `dev_model_budget` (code_change) — **outside** the €10
  runtime cap, so a code backlog can't exhaust the business runtime budget.
- **Status:** in use for development; not a business-runtime cost.

---

## Sequencing (why this order)

`AG-007` eval harness → `AI-001` quality proof → `AI-002` 20-case shadow eval →
`AI-003` accept/edit/reject + time-saved capture in Baserow → `AI-004` owner
go/no-go. Value is proven on drafts (low blast radius) before AI touches live
lead flow, and never inside the read-only integrity boundary.
