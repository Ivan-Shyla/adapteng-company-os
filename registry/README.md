# Registry — the living index of what exists

This folder is the **machine- and human-readable index** of the AdaptEng
operating system: every service, live workflow, data store and environment,
with its owner, repository, live status and source-of-truth.

It is an **index, not an implementation**. It never contains secrets, PII,
client data or copies of code from other repositories — only stable
identifiers, hostnames, table/workflow IDs and status. Secret *values* live in
Coolify / n8n credentials / provider consoles and are referenced here by
**name only**.

## Files

| File | What it answers |
|---|---|
| [`services.yaml`](services.yaml) | Which services exist, in which repo, on which runtime, and are they live? |
| [`workflows.yaml`](workflows.yaml) | Which n8n workflows are part of Company OS live operation, and how are they governed? |
| [`data-stores.yaml`](data-stores.yaml) | Baserow tables, Postgres schemas/migrations and Drive — with authority/precedence. |
| [`environments.yaml`](environments.yaml) | Production hosts, domains, platform and budgets (no secrets). |

## Relationship to `ARCHITECTURE.md`

[`ARCHITECTURE.md`](../ARCHITECTURE.md) is the **canonical narrative** — the
"why" and the plan. This registry is the **operational lookup** — the "what and
where", kept in a diffable structured form so a human or an AI teammate can
answer "is X live?" without re-reading the whole architecture. When the two
disagree, `ARCHITECTURE.md` §11 (Current status) wins; open a PR to fix the
registry.

## How to extend

1. Add or update a row in the relevant YAML file in the same PR that changes
   live reality (a new service, an applied migration, an activated workflow).
2. Keep every entry to identifiers and status — **never** paste a token, key,
   DSN, JWT or client value.
3. Mirror the status change into `ARCHITECTURE.md` §11 so the canonical board
   stays true.
