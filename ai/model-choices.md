# Model choices & prices

**Standing rule:** re-verify prices before any enablement — model prices move.
Figures below verified **2026-07** (sources at the bottom).

## Decision

For AdaptEng's constraint set — **EU data residency, no training on our data,
zero-data-retention** — the optimal first pilot model is **Vertex AI
`gemini-3.1-flash-lite` (EU multi-region)**. It is residency-compliant *and*
among the cheapest capable models. `gemini-2.5-flash-lite` is an even cheaper
Google/EU option for pure classify/extract where quality suffices. Non-EU-native
models are **fallback-only**, allowed only after equal quality **and** an
approved EU data-processing configuration.

This matches Gate-0 (2026-07-25) and ADR-0010 (AI Gateway = EU Vertex canonical).
It is a model selection, not runtime readiness: the current business-AI verdict
is **REJECT_LIVE**.

### Official chosen-candidate availability and rates

Re-verified from official Google documentation on 2026-07-27:

| Item | Verified value |
|---|---|
| Availability | **GA**; supported in the EU multi-region |
| Location | `eu` |
| Endpoint | `https://aiplatform.eu.rep.googleapis.com` |
| Standard non-global text/image/video input | **$0.275 / 1M tokens** |
| Standard non-global audio input | **$0.55 / 1M tokens** |
| Standard non-global cached text input | **$0.0275 / 1M tokens** |
| Standard non-global text output/reasoning | **$1.65 / 1M tokens** |

## Verified prices (per 1M tokens, 2026-07)

| Model | Input | Output | Residency | Verdict |
|---|---:|---:|---|---|
| **Vertex `gemini-3.1-flash-lite`** | **$0.275** | **$1.65** | EU multi-region (non-global price) | **GA — chosen first-pilot candidate** |
| Vertex `gemini-2.5-flash-lite` | $0.10 | $0.40 | EU multi-region | Cheaper Google option for classify/extract |
| OpenAI `gpt-5-mini` | $0.20 | $1.00 | US / regional* | Fallback-only (not EU-native) |
| Anthropic `claude-haiku-4.5` | $1.00 | $5.00 | US / global | Fallback-only (pricier, not EU-native) |

\* OpenAI regional processing requires approved data controls and ~10% uplift;
Anthropic first-party inference is US/global. Both are fallback-only under our
residency rule, despite `gpt-5-mini` being the raw-cheapest.

### Why not use a lower sticker-price US endpoint?

Raw price is not the objective — **compliant** price is. Our data must stay in
the EU with no training/retention. `gemini-3.1-flash-lite` on EU Vertex meets
that at the required quality tier; a few tenths of a dollar per million tokens
saved on a US endpoint is not worth breaching residency for client-adjacent
content. `gemini-2.5-flash-lite` remains the lower-cost EU option when the
ratified classify/extract quality set shows it is sufficient.

## Representative call cost

Reference workload: **20k input + 4k output** (a draft).

| Model | Input cost | Output cost | Per call (before FX) | Calls within €10/mo* |
|---|---:|---:|---:|---:|
| `gemini-3.1-flash-lite` | $0.0055 | $0.0066 | **≈ $0.0121** | ~825 |
| `gemini-2.5-flash-lite` | $0.0020 | $0.0016 | **≈ $0.0036** | ~2,700 |

The reference uses no cache or audio: 20,000 text tokens × $0.275/M = $0.0055;
4,000 text-output tokens × $1.65/M = $0.0066; total **$0.0121 before FX**.

\* Illustrative, USD≈EUR at parity for sizing only; any future live gateway must
use an explicit operator-configured FX rate with `as_of` and fail closed if it
is missing or stale. Well under the **€0.10/call** cap either way. Batch API
(−50% on tokens) is available but not assumed.

## Gates before the first real call (all required)

1. AG-008 closes the optional/unvalidated-envelope,
   missing-`no_external_action`/synthetic-approval and over-cap/negative-budget
   P0 bypasses.
2. Persistent Postgres cost reservation/reconciliation is deployed as the
   authority; the in-memory `ModelGateway` is never production authority.
3. Real EU Vertex client + GCP service account are wired into `ai-gateway`.
4. Drive adapters, orchestration, canonical approval and deployment are proven.
5. Zero-data-retention-compatible Vertex config is verified; explicit FX rate +
   `as_of` are configured; caps are active (€0.10 / €1 / €10); project cache is
   **disabled**; no grounding / no request-response logging.
6. `AG-007` / `AI-001` quality, citation and safety proof is accepted by the
   owner.

Until all six hold, the gateway stays repo-merged-not-live and no business task
may call a model; any pre-gate artifact remains deterministic and pending-only.

## Sources (2026-07)

- Vertex AI generative AI pricing — <https://cloud.google.com/vertex-ai/generative-ai/pricing>
- Vertex AI model availability — <https://cloud.google.com/vertex-ai/generative-ai/docs/models>
- Vertex AI generative AI locations/endpoints — <https://cloud.google.com/vertex-ai/generative-ai/docs/learn/locations>
- Gemini 3.1 Flash-Lite launch — <https://blog.google/innovation-and-ai/models-and-research/gemini-models/gemini-3-1-flash-lite/>
- Independent price comparisons (BenchLM, Metacto, IntuitionLabs), July 2026.
- Vertex data residency / ZDR — <https://cloud.google.com/vertex-ai/docs/general/data-residency>
