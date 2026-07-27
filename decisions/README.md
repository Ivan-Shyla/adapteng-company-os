# Decisions (ADRs)

Architecture Decision Records for **company-OS-level** choices — the boundaries
and policies that govern how the whole operating system fits together.

Implementation-level ADRs live with their code in
`adapteng-automation-platform/docs/decisions/` (deployment platform, Postgres
canonical layer, secrets policy, AI Gateway, integrity boundary, …). This log
does not duplicate them; it records decisions that are about the Company OS as a
whole and links out to the platform ADRs they depend on.

## How ADRs work here

- One decision per file, numbered `NNNN-short-slug.md`, using
  [`ADR-template.md`](ADR-template.md).
- Status is one of `Proposed | Accepted | Superseded by NNNN | Deprecated`.
- An ADR is immutable once `Accepted`; change direction by adding a new ADR that
  supersedes it, never by rewriting history.
- Accepting an ADR is a **governance act, not a deployment** — it changes no live
  system by itself.

## Index

| ADR | Title | Status |
|---|---|---|
| [0001](0001-company-os-is-index-not-implementation.md) | Company OS repo is an index, not an implementation | Accepted |

## Related platform ADRs (in `adapteng-automation-platform`)

| ADR | Subject |
|---|---|
| ADR-0002 | Job Monitor / Postgres canonical layer |
| ADR-0006 | Encrypted secrets in repo (secrets policy) |
| ADR-0009 | Coolify self-hosted platform |
| ADR-0010 | AI Gateway — EU Vertex canonical service |
| ADR-0011 | Integrity reconciliation boundary (read-only, deferrals) |
