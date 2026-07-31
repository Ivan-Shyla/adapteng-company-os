# Owner action items

Prioritized. Each item says **why**, **how**, and **who/where**. Check them off
in a status PR as you complete them, and mirror the change into
`ARCHITECTURE.md` §11.

Legend: 🔴 security / do first · 🟠 data hygiene · 🟡 unblock next steps · ⚪ standing / DoD

---

## 🔴 Security — do first

- [ ] **URGENT - revoke and rotate the compromised Baserow API token**
  (`baserow-company-os-primary`; fingerprint redacted). Its literal value
  was committed to Git history and must be treated as compromised. Owner
  only: revoke it at the provider, issue and install a least-privilege
  replacement, then verify the old value fails. Current tracked files are
  sanitized, but rotation is not complete or evidenced; do not rewrite history.
- [x] **Revoke the temporary Baserow cleanup token** (secret
  name/fingerprint redacted).
  ✅ **DONE 2026-07-26** — independent API verification now returns HTTP 401.
- [ ] **Rotate the self-hosted n8n public API management key after launch work.**
  It was supplied through chat for the governed workflow build. Revoke it in
  n8n, issue a new operator key only if continued API administration is needed,
  and keep the replacement in the password manager/runtime secret store — not
  Git or chat. Existing encrypted workflow credentials/webhooks do not depend
  on this management key, so rotation should not interrupt AUT-001/WEB-002.
- [ ] **Close systemic n8n MCP exposure.** Audited workflows still have
  **Available in MCP** enabled because the available workflow-update API
  rejected that unsupported field; no availability changed. Instance-level MCP
  plus per-workflow availability and an authenticated user exposes supported
  workflows. Disable instance-level MCP globally or change per-workflow
  availability through a supported UI/API, then verify the effective exposure
  is an explicit allowlist only. Turning **Available in MCP** off does not stop
  normal webhook, schedule, manual or internal triggers. Repository containment
  evidence is merged: automation-platform PR #85 reviewed head
  `a88ba7e3f76e6a192ee687ef7d55aa50fc575fc1` received independent
  **REVIEW CLEAN** and was guarded squash-merged as automation `main`
  `99f4d88e867cb874cf8821de14ddea1b882b5560` at
  `2026-07-28T07:26:18Z`. Keep this owner action open: that merge did not access
  n8n, change live availability, verify an authenticated MCP allowlist, or alter
  the verified **89 non-archived / 31 active / 58 inactive** state.
- [ ] **Harden live paths before any further freeze decision.** Do not freeze
  MM-18 while recent successful webhooks prove it is the current website lead
  path; retain it until a new immutable review-clean WEB-002 producer head plus
  actual producer T1-T4, atomic no-dual-write cutover and rollback proof are
  complete. Repair MM-20/MM-24 approval, dependency and idempotency controls and
  make MM-07 allowlist logging redacted. Personal-project workflow work is
  outside the Company OS operational roadmap.
- [ ] **Enable minimal solo-safe `main` protection in repository Settings/Rules.**
  Re-verified 2026-07-27: `main` is **unprotected** in
  `Ivan-Shyla/adapteng-company-os`, `adapteng-automation-platform`,
  `ai-dev-loop-control-plane`, `adapteng-marketing`, `adapteng-website` and
  legacy `PalinaRuban/adapteng`. The active repositories remain under the
  personal `Ivan-Shyla` namespace; company ownership is not yet evidenced. An
  administration-API attempt to apply the minimal contract to Company OS
  returned 404; **no setting changed and protection is not enabled**. In the
  five active repositories, require a pull request with 0 required approvals,
  require conversation resolution and linear history, block force-push and
  deletion, and pin no required checks yet. Apply the same contract to the
  legacy repository only after containment.
- [ ] **Isolate the shared deploy key.** Inventory deploy-key bindings by
  non-secret identifier, replace the shared key with per-repository/service
  least-privilege credentials, and prove deploy/rollback continuity without
  interrupting current deployments.
