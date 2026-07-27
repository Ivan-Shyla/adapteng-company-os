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
- [ ] **AI runtime readiness — REJECT_LIVE.** Control-plane main
  `affe6ea1e4d522be0df0641e98a08e20a84549ae` contains deterministic
  AG-001/002/003/006/007 only; there is no business worker, real provider or
  Drive runtime. AG-008 must close the reproduced optional/unvalidated-envelope,
  missing-`no_external_action` plus synthetic-`approval_id`, and over-cap and
  negative-budget P0 bypasses. Separately, automation-platform must deploy and
  wire persistent Postgres cost reservation/reconciliation, the EU Vertex
  adapter, Drive adapters, orchestration, canonical approval and runtime.
  Repository components are not deployed/working business AI.
- [ ] **AI-001 exact first live model proof.** Use only the already-approved and
  published July public article-radar package `ART-2026-001` with source set
  `SRC-2026-001` for the first live model-backed Company Drive proof. Its prior
  publication is evidence, not permission to republish; the proof may create only
  a new `DRAFT_NOT_APPROVED`. `CASE-2026-001` is separately the first governed
  raw-source/case migration and evidence-bounded deterministic case draft. Its
  media and publication remain fail-closed until Ivan reconciles live Sheet
  redaction state with Git. The split is recorded in
  [`ai/ai-001-pilot-intake.md`](../ai/ai-001-pilot-intake.md). Before the proof,
  Ivan ratifies the `AG-007` acceptance set. Technical gates remain governed
  Company Drive writes, ZDR/cache-off/FX verification and every REJECT_LIVE
  blocker above. Then one measured, inactive EU Vertex
  `gemini-3.1-flash-lite` call may run through the canonical gateway. Never
  reactivate or route around frozen direct-model workflow MM-22.
- [ ] **Website producer (website PR #78)** stays draft/held. WEB-002 and migration
  004 are already live and the body is compatible in principle, but current
  transport is unsafe: only the old n8n Cloud allowlist, no host-only
  `X-Webhook-Token`, no durable retry, no 409 dead-letter and no producer E2E.
  The PR auto-deploys `wp-content/**`, so these cannot be deferred until after
  merge. Follow the [producer cutover
  sequence](../runbooks/n8n-operations.md#website-producer-cutover-safety):
  preserve flat MM-18 as host-only legacy default; add dark WEB-002
  URL/token allowlist + auth; store only Fluent Forms entry refs in the outbox;
  2xx ack, 5xx/transport retry, 409 dead-letter/manual review, other 4xx config
  alert; actual WordPress T1–T4; atomic flag cutover with no dual-write;
  seven-day reconciliation; retire MM-18 last. Keep model-provider legal
  placeholders unpublished.
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
- [ ] **n8n Cloud inventory drift:** live API now reports 89 non-archived
  workflows / 33 active versus 82 repository exports; drift remains 14
  live-only / 7 repo-only. The active safety-freeze chain is **42 → 40 → 38 →
  37 → 36 → 35 → 34 → 33**. Export,
  sanitize, classify and reconcile before claiming the repository index is
  authoritative; do not bulk-import/activate during this cleanup. MM-40 through
  MM-43 were deliberately unpublished with all entry triggers disabled; do not
  reactivate their direct-model/public-form/WordPress/social path during
  reconciliation. Live-only `d1SDcRTgMqS9Zvgi` (Claude n8n MCP Gateway) is not
  Company OS authority and had no execution after 2026-07-10. Its fixed GET
  method still allowed AI-selected n8n API read paths without endpoint
  allowlisting or response redaction: broad-read confidentiality/exfiltration
  risk to external Claude, **not arbitrary write**. Prior active version
  `51f02adb` is retained; draft `de142f7b` disables the MCP trigger; it is
  unpublished and production execution is rejected. Do not reactivate or
  connect it to Company OS. MM-ZH-02 `J5SpIS8Ye8JHViFi` accepted approval routes
  from any sender using only a subject substring; scheduled MM-04
  `4D9UBruS1ZhLn1pS` directly synchronized
  `Approval_Log → Content_Drafts`; MM-05
  `o9Lj7F9WbhFSCARq` built `Publish_Plan` non-idempotently on a schedule. All
  entry triggers are disabled, all three are unpublished, and production
  execution is rejected. MM-04 execution `15214` read 30 stale smoke approval
  rows and updated 0; MM-05 execution `15216` found 0 approved drafts.
  MM-22 `clPtSQwzze8DHEvp` called `gpt-5-mini` directly, bypassing the canonical
  gateway, ledger/caps, AG-008 and governed Company Drive. Its Manual and Execute
  Workflow triggers and model are disabled; it is unpublished and production
  execution is rejected. Prior active version `72869463`, freeze draft
  `28a7ef72` and six historical runs/data are preserved. Do not reactivate the
  direct-model path. For the gateway, mail router, approval sync and plan
  builder plus MM-22, manual-mode and production probes reject before execution
  creation; the five freezes are verified fail-closed.
  `uBVRMTCKwnUG91kU` remains active in its founder-chat-allowlisted media
  sanitize/log version; only the unpublished `/approve → MM21-24` draft path was
  disabled, so do not subtract it from the active count. Historical WordPress
  pages 878/880/882/884/891 are already in Trash, related n8n rows are
  quarantined and page 891 cache/public access was verified closed. Unattached
  public media IDs 886–889 and 893–896 were also removed and verified 404; the
  source Drive HEIC files remain untouched.
- [ ] **Legacy approval/publish forensic reconciliation:** treat `Approval_Log`,
  `Content_Drafts` and `Publish_Plan` as non-canonical legacy state. Reconcile
  lineage, stale smoke rows and any surviving decisions/plans before archive or
  canonical migration 003 approval/outbox cutover. Read-only workflow
  `Q2PmbE2VDffRl1iT` execution `15547` found 179 `Approval_Log` rows, all
  synthetic `APPROVE` smoke (177 `TYPE` self-loop + 2 `TEST123`, all
  2026-06-12); 19 `Content_Drafts`, all pending (18 `pending_approval` + 1
  `pending_manual_review`), zero approved/package-ready; and 0 `Publish_Plan`.
  No current real draft was promoted. Temporary workflow `a3luyFSBH9xRELDW`
  dry-run `15548` matched exactly the 179 `TYPE`/`TEST123` smoke rows; live
  execution `15549` deleted exactly row ids 1–179; verification `15550` matched
  0. Both `a3luyFSBH9xRELDW` and read-only `Q2PmbE2VDffRl1iT` are archived.
  `Content_Drafts` remains 19 pending and `Publish_Plan` remains 0; both were
  untouched. Approval cleanup is complete; reconcile the remaining draft
  lineage before canonical migration 003 cutover.
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
