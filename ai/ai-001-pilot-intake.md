# AI-001 pilot intake

This is the owner/content contract for the **first AI value** — the draft
assistant. It separates two firsts that must not be conflated:

1. `CASE-2026-001` is the first governed raw-source/case migration and
   evidence-bounded deterministic case draft. It is **not** the first live model
   proof. Its media and publication are fail-closed pending live Sheet-vs-Git
   redaction reconciliation.
2. `ART-2026-001`, using approved public source set `SRC-2026-001`, is the exact
   already-approved/published July article-radar package selected for the first
   live model-backed Company Drive proof. Reuse does not authorize
   republication.

No live model call has run; readiness is **REJECT_LIVE**. Control-plane main
`affe6ea1e4d522be0df0641e98a08e20a84549ae` contains deterministic
AG-001/002/003/006/007 only, with no business worker, real provider or Drive
runtime. The audit reproduced an optional/unvalidated envelope, completion
accepting missing `no_external_action` plus synthetic `approval_id`, and the
in-memory `ModelGateway` allowing actual cost above cap and negative remaining
budget. AG-008 owns deterministic fixes; automation-platform must still provide
the persistent business runtime. Only after both layers are accepted may one
**measured, inactive** EU Vertex `gemini-3.1-flash-lite` proof create a
`pending/draft` through the governed adapter. It can **never publish or send**,
and a human stays the only approver.

Why this and not more: AI attaches to the already-governed spine (see
`insertion-points.md`). The CASE contract below governs deterministic case
migration/drafting; the exact public ART/SRC pair governs the live model proof.
The remaining technical gates are control-plane hardening, the governed Drive
path, canonical AI Gateway and measured evaluation below.

---

## First governed case migration and deterministic draft (2026-07-26)

The owner authorized use of the existing `CASE-2026-001` material and allowed a
provisional presentation/style choice. The legacy source was inventoried
read-only: one intake marker, one case note, four HEIC images and two MOV videos.
The original remains untouched; a governed copy to the company Shared Drive is
in progress. A later live Sheet record conflicts with the repository redaction
record, so all six media files and any CASE publication remain blocked until a
human reconciles them. The evidence-bounded deterministic text draft remains an
internal draft.

### Approved source-bounded claims

1. The work was a service visit at an aluminum plant to confirm measurement
   quality through parallel measurements.
2. Two portable analyzers were connected to the existing MKAS gas-analysis
   system for the comparison.
3. The instruments use different measurement principles and produced aligned
   results within their stated measurement uncertainty.
4. The source identifies Endress+Hauser / SICK equipment in the MKAS system and
   a Wöhler portable analyzer; exact model numbers are not yet evidenced.
5. The public client description is limited to **an aluminum plant**. No client
   name, location, date, measured values or regulated-component claim may be
   invented.

These are case-specific claims, not blanket claims of accreditation,
certification, regulatory compliance, guaranteed accuracy or laboratory status.

### Provisional style and artifact

- Primary language: English; factual engineering voice; short paragraphs.
- First artifact: website article/case draft for industrial plant engineering
  and maintenance readers, with a restrained LinkedIn derivative later.
- Structure: client need → parallel-measurement method → bounded result → why
  independent verification matters → factual CTA.
- Use vendor names only where technically relevant and trademark them
  accurately. Never imply vendor endorsement.
- All photos/video require human privacy/redaction review before external use,
  even though the owner approved the source set for drafting.
- Output remains `draft`; no model/workflow may approve, publish or send it.

### Acceptance/red lines for this deterministic case draft

- Every technical claim maps to the case note or an explicitly approved image.
- Missing model numbers, readings, uncertainty values and dates remain marked
  as missing evidence; they are never guessed.
- No client identity, personal data, GPS/EXIF, safety conclusion, legal
  compliance conclusion or performance guarantee.
- The draft must be useful after human editing and must cost less than €0.10.
- Media use and publication remain blocked until the live Sheet-vs-Git
  redaction state is reconciled; owner approval cannot bypass that evidence gap.

