# Owner action items

Prioritized. Each item says **why**, **how**, and **who/where**. Check them off
in a status PR as you complete them, and mirror the change into
`ARCHITECTURE.md` §11.

Legend: 🔴 security / do first · 🟠 data hygiene · 🟡 unblock next steps · ⚪ standing / DoD

---

## 🔴 Security — do first

- [x] **Revoke the leaked Baserow token `acJgo3…`.** ✅ **DONE 2026-07-26** — owner
  revoked it at source (Baserow → API tokens). It no longer authenticates.
- [x] **Revoke the temporary cleanup token `temporary-test-cleanup-2026-07-26`.**
  ✅ **DONE 2026-07-26** — independent API verification now returns HTTP 401.
- [ ] **Rotate the self-hosted n8n public API management key after launch work.**
  It was supplied through chat for the governed workflow build. Revoke it in
  n8n, issue a new operator key only if continued API administration is needed,
  and keep the replacement in the password manager/runtime secret store — not
  Git or chat. Existing encrypted workflow credentials/webhooks do not depend
  on this management key, so rotation should not interrupt AUT-001/WEB-002.
- [ ] **Retire credentials in legacy `PalinaRuban/adapteng`.** The historical
  WordPress snapshot tracks `wp-config.php` with database settings/salts and a
  stale Azure deployment workflow references a publish-profile secret. Confirm
  production no longer depends on them, rotate DB credentials/WordPress salts/
  Zoho app password/Azure profile at the providers, remove repository secrets,
  disable the stale workflow, then archive the repo. A normal commit cannot
  revoke values already present in Git history.

## 🟠 Data hygiene — synthetic test rows

- [x] **Delete synthetic Baserow rows.** ✅ **DONE 2026-07-26** — removed 14 rows via
  the temporary cleanup token and verified by read-back: WEB-002
  `AE-{ORG,PER,OPP,ACT}-100002/3/4` (Organizations 842 / People 843 /
  Opportunities 844 / Actions 846) and canary `AE-ORG-0001/0002`. The legitimate
  `AE-SYS-baserow-adapter` registry row (Systems_Automations 849) was left intact;
  only Baserow's two default-empty rows remain per table.
  - The Postgres reservation rows (`900:1`–`903:1`) are PII-free append-only
    identity markers; left in place deliberately.

## 🟡 Unblock the next build steps

- [x] **Provide Google service-account credentials.** ✅ **DONE 2026-07-26** — SA
  `adapteng-ai-operator@adapteng-workspace-automation.iam.gserviceaccount.com`
  (project `adapteng-workspace-automation`), domain-wide delegation for
  `https://www.googleapis.com/auth/drive`, delegated to the owner's primary
  Workspace user. Key stored as the Coolify runtime secret
  `GOOGLE_SERVICE_ACCOUNT_JSON_B64` (value never in repo). This unblocks the
  **deploy-time** wiring of `drive-adapter` and the INT-001 Drive reader — the
  *live wiring itself* still rides the INT-001 approved-PR gates below.
- [ ] **INT-001 (integrity) — approve the deferred wiring, one PR at a time.**
  ADR-0011 defers each of these to a *future approved PR*: live schedule, the
  Finding→Action adapter, the n8n workflow, live manifest wiring, and deployed
  credentials. Keep **migration 006 unapplied** until backup/restore planning.
  Nothing here should be forced by an agent.
- [ ] **AI-001 first-pilot ratification.** `CASE-2026-001`, its bounded claims,
  English website-article scope and provisional style are already recorded in
  [`ai/ai-001-pilot-intake.md`](../ai/ai-001-pilot-intake.md); no blank
  questionnaire or extra sources are required to create the first
  `DRAFT_NOT_APPROVED`. Before a live model-assisted revision, Ivan reviews that
  pilot contract and ratifies the `AG-007` acceptance set. Technical gates remain
  ZDR/cache-off/FX verification plus the 2026-07-26 control-plane
  admission/no-external-action/cost hardening. Then a measured, inactive EU
  Vertex `gemini-3.1-flash-lite` evaluation may run; publication remains a
  separate human decision.
