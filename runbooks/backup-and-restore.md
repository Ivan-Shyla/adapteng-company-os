# Runbook — back up and restore `adapteng_ops`

`adapteng_ops` (Postgres, internal Coolify) is the operational truth — id
allocation, lead reservation, run/approval ledger. Back it up before any
migration or destructive change.

## Back up (before every migration)

1. Trigger a database backup for the `adapteng_ops` service in **Coolify**
   (the managed backup for the Postgres resource).
2. Verify the backup **completed** and note: timestamp, status, size.
   - Last verified: **2026-07-25 13:31 — Success — 35.21 KB** (Coolify local
     copy preserved + an additional copy downloaded by the owner).
3. Keep at least one copy **off-host** (owner download or Storage Box). A backup
   that only exists on the same host is not a real backup.
4. Record the backup reference in the migration PR before applying.

## Restore (recovery / rollback)

1. Stop consumers that write to the database (adapter, governed workflows) to
   avoid writes racing the restore.
2. Restore the chosen backup into the Postgres resource via Coolify.
3. Verify object presence and a representative read (allocator sequence,
   `lead_identity_reservation`) before re-enabling consumers.
4. Re-enable consumers; run a synthetic canary and read it back.

## Notes

- The reservation authority (`public.lead_identity_reservation`) is
  **append-only** by design — reservations are never released. A restore rewinds
  it to the backup point; ensure that's intended.
- A full restore **drill** (restore into a scratch target and prove readability)
  is part of the §13 Definition of Done and is still owed by the owner — see
  `owner/action-items.md`.