## Exact first live model-backed Company Drive proof

Use `ART-2026-001` with approved public source set `SRC-2026-001` (US EPA EMC /
40 CFR Part 60 Appendix F, Procedure 1). This July article-radar package already
has a source review, approved/published article draft, quality review and
WordPress package in the marketing repository. Marketing PR #20 pins this
selection.

The package is reused only as stable public evidence for one controlled model
proof through the governed Company Drive path. The new output remains
`DRAFT_NOT_APPROVED`; the historical publication does not authorize
republication. The proof uses no CASE media or client data. It must enter
through the canonical Company OS gateway and AG-008; frozen direct-model
workflow MM-22 must never be reactivated or bypassed.

## Ratification still required

The CASE deterministic-draft scope is fixed to one English website case draft
for industrial plant engineering and maintenance readers, using only its
bounded claims above. The first live model-backed proof is separately fixed to
the public `ART-2026-001`/`SRC-2026-001` package. Ivan still ratifies the
`AG-007` acceptance set before the model proof; every generated artifact remains
`DRAFT_NOT_APPROVED`.

Broader program inputs are not blockers for this first controlled draft:

1. **Company claims register** — factual AdaptEng services, capacities,
   certifications and guarantees. One row per claim; the AI may use **only
   approved** claims and may never invent a capability.
2. **Reusable style guide** — extend the provisional English pilot style to
   channel/language rules after the first review.
3. **Additional sources** — add two more real source documents only after this
   controlled path proves copy, evidence links, review and replay safety.
4. **Acceptance set (AG-007)** — prepare 5–10 worked examples from approved
   sources; Ivan ratifies the set before a live model evaluation is allowed to
   influence a business draft.

## Shape (so it's directly usable)

- `claims.csv` — columns: `claim,evidence_url,approved(y/n)`
- `style.md` — prose
- sources — Drive links or `Content_Items` / case ids
- `acceptance` — 5–10 items of `{ input → acceptable draft, red-lines }`

## Gates I verify before any live model call

- Input is exactly public package `ART-2026-001`/`SRC-2026-001`; no CASE media,
  client data or unreconciled source enters the call.
- AG-008 closes the envelope, `no_external_action`, synthetic approval and
  over-cap/negative-budget bypasses deterministically.
- Persistent Postgres cost reservation/reconciliation is the budget authority;
  the in-memory `ModelGateway` is never used as production authority.
- The real EU Vertex adapter, Drive adapters, orchestration, canonical approval
  composition and deployment are wired and proven in automation-platform.
- ZDR / no-training on EU Vertex; response cache off; FX config for the €-cap.
- Per-call ≤ €0.10, per-day ≤ €1, runtime ≤ €10/month — **fail-closed**, not
  best-effort (`environments.yaml` budgets).
- Draft-only path: write to `Content_Items` (848) as `pending/draft` via the
  governed adapter; never publish/send; human approves. Human-owned fields
  (e.g. `content_type`) are never overwritten.

## What I do once the gates pass

1. Verify Vertex IAM/ADC access for the intended runtime identity (the provided
   SA currently has evidenced Drive DWD scope, not evidenced Vertex permission);
   deploy the canonical Postgres cost authority, EU Vertex/Drive adapters,
   orchestration and approval composition; apply migration 005 **with a
   backup**; and smoke-test with **no business data**.
2. Run one measured, inactive `ART-2026-001`/`SRC-2026-001` proof and record its
   model, cost, evidence and pending/draft Company Drive artifact.
3. Run an **AI-002 shadow eval** on the ratified acceptance set (~20 cases) and
   report quality, cost/call and time-saved — changing no live pipeline.
4. Bring you an **AI-004 go/no-go** before AI ever touches live lead flow.

This keeps the first AI strictly on low-blast-radius drafts, inside the €10 cap,
and never inside the read-only integrity boundary (ADR-0011).