- [ ] **Website producer (website PR #78)** stays draft/held. WEB-002 and migration
  004 are already live; remaining producer gates are exact self-hosted endpoint
  allowlisting, host-only `X-Webhook-Token`, bounded 5xx/transport retry,
  409 dead-letter/manual review, Fluent Forms retention/reconciliation, dark
  feature-flag deployment, actual WordPress-producer E2E and rollback. The PR
  auto-deploys `wp-content/**`, so these cannot be deferred until after merge.
- [ ] **self-hosted n8n cutover:** repoint the Coolify source from branch
  `palinaruban-repo-status-review` to `main`, verify auto-deploy, then complete
  the inactive shadow. n8n Cloud remains the authority for MM/LM/JM/EC until
  each individual cutover is evidenced.

## ⚪ Standing / Definition-of-Done

- [ ] **Restore drill:** restore an `adapteng_ops` backup into a scratch target
  and prove readability (§13 DoD). Backup last verified 2026-07-25 13:31.
- [ ] **Migrations not live:** 002 (run ledger), 003 (approval/outbox), 005 (AI
  gateway), 006 (integrity) — apply only with backup + a real consumer
  (`runbooks/apply-migration.md`).
- [ ] **Baserow off-host export/restore** completion; **Google Workspace**
  Manager/recovery acceptance.
- [ ] **Workspace recovery/break-glass acceptance:** verify Ivan is Manager of
  `AdaptEng Company`, create/confirm the Cloud Identity Free break-glass
  super-admin, enable MFA, and store recovery codes offline. This cannot be
  proven by a service account.
- [ ] **Personal JM/EC isolation:** verify live credential/store identity;
  the `ISO-1` waiver expires **2026-08-08**.
- [ ] **n8n Cloud inventory drift:** live API now reports 89 workflows / 38
  active versus 82 repository exports (14 live-only, 7 repo-only). Export,
  sanitize, classify and reconcile before claiming the repository index is
  authoritative; do not bulk-import/activate during this cleanup. MM-40 through
  MM-43 were deliberately unpublished with all entry triggers disabled; do not
  reactivate their direct-model/public-form/WordPress/social path during
  reconciliation. Historical WordPress pages 878/880/882/884/891 are already
  in Trash, related n8n rows are quarantined and page 891 cache/public access
  was verified closed.
- [ ] **Media intake:** approve snapshot/rollback, import the backward-compatible
  consumer first, canary one real case, then redeploy the marketing worker.
- [ ] **CASE-2026-001 media review:** repository metadata says redaction resolved,
  but a later live Sheet record says `needs_redaction_review` with a visible
  coordinate risk. Treat all six media files as blocked from publication until
  a human reconciles the live status and confirms EXIF/GPS/visual redaction.
- [ ] **Rotate the Coolify API token** post-launch.
- [ ] **Record actual invoices/renewals** for Hetzner, Cloudways, n8n Cloud,
  Zoho, GoDaddy and Workspace. Public list prices are planning evidence; invoices
  are the cost source of truth.
- [ ] **GitHub Actions** monthly budget is $10 hard-stop — mind it when queueing
  CI-heavy work.

---

## What's already done (live-proven, no action needed)

- Governed **baserow-adapter** live internal-only; migration 001 applied on first
  boot; adapter service token rotated post-canary.
- **AUT-001** Systems Registry governed workflow live (pure-internal, header-auth).
- **WEB-002** governed lead intake live and proven (T1–T4), migration 004 applied,
  fail-closed retry semantics.
- Baserow Company OS schema provisioned live (8 tables / 107 fields / 10 views);
  read-only table-id binding captured on `main`.
- Leaked Baserow token revoked at source; 14 synthetic proof rows deleted and
  verified (2026-07-26).
- Google service account provisioned with Drive domain-wide delegation; key held
  in Coolify as `GOOGLE_SERVICE_ACCOUNT_JSON_B64`.
