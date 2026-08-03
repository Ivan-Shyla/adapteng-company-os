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
- **Gate:** ratified claims/style/sources; `AG-007` quality proof; a **durable
  cost authority that survives process restart** (persistent Postgres cost
  reservation/reconciliation with cross-process spend accounting and latch
  persistence); real EU Vertex and Drive adapters; orchestration, canonical
  approval and deployment; privacy (ZDR), cache-off, Vertex IAM and FX.
  Never use the local in-memory test seam as budget authority — that warning
  **stands unchanged**, and P0 #3 below is precisely why it is not obsolete.
  AG-008 has closed the envelope and no-external-action items at the control
  plane. The **cost** item is closed only *within a process* and is therefore
  **not** a durable authority, so this gate remains unsatisfied. See the status
  below and `owner/action-items.md`.
- **Status:** **REJECT_LIVE** — judged per P0 below, not as one global verdict.
  Re-assessed **2026-08-03** against control-plane main
  `edadb09125f7fb5d173d5f595181d1384050b6b5`, which is the merge commit of
  control-plane **PR #39** (*fix(validate_json): fail closed on duplicate keys
  and non-finite constants*, merged 2026-08-02). The fail-closed hardening
  actually under judgement here landed one commit earlier, at `c6a5b509` —
  control-plane **PR #38**, *AG-008: fail-closed business artifact safety
  hardening*, merged 2026-07-30. This **supersedes** the 2026-07-25 assessment
  pinned to `affe6ea1e4d522be0df0641e98a08e20a84549ae` (control-plane PR #36),
  which is kept visible here because the record is corrected in place, not
  erased.

  **Read the invariant, not the hash.** The claim here is *"P0 #1 and #2 are
  closed as of AG-008, which is merged on control-plane `main`"*; the SHAs
  above are evidence anchors showing where and when that was established, not
  the claim itself. A later control-plane merge therefore leaves this
  assessment stale-*dated* at worst — it does not falsify it, and it should
  not be read as a verification that failed. What would falsify it is AG-008
  being reverted, or the durable-cost gap in P0 #3 below being closed.

  **Evidence, and exactly what is merged.** The verdicts below come from
  control-plane **PR #40**, which executed each original failure at `affe6ea`
  and re-ran the identical probe at `edadb091`, re-sealing mutated artifacts
  with each tree's own `evidence_digest()` / `artifact_envelope_sha256()` so a
  refusal reflects the policy under test rather than a stale hash. No model
  call, no spend. **PR #40 is unmerged**, so its merge state must be read
  precisely: it changes no source file — only `README.md`, agent logs,
  `context/CURRENT_STATUS.md`, `docs/AG008_P0_AUDIT.md` and tests — therefore
  the closures below are properties of control-plane **`main` at `edadb091`**,
  delivered by the already-merged PR #38, and are *not* contingent on PR #40
  landing. What is not yet on `main` is the regression guard
  `tests/test_ag008_p0_regression.py`: until PR #40 merges, `main` has the
  fixed behaviour with **no test pinning it there**, so these closures are
  currently unprotected against silent re-introduction. Verdicts on the three
  P0s this file previously held open:
  - **P0 #1 — optional/unvalidated task envelope: CLOSED.** At `affe6ea`,
    `evaluate_artifact` returned `ready=True` with no envelope at all and
    `check_task_completion.py` exited `0`; the help text read
    `--envelope ENVELOPE  Optional business task envelope JSON.` At
    `edadb091` the same call fails with
    `business_artifact task requires --envelope for admission`, and
    "Optional" is gone from the help text.
  - **P0 #2 — completion accepting missing `no_external_action` plus a
    synthetic `approval_id`: CLOSED.** The most serious sub-case: at
    `affe6ea`, `validate_business_artifact_completion` read its inputs off a
    caller-supplied object via `getattr`, so an object merely declaring the
    right attributes `True` passed completion **with no artifact existing at
    all**. At `edadb091` the refusal is explicit —
    `missing required key 'no_external_action'` and
    `unexpected key 'approval_id'`.
  - **P0 #3 — over-cap actual cost driving the budget negative: PARTIAL, i.e.
    NOT closed.** The immediate defect is fixed: at `affe6ea` a €500 actual
    against a €10 cap was declared "within runtime_model_cap", drove
    `runtime_remaining_eur` to **-490.0** and released the output; at
    `edadb091` it is refused before any spend is applied, `task_state` is
    `reconciliation_required`, the output is withheld and follow-up calls are
    denied. **The remaining gap is a mechanism, not an unfinished task.**
    Re-constructing `ModelBudget` from the same config resets accumulated
    spend to zero *and* clears the `reconciliation_required` latch — measured
    as `call 1 allowed: True | spent now: 2.5`, then, from a new
    process-equivalent budget built from that same config, `spent: 0` and
    `reconciliation_required carried over?: False`. So any process restart
    erases both the spend total and the refusal that is supposed to protect
    it. Stated exactly: **the in-repo gateway is correct *within* a process; it
    is not an authority *across* processes.** This cannot be ticked off inside
    the control plane: the budget is held in process memory, that repository
    holds no database driver and no durable
    spend store by design — a codebase-wide search there for `psycopg` /
    `postgres` / `sqlalchemy` / `DATABASE_URL` returns nothing — and its only
    ledger is an append-only JSONL run ledger, which is not a monetary
    authority. The gateway's own refusal string, *"authoritative
    reconciliation required"*, is the code correctly deferring to a store that
    does not exist in that repository.

  Two properties of that audit are recorded here because they are what make
  it re-checkable: P0 #3 was judged on raw `runtime_spent_eur` and **not** on
  `runtime_remaining_eur`, which is clamped with `max(ZERO_EUR, ...)` and
  would have shown a false green; and an earlier probe that was refused at
  *admission* and so never reached the cost path was **discarded as
  inconclusive** rather than recorded as a pass.

  **What now blocks AI-001** is therefore narrower and fully specified. It is
  no longer "three deterministic P0s plus runtime": it is **a durable cost
  authority that survives process restart**. That cannot be built in the
  control plane, which has no persistent store by design; it lives in
  `adapteng-automation-platform` (`005_ai_gateway.sql` and
  `008_ai_gateway_runtime_hardening.sql`), together with the EU Vertex adapter
  and the Drive adapters. Those two migrations were confirmed to *exist* and
  were **not** exercised, so this is an **open ask against that repository, not
  a defect finding about it** — a different claim with a different owner and a
  different evidence bar. Durable spend authority is therefore not a proven
  capability here. Status stays `REJECT_LIVE`: two of three P0s closing does
  not authorize a live model call, and P0 #3 is explicitly PARTIAL. AG-008's
  deterministic fixes have landed on control-plane `main` via PR #38;
  automation-platform still owns the persistent runtime. Scope note: PR #40
  re-tested the three P0s only — the absence of a business worker, real
  provider and Drive runtime is carried forward from the 2026-07-25 assessment
  and was not re-verified.

  `AI-001` is merged (marketing PR #19, deterministic, 106 tests), but **no real
  model call has run**. Exact public package `ART-2026-001`, using source
  set `SRC-2026-001`, is selected for the first live model-backed Company Drive
  proof; its prior approval/publication does not authorize republication.
  `CASE-2026-001` is separately the first governed raw-source/case migration and
  evidence-bounded deterministic draft, with media/publication blocked pending
  live Sheet-vs-Git reconciliation. The 2026-07-26 production audit blocks the
  proof until both deterministic and persistent runtime blockers close; the
  deterministic side is now closed as recorded above, so the persistent
  runtime blocker is the one that remains. The proof must enter through the
  canonical gateway and AG-008 and must never use frozen direct-model workflow
  MM-22.

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
