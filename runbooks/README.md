# Runbooks — repeatable operational procedures

Step-by-step procedures for operating the AdaptEng Company OS safely. A runbook
either captures a task performed against live systems or marks an approved
fail-closed procedure as **proposed, not configured** until its owner evidence
exists. Never infer live capability from documentation alone.

**Golden rules**

- Live systems change only through a governed, evidenced procedure — never an
  ad-hoc console poke.
- Back up before any migration or destructive change (see
  [`backup-and-restore.md`](backup-and-restore.md)).
- Secrets never enter this repository. Rotate in the provider, reference by
  name (see [`secret-rotation.md`](secret-rotation.md)).
- Prefer idempotent, retry-safe operations that fail closed.

## Index

| Runbook | Use when |
|---|---|
| [`company-drive.md`](company-drive.md) | Deciding where photos, case files, drafts and approved artifacts belong; migrating safely from personal Drive. |
| [`n8n-operations.md`](n8n-operations.md) | Building, activating, or debugging a governed n8n workflow via the API. |
| [`apply-migration.md`](apply-migration.md) | Applying a Postgres migration to live `adapteng_ops`. |
| [`backup-and-restore.md`](backup-and-restore.md) | Proposed physical/WAL backup, isolated restore and exact 007/Drive-008 rehearsal for `adapteng_ops`; not configured until owner evidence says otherwise. |
| [`secret-rotation.md`](secret-rotation.md) | Rotating a Baserow / n8n / Coolify / provider token. |
| [`incident-response.md`](incident-response.md) | A webhook, adapter, or workflow is misbehaving in production. |