- [ ] **Separate excluded personal-project resources and prove company credential
  isolation.** Job Monitor/job-search, English Coach/English-learning and Kraken
  personal trading must not migrate into Company OS, company Shared
  Drive/Baserow/Postgres/n8n, AI employees, budgets or the operational roadmap.
  Keep aggregate exclusion/isolation evidence only; create no
  `Systems_Automations` or other operational company rows. Separate any currently
  shared credential/store within the personal boundary and revoke the
  company/shared binding after proof, without copying personal data. The
  `ISO-1` waiver expires **2026-08-08**. Record company workflow→credential
  bindings by ID/name only, then nominate and test a second company
  administrator/break-glass operator.
- [ ] **Finish owner-only remediation and archive legacy
  `PalinaRuban/adapteng`.** Treat it only as a personal-account June-2026
  WordPress/Azure snapshot — not active Company OS, authoritative production or
  rollback. PR #3 exact head `9b9d9e99859370a4d43d563870d1028725171348`
  received bounded immutable **REVIEW CLEAN** and was guarded squash-merged as
  `main` commit `9c8acd166bf57dc416ed6de86ced8f0b26ac3eb5` at
  `2026-07-28T10:43:33Z`. `candidate-policy` succeeded;
  `trusted-base-policy` skipped only for the expected one-time bootstrap.
  Repository containment is complete, but history-clean, credential rotations,
  archival and live-ready all remain **false**. Rotate the historically exposed
  DB credentials, WordPress auth salts, repository/Azure deployment credential
  and mail credential at their providers, then verify the old values fail; no
  rotation or history cleanup occurred here, and do **not** rewrite Git history
  yet. Retain only the custom theme, brand/license provenance and historical
  runbook. Migrate structured approved business knowledge from the current live
  CMS/database; exclude WordPress core/plugins/runtime/credentials/PII. Archive
  only after a fresh encrypted current CMS/database/media export and company
  ownership transfer. Keep the legacy repository excluded from Company OS
  authority throughout.

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
  `GOOGLE_SERVICE_ACCOUNT_JSON_B64`; delegated-user config is
  `GOOGLE_WORKSPACE_DELEGATED_USER` (values never in repo). This closes
  credential supply only. Current `main` is folder-only and still expects old
  `GOOGLE_SERVICE_ACCOUNT_JSON` / `GOOGLE_WORKSPACE_ADMIN` names, so copy,
  pending-artifact and replay implementation remain blocked.
- [ ] **Drive PR-A, then PR-B — no combined bridge shortcut.** Current `main`
  has folder/base-structure operations only: no general file/tree listing,
  copy, pending-artifact creation or deterministic partial-failure replay.
  Implementation/review is in progress; **no controlled copy has begun**. Open
  implementation attempts are not readiness evidence. PR-A must add the typed
  allowlisted copy/pending-artifact library, Google client, deterministic
  partial-failure replay, actual B64/delegated-user env config and dispatch/CLI.
  Review PR-A first. Only then stack PR-B with the authenticated internal HTTP
  service. Deployment and any controlled copy require separate approval.
