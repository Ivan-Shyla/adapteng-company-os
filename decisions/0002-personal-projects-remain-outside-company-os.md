# ADR-0002: Personal projects remain outside Company OS

- **Status:** Accepted
- **Date:** 2026-07-28
- **Deciders:** Ivan Shyla (AdaptEng)
- **Scope:** Company-OS-level exclusion governance. This ADR governs company
  ownership only; it does not index personal implementation or runtime decisions.

## Context

Job Monitor and other personal job/vacancy-search automation, English Coach and
other personal English-learning automation, and Kraken/personal trading are
personal projects. Their names appear here solely to define exclusion from
Company OS. This ADR does not catalogue their implementation, runtime, data,
status or decisions.

Company systems may still have shared credential or store dependencies with an
excluded project. The company must remove that exposure without taking
ownership of, copying or operating the personal project.

## Decision

The named personal projects are permanently excluded from Company OS. Company OS
MUST NOT own, operate, migrate or represent them in:

- the company Shared Drive, Baserow or Postgres;
- self-hosted n8n or AI employees;
- company credentials, budgets or cost caps;
- company source manifests, service/system rows or operational roadmap; or
- any company cutover, backup, restore or approved-source migration.

Existing shared n8n credentials and store dependencies are separated only to
protect the company boundary. Separation MUST NOT export, import or copy
personal workflows or data into a company-controlled system.

Company OS MAY retain only aggregate exclusion and isolation evidence, such as
the count of excluded workflows and whether company credentials/stores are
isolated. It MUST NOT retain personal workflow definitions, records, payloads or
project status. A data-free generic engineering pattern may be reused only
through a separate reviewed decision that establishes provenance and company
need; exclusion creates no standing permission to reuse it.

This boundary is narrow. English remains AdaptEng's working and public business
language, and legitimate regulated emissions-trading engineering content remains
valid company work. The exclusions apply to the personal English Coach and
Kraken/personal-trading projects, not to those company activities.

## Alternatives considered

- **Migrate personal workloads on a separate lane** — rejected: it would make
  Company OS responsible for personal operations and data.
- **List personal workflows as `Systems_Automations` rows** — rejected: the
  company interface and manifests must contain company systems only.
- **Ignore the shared bindings** — rejected: a shared credential/store can allow
  personal activity to consume company budget or reach company data.

## Consequences

- **Positive:** company authority, spend and data boundaries are explicit; shared
  dependency remediation cannot become a personal-workflow migration.
- **Negative / cost:** detailed personal inventory and remediation records must
  remain outside Company OS; this repository can show only aggregate isolation
  evidence.
- **Neutral / follow-up:** company-side isolation work may prove the boundary,
  but it does not add a personal project to the Company OS roadmap.

## Compliance

This decision authorizes no live action, write, migration, deployment or
credential change. It contains no personal records, PII, secrets or opaque IDs.
Any future data-free pattern reuse requires its own reviewed decision. Any
company runtime or storage change requires a separately approved implementation.
