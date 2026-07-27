# AI-001 pilot intake

This is the owner/content contract for the **first AI value** — the draft
assistant. It is necessary but no longer the only live gate: a 2026-07-26
production-readiness audit found completion-path admission/external-action and
local budget-gate defects in the control plane. A separate hardening PR must
land before a **measured, inactive** pilot on EU Vertex
`gemini-3.1-flash-lite`: **drafts only**, written as `pending/draft` into Baserow
`Content_Items` (848) through the governed adapter. It can **never publish or
send**, and a human stays the only approver.

Why this and not more: AI attaches to the already-governed spine (see
`insertion-points.md`). This file supplies the source/claims/style side of the
first pilot; the remaining technical gates are the control-plane hardening,
governed Drive path, canonical AI Gateway and measured evaluation below.

---

## First owner-approved pilot (2026-07-26)

The owner authorized use of the existing `CASE-2026-001` material and allowed a
provisional presentation/style choice. The legacy source was inventoried
read-only: one intake marker, one case note, four HEIC images and two MOV videos.
The original remains untouched; a governed copy to the company Shared Drive is
in progress.

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

### Acceptance/red lines for this pilot

- Every technical claim maps to the case note or an explicitly approved image.
- Missing model numbers, readings, uncertainty values and dates remain marked
  as missing evidence; they are never guessed.
- No client identity, personal data, GPS/EXIF, safety conclusion, legal
  compliance conclusion or performance guarantee.
- The draft must be useful after human editing and must cost less than €0.10.
- Publication remains an explicit owner action after source/media review.

## Ratification still required

The first pilot scope is now fixed: one English website article/case draft for
industrial plant engineering and maintenance readers, using only the bounded
claims above. The provisional draft may be generated and stored as
`DRAFT_NOT_APPROVED`; Ivan still performs the final source/claims/style review
before any model-assisted revision is accepted or anything is published.

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

- ZDR / no-training on EU Vertex; response cache off; FX config for the €-cap.
- Per-call ≤ €0.10, per-day ≤ €1, runtime ≤ €10/month — **fail-closed**, not
  best-effort (`environments.yaml` budgets).
- Draft-only path: write to `Content_Items` (848) as `pending/draft` via the
  governed adapter; never publish/send; human approves. Human-owned fields
  (e.g. `content_type`) are never overwritten.

## What I do once you provide the above

1. Verify Vertex IAM/ADC access for the intended runtime identity (the provided
   SA currently has evidenced Drive DWD scope, not evidenced Vertex permission),
   wire the canonical `ai-gateway`, apply migration 005 **with a backup**, and
   smoke-test with **no business data**.
2. Run an **AI-002 shadow eval** on your acceptance set (~20 cases) and report
   quality, cost/call and time-saved — **inactive**, changing no live pipeline.
3. Bring you an **AI-004 go/no-go** before AI ever touches live lead flow.

This keeps the first AI strictly on low-blast-radius drafts, inside the €10 cap,
and never inside the read-only integrity boundary (ADR-0011).