- [ ] **`drive-folder-usage-notes` — approve live `START HERE` placement
  separately.** PR #11 defines only the repository contract; it performs no live
  Drive write. Put exactly one concise versioned note in every canonical work
  area and generated `AE-CAS`/`AE-CGR` folder, prioritizing `01_Inbox`,
  `30_Projects_Cases`, then `40_Content`. Require purpose, allowed/disallowed
  inputs, naming/metadata, one placeholder-only example, current
  manual/live/planned automation, trigger/actions/output, approvals/PII and
  owner/version. The manager must be idempotent, create no duplicate, include no
  secret/credential/assigned or provider ID/live payload/PII, update only its
  versioned managed section and preserve all human-authored content. Fail closed on
  duplicate notes or malformed markers.
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
- [ ] **Website producer (website PR #78)** stays draft/held. Head
  `b0e3a656cf6659b893810e11a15b9f515527ab79` is historical last-reviewed
  evidence only. Head `1baedaf732088edcc3fa4e40892d23d42b140d7b` is the
  historical seven-issue-blocked delivery/data-race head. Current GitHub head
  `a23b194fb72aed51941d9cb1c288cbc7eb2f66a0` remains draft, unmerged and
  undeployed and awaits fresh independent review. No review-clean or
  cutover-ready claim is made. Require an exact-head independent review-clean
  result before following the [producer cutover
  sequence](../runbooks/n8n-operations.md#website-producer-cutover-safety):
  actual WordPress/Fluent Forms producer T1–T4, atomic mode switch with no
  dual-write, seven-day reconciliation and rollback proof; MM-18 retirement
  last. Keep model-provider legal placeholders unpublished.
- [ ] **self-hosted n8n cutover:** repoint the Coolify source from branch
  `palinaruban-repo-status-review` to `main`, verify auto-deploy, then complete
  the inactive company-workflow shadow. n8n Cloud remains the authority for
  company MM/LM until each company cutover is evidenced. Excluded personal
  projects have no Company OS cutover.

## ⚪ Standing / Definition-of-Done

- [ ] **Configure and prove the approved `adapteng_ops` physical backup path.**
  The selected design is operator-managed pinned pgBackRest 2.59.0 physical
  base backups + continuous WAL using provider-managed Backblaze B2 EU Central
  object storage, with a clean disposable Hetzner PostgreSQL-only restore host.
  It is **proposed, not configured**; it is not provider-managed PostgreSQL
  backup, the 2026-07-25 Coolify logical backup is insufficient, and the Baserow
  all-in-one backup is unrelated. Follow
  [`runbooks/backup-and-restore.md`](../runbooks/backup-and-restore.md): land the
  separately reviewed compatible image manifests/build, collectors, status
  harness and scheduler; approve the complete provider quote; require bucket
  Object Lock disabled; take a fresh full; and pass post-backup `check` plus
  parsed selected-set `verify`. Use only the tracked guarded A/B/C restore
  entrypoint, Docker-measured image identity and digest-verified transaction
  probe. Prove retention from authentic selected-set completion and fresh
  scheduler/repository inventories at rollout authorization. Rehearse A exact
  pre-migration baseline; B exact 007 + Drive-008 plus DML transaction rollback
  with zero durable synthetic state; and independent C ending in B's exact
  migrated catalog state. Record digest-only evidence, capture C final exact
  status before cleanup, then delete the host/volumes and revoke the read-only
  key. Automation evidence schema v2 at
  `4afc3b13668e6a07187db2ee50ee4a283833d16d` rejects the new procedure/image/
  inventory/retention fields; its separately reviewed schema, validator,
  fixtures and consumer evidence-lifecycle PR is an explicit blocker. Rollout
  authorization remains **NOT READY** until that PR merges and validates these
  exact local fields: `completed_at`, `selected_set_info_sha256`,
  `scheduler_inventory_sha256`, `scheduler_inventory_observed_at`, and
  `retention_valid_until`. Do not dispatch
  approved-assets before that dependency merges and a reviewed sanitized
  `PASS` validates.
- [ ] **Migrations not live:** 002 (run ledger), 003 (approval/outbox), 005 (AI
  gateway), 006 (integrity), 007 (source-identity reservation) and Drive-008
  (replay reservations) are repo-only and unapplied. The approved-source pair
  was merged by automation-platform PR #89 at
  `dbcf806ea7714b8e2a7415ae6cd788491924178d`; apply it to live only after the
  physical-backup rehearsal above, rollout review and explicit owner go/no-go.
  Follow [`runbooks/apply-migration.md`](../runbooks/apply-migration.md), require
  a real consumer, and redeploy the adapter for 007. Unrelated
  `008_ai_gateway_runtime_hardening.sql` remains forbidden for this rollout.
- [ ] **Baserow off-host export/restore** completion; **Google Workspace**
  Manager/recovery acceptance.
- [ ] **Workspace recovery/break-glass acceptance:** verify Ivan is Manager of
  `AdaptEng Company`, create/confirm the Cloud Identity Free break-glass
  super-admin, enable MFA, and store recovery codes offline. This cannot be
  proven by a service account.
- [ ] **n8n Cloud inventory drift:** a fresh live audit reconfirmed
  89 non-archived workflows / 33 active; reversible freeze-now actions then
  produced **89 non-archived / 31 active / 58 inactive** versus 82 repository
  exports. Drift remains 14 live-only / 7 repo-only. The active safety-freeze
  chain is **42 → 40 → 38 → 37 → 36 → 35 → 34 → 33 → 31**. Export,
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
  creation; the five earlier freezes are verified fail-closed.
  MM-Visual-Evidence-Intake `uBVRMTCKwnUG91kU` is now unpublished because its
  Telegram route lacked a principal allowlist and could reach media-worker
  before validated-ready output. Published version
  `dda7f039-764c-449d-9f0e-4c8badb44919` is retained for audit/rollback; current
  draft is `3093a3bf-0567-4cb3-9956-56020dc4713c`, with `active=false` and
  `active_version_id=null`. Manual, Telegram and worker nodes are disabled.
  Never reactivate it until a founder/principal allowlist precedes every command
  and media-worker is reachable only from validated-ready output. MM-08
  `RAPjKSnj6EY7axtb` is also
  unpublished because it was an unauthenticated public write ingress with zero
  executions and no proven dependency; webhook and lead-write nodes are
  disabled. Active version `644416d5-7f7f-4fa0-b02f-a8c787752617` is retained;
  current draft is `37817f58-e6fb-4876-a076-497ab776413c`, with `active=false`
  and `active_version_id=null`. The prior active version and version history
  remain retained as rollback evidence against the containment draft.
  Reactivation/replacement requires named accountable owner Ivan, explicit owner
  approval, authentication, schema validation, rate control and stable
  deduplication. Production and manual probes for both workflows have
  `execution_id=null`; the post-freeze execution count is zero. Already-inactive
  MM-10 `39CAjeKcZD64VM25` and MM-29
  `at9H54krWF9ULdtT` retain drafts
  `22776538-c4eb-4ea3-98e8-eeb1de8c6ea7` and
  `1ca9a60e-9c4f-425c-980f-fedc24d85bf2`; Manual, Schedule and approval-write
  nodes are disabled as defense-in-depth, both remain inactive, and prior
  history is retained. Historical WordPress pages
  878/880/882/884/891 are already in Trash, related n8n rows are
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
- [ ] **Media intake:** implementation remains split among the live
  `adapteng-marketing` worker, n8n Cloud MM workflows and the governed consumer,
  which is merged but not imported. Name the canonical implementation/owner,
  complete Drive PR-A review then PR-B, approve snapshot/rollback, import the
  backward-compatible consumer, canary one real case, then redeploy the
  marketing worker.
- [ ] **CASE-2026-001 media review:** repository metadata says redaction resolved,
  but a later live Sheet record says `needs_redaction_review` with a visible
  coordinate risk. Treat all six media files as blocked from publication until
  a human reconciles the live status and confirms EXIF/GPS/visual redaction.
- [ ] **Rotate the Coolify API token** post-launch.
- [ ] **Record vendor commercial baselines** for Hetzner, Cloudways, n8n Cloud,
  Zoho, GoDaddy and Workspace: actual base cost, renewal/cancellation date,
  account owner and SLA/support terms. Public list prices are planning evidence;
  invoices are the cost source of truth.
- [ ] **Clean up stale pull requests deliberately.** Classify each open PR as
  current work, retained evidence or superseded draft; close stale and explicit
  `DO NOT MERGE` branches with a reason, without merging merely to reduce count.
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
- Fourteen synthetic Baserow proof rows deleted and verified (2026-07-26).
  Compromised token remediation is reopened under Security because a literal
  value remains in Git history; no rotation completion is claimed.
- Google service account provisioned with Drive domain-wide delegation; actual
  runtime refs are `GOOGLE_SERVICE_ACCOUNT_JSON_B64` and
  `GOOGLE_WORKSPACE_DELEGATED_USER`. This proves credential supply, not file
  copy/artifact implementation or live wiring.
