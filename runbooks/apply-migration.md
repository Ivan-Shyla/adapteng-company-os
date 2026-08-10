# Runbook — apply a Postgres migration to live `adapteng_ops`

> **STOP — read before using this runbook. Verified 2026-08-10.** All nine
> logical migration units (001, 002, 003, 004, 005, 006, 007,
> `008_drive_bridge_replay_reservations.sql`,
> `008_ai_gateway_runtime_hardening.sql`) are **already applied in production**.
> The owner's post-rollout manual production check found every one of them
> exact. **None of them may be replayed.** This runbook is the procedure for a
> *genuinely new* migration only. If you are here because a note said a
> migration "remains repo-only and unapplied", that note was stale and has been
> corrected — see [`owner/action-items.md`](../owner/action-items.md) and
> [`registry/data-stores.yaml`](../registry/data-stores.yaml).
> `UNVERIFIED`: per-unit production state is owner-attested and cannot be
> re-derived from GitHub, because the rollout was manual and `Migrate Approved
> Assets` has zero runs. A read-only schema verification would settle it. Note
> that zero runs is **not** evidence of an unapplied database — that inference
> is what produced the stale note in the first place.

Governed procedure for applying a migration from
`adapteng-automation-platform/database/migrations/` to the live operational
database. Proven applying `001_id_allocator` (adapter first boot) and
`004_lead_identity` (WEB-002).

## Preconditions (all required)

1. The migration is **merged to `main`** in `adapteng-automation-platform`.
2. A fresh backup of `adapteng_ops` exists and is verified — see
   [`backup-and-restore.md`](backup-and-restore.md). **Do not skip this.**
3. The migration is **not already applied**. Confirm read-only against the live
   database first; do not infer from a document. All nine existing units are
   applied (see the banner above), so this precondition currently excludes every
   migration in the repository.
4. There is a real consumer for the migration, or an explicit owner decision to
   pre-apply. Applying schema with no consumer adds surface for no value. This
   was previously the reason `006` was recorded as intentionally kept
   **unapplied**; that is no longer the state — `006` was applied during the
   rollout and must not be replayed. What ADR-0011 still defers is `006`'s
   *wiring*, not its schema (see `registry/data-stores.yaml` and ADR-0011).

## The `search_path` trap (important)

The internal DB connects as role `adapteng_ops`, which has a **same-named
schema** `adapteng_ops` first on its `search_path`. Unqualified `CREATE TABLE`
therefore lands in `adapteng_ops`, **not** `public`. But functions like
`reserve_lead_identity` pin `search_path = pg_catalog, public`, so the objects
they use **must** live in `public`.

**Rule:** prepend `SET search_path TO public;` to the migration body (or fully
schema-qualify every object) so tables/functions land where consumers expect.
For `006`, objects are created in a dedicated `integrity` schema — qualify
accordingly.

## Procedure

1. Fetch the exact migration SQL from `main` (byte-for-byte — never hand-retype).
   PowerShell:
   ```powershell
   $encoded = (gh api `
     'repos/Ivan-Shyla/adapteng-automation-platform/contents/database/migrations/<file>?ref=main' `
     --jq .content) -join ''
   [IO.File]::WriteAllBytes(
     '<file>',
     [Convert]::FromBase64String($encoded)
   )
   ```
   POSIX shell:
   ```bash
   gh api \
     'repos/Ivan-Shyla/adapteng-automation-platform/contents/database/migrations/<file>?ref=main' \
     --jq .content | base64 -d > '<file>'
   ```
2. Confirm the backup timestamp (step 2 above) and record it.
3. Apply through a **governed path** (a one-shot n8n Postgres node against the
   internal DB credential, or the adapter's guarded `RUN_MIGRATIONS_ON_START`
   for `001`). Migrations are written idempotently (`CREATE ... IF NOT EXISTS`,
   `DO $$ ... duplicate_object ... $$`), so re-running is safe.
4. Verify: query `information_schema.tables` / `pg_proc` for the new objects in
   the expected schema; run one representative call (e.g. the function returns
   the expected outcome).
5. Update `registry/data-stores.yaml` (`live: true` + applied note) **and**
   `ARCHITECTURE.md` §11 in the same PR.

## Rollback

Migrations are additive and idempotent; prefer forward-fixing. If a migration
must be undone, restore from the pre-migration backup
([`backup-and-restore.md`](backup-and-restore.md)) rather than hand-dropping
objects, unless the drop is trivial and reviewed.
