# ADR-0001: Company OS repo is an index, not an implementation

- **Status:** Accepted
- **Date:** 2026-07-26
- **Deciders:** Ivan Shyla (AdaptEng)
- **Scope:** The `adapteng-company-os` repository and its relationship to the
  other AdaptEng repositories and live systems.

## Context

AdaptEng runs across several repositories (`adapteng-automation-platform`,
`ai-dev-loop-control-plane`, `adapteng-marketing`, `adapteng-website`) and live
systems (Coolify/Hetzner, n8n, Baserow, Postgres, Drive, Cloudways). The company
needs one place that answers "how does the company run, what is live, and what
do we do next" — without becoming a second copy of everything, a place for
secrets, or a competing plan that drifts from reality.

`ARCHITECTURE.md` already establishes: no client documents, no PII, no
passwords, no runtime dumps, no implementation copies from other repos, and
"changes update the existing master file, not a new parallel plan." This ADR
ratifies that boundary for the repository as a whole, now that the repo also
carries an operating structure (`registry/`, `runbooks/`, `decisions/`, `ai/`,
`owner/`).

## Decision

`adapteng-company-os` is the **governance and index layer** of the operating
system. It MAY contain:

- the canonical architecture narrative and plan (`ARCHITECTURE.md`);
- structured **indexes** of live reality (`registry/*.yaml`) — identifiers,
  hostnames, statuses;
- **runbooks** — repeatable operational procedures;
- **decisions** — company-level ADRs;
- the **AI program** governance (`ai/`) — where AI plugs in, guardrails, model
  choice + prices, cost controls;
- **owner action pins** (`owner/`) — the things only the owner can do, and an
  access map by **name only**.

It MUST NOT contain:

- secrets or credential **values** (tokens, keys, DSNs, JWTs, service-account
  JSON) — reference them by name;
- PII or client data;
- runtime dumps or logs;
- copies of implementation code from other repositories;
- a parallel/competing plan — the plan lives once, in `ARCHITECTURE.md`, and is
  updated in place.

When the index and the canonical narrative disagree, **`ARCHITECTURE.md` §11
(Current status) is authoritative**; the index is corrected by PR to match.

## Alternatives considered

- **Monorepo of everything** — rejected: mixes governance with implementation,
  invites secret/PII leakage, and couples release cycles.
- **Only `ARCHITECTURE.md`, no structure** — rejected: prose alone cannot be
  queried ("is X live?") or operated (no runbooks), which was the observed gap.
- **A separate wiki/Notion** — rejected: not diffable, not PR-reviewed, drifts
  from code and evidence.

## Consequences

- **Positive:** one authoritative, diffable, PR-reviewed operating system; fast
  lookups; repeatable operations; safe to give an AI teammate read access
  because there are no secrets or client data.
- **Negative / cost:** the index must be updated in the same PR as any live
  change, or it drifts. Mitigated by the weekly status workflow and the update
  protocol (`ARCHITECTURE.md` §12).
- **Neutral:** structured YAML indexes could later be validated in CI (schema
  lint) — a future enhancement, not required now.

## Compliance

- No secrets in repo (ADR-0006, automation-platform) — enforced by "names only".
- No unreviewed live change — this repo changes docs/indexes, not live systems.
- Single source of truth — `ARCHITECTURE.md` remains canonical (§12: "Do not
  create `ARCHITECTURE_v3`, amendment or competing plan").
