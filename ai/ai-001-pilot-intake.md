# AI-001 pilot intake

The single artifact that unblocks the **first AI value** — the draft assistant.
Fill this in and I run a **measured, inactive** pilot on EU Vertex
`gemini-3.1-flash-lite`: **drafts only**, written as `pending/draft` into Baserow
`Content_Items` (848) through the governed adapter. It can **never publish or
send**, and a human stays the only approver.

Why this and not more: AI attaches to the already-governed spine (see
`insertion-points.md`). The only thing missing is *your* ratified content — I must
not invent claims, style, or sources for a real engineering company.

---

## What I need from you (owner)

1. **Claims register** — the factual claims AdaptEng may state (services,
   capacities, certifications, guarantees). One row per claim; the AI may use
   **only approved** claims and may never invent a capability.
2. **Style guide** — tone, voice (we / AdaptEng), language(s) (EN / CS / RU?),
   do/don't words, length norms per artifact type.
3. **2–3 real source documents** — actual case/opportunity material for the first
   drafts. Upload to the Shared Drive and give the links (or Baserow
   `Content_Items` / case ids).
4. **Artifact scope** — which first: **case write-up**, **article**, or both? And
   the target audience.
5. **Acceptance set (AG-007)** — 5–10 worked examples of "good" (input → an
   acceptable draft) plus **red-lines**: claims/safety/citation rules a draft must
   never violate.

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

1. Wire `ai-gateway` to EU Vertex using the provided service account
   (`GOOGLE_SERVICE_ACCOUNT_JSON_B64`), apply migration 005 **with a backup**, and
   smoke-test with **no business data**.
2. Run an **AI-002 shadow eval** on your acceptance set (~20 cases) and report
   quality, cost/call and time-saved — **inactive**, changing no live pipeline.
3. Bring you an **AI-004 go/no-go** before AI ever touches live lead flow.

This keeps the first AI strictly on low-blast-radius drafts, inside the €10 cap,
and never inside the read-only integrity boundary (ADR-0011).
