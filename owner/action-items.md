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
- [x] **Enable minimal solo-safe `main` protection in repository Settings/Rules.**
  ✅ **DONE, re-verified read-only 2026-08-03** — an active `main-protected`
  GitHub ruleset now exists on all five active repositories
  (`adapteng-company-os` id `20236724`, `adapteng-automation-platform`
  `20236725`, `ai-dev-loop-control-plane` `20236728`, `adapteng-marketing`
  `20236729`, `adapteng-website` `20236726`; created 2026-08-02T15:30 CEST via
  automation-platform PR #58/company-os PR #19 and its siblings), each
  requiring a pull request (0 required approvals) plus per-repository required
  status checks, and blocking force-push and deletion. Thread resolution and
  linear history are **not** enforced (left off). Legacy `PalinaRuban/adapteng`
  still cannot be checked via the rulesets API (403: below GitHub Pro), so its
  protection state remains unverified this cycle; apply the same contract there
  only after containment is otherwise complete. Consider adding required
  conversation-thread resolution if desired.
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

- [ ] **Reconcile the Backblaze B2 backup configuration with the runbook.**
  Object-store connectivity is proven: dispatch-only run
  [`30752237109`](https://github.com/Ivan-Shyla/adapteng-company-os/actions/runs/30752237109)
  (`Verify B2 connectivity`, `workflow_dispatch`, conclusion `success`,
  2026-08-02T14:30:06Z, head `7f5f3585588da8b330e4ae9779f0b6343e1156eb`) wrote a
  probe object to the private bucket, read it back and compared the bytes,
  deleted it, then asserted with `head-object` that it was gone. Credentials
  authenticate and deletes really delete, so bucket Object Lock is not silently
  making retention unenforceable. **No backup exists** — nothing in that run
  touched PostgreSQL. Four follow-ups remain, and only the code fix can be done
  from a pull request:
  1. **`PGBACKREST_REPO1_S3_URI_STYLE` — keep the configured value `host`; no
     variable change needed.** The runbook previously specified `path`; that was
     the error and it is now corrected in
     [`runbooks/backup-and-restore.md`](../runbooks/backup-and-restore.md).
     pgBackRest documents `host` as its default and defines `path` for stores
     that cannot serve `bucket.endpoint`
     (<https://pgbackrest.org/configuration.html>); Backblaze documents that its
     S3-compatible API accepts the bucket name in either the hostname or the
     path
     (<https://www.backblaze.com/docs/cloud-storage-call-the-s3-compatible-api>),
     so B2 does not require `path`. Checked independently on 2026-08-02: the
     virtual-hosted name for the configured bucket resolves, TLS validates and
     B2 answers `403` rather than a DNS or certificate error, and run
     `30752237109` used the AWS CLI with only `--endpoint-url`, whose
     `addressing_style` default is `auto` and prefers virtual-hosted
     (<https://docs.aws.amazon.com/cli/latest/topic/s3-config.html>).
  2. **`PGBACKREST_REPO1_PATH` — the value is a prefix inside the bucket, but it
     is not unconstrained; see item 5 before changing anything.** **Correction
     to what this item said when it was first written:** it claimed that at
     `7f5f3585588da8b330e4ae9779f0b6343e1156eb` "nothing reads a literal". That
     was wrong, and the error was mine. `scripts/postgres_restore_generation.py`
     does take the value from the guard packet and
     `.github/workflows/verify-b2-connectivity.yml` does only assert it is
     absolute — but `validate_repository` in `scripts/postgres_restore_guard.py`
     compares it against a hardcoded literal and fails closed on mismatch. That
     comparison was introduced on 2026-08-01 in
     [#15](https://github.com/Ivan-Shyla/adapteng-company-os/pull/15) (`e30da31`),
     a day before the claim was written, so it was present and simply not
     checked: the trace followed the generator and stopped there. The prefix is
     therefore free to choose only in B2; inside this repository one literal
     depends on it. Item 5 has the detail and the recommendation. The exact value
     remains deliberately unwritten in Git because the runbook's own evidence
     policy lists repository paths as forbidden. **Owner check in the B2
     console:** the hidden-version deletion (35 days) and unfinished-large-file
     cancellation (7 days) lifecycle rules, and the application key's prefix
     restriction, must be scoped to whichever value item 5 settles on. A
     lifecycle rule left on a stale prefix would silently stop expiring hidden
     versions — a retention and cost defect that no pgBackRest command reports.
  3. **The application key is broader than the runbook prescribes.** Phase 2
     step 3 requires a key restricted to the bucket *and the pgBackRest
     repository prefix*, but run `30752237109` wrote and deleted under a
     `connectivity-check/` prefix outside it and succeeded. Decide explicitly:
     either accept a bucket-scoped key and record that decision, or narrow the
     key to the repository prefix and accept that the connectivity workflow then
     needs its own allowed prefix. Do not leave it undecided. This also bears on
     the `403` the restore rehearsal is currently failing on: because run
     `30752237109` succeeded under a prefix *outside* the pgBackRest repository
     prefix, the key in use is demonstrably not narrowly prefix-restricted,
     which is evidence against "the key cannot see that prefix" as the
     explanation for the `403`.
  4. **Code fix — done, no owner action.**
     `scripts/postgres_restore_generation.py` hardcoded
     `repo1-s3-uri-style=path` and never consumed
     `PGBACKREST_REPO1_S3_URI_STYLE`, so that variable had no effect on
     restore and the restore side disagreed with the backup side. Three of its
     neighbours were the same shape: `repo1-type` and `repo1-cipher-type` were
     also hardcoded, and `repo1-s3-key-type` was never emitted at all — four of
     the eight non-secret variables were set but ignored. All four now flow
     from the guard packet, an unset setting falls back to pgBackRest's own
     default rather than to the copied `path`, and a value the procedure cannot
     honour stops the run instead of being silently overridden.
  5. **`PGBACKREST_REPO1_PATH` does not match the value the restore guard
     pins, and only you can change the variable.** `validate_repository` in
     `scripts/postgres_restore_guard.py` fails closed unless the repository
     prefix is exactly `/adapteng-ops` — hyphen-separated, matching the stanza
     name `adapteng-ops` that the same check pins and that the runbook, the
     generated config and the unit tests all use. The configured variable uses
     an underscore instead, so a restore wired to it would stop at the guard
     with `repository stanza/repo is not exact`. **Recommendation: change the
     variable to `/adapteng-ops`, not the guard.** Three reasons: the guard's
     literal is the value every other surface in the repository already agrees
     on; the pin is a deliberate fail-closed control and relaxing it would
     remove a check rather than fix a mismatch; and no backup has ever been
     written under either prefix, so there is nothing to migrate and this is the
     cheapest moment it will ever be to correct. Whichever way you decide, the
     lifecycle rules and key scope in item 2 must be scoped to the value you
     settle on.

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
  separately.** PR #11 defines only the repository contract; automation-platform
  PR #91 (`427dd7f5...`, merged 2026-07-30) then repository-merged the
  planner/apply capability, but it performs no live Drive write. Put exactly
  one concise versioned note in every canonical work
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
- [ ] **AI runtime readiness — REJECT_LIVE, but AG-008 is repository-merged.**
  Control-plane main advanced to `edadb09125f7fb5d173d5f595181d1384050b6b5` via
  PR #38 (`c6a5b509...`, merged 2026-07-30) and PR #39 (`edadb091...`, merged
  2026-08-02): PR #38 requires a mandatory schema-valid task envelope, enforces
  `no_external_action: true` with recursive rejection of approval/publish/
  send/action/execute fields and IDs, and makes model-gateway cost accounting
  atomic and cap-bounded — closing the optional/unvalidated-envelope,
  missing-`no_external_action`/synthetic-`approval_id`, and over-cap/negative-
  budget bypasses this action item previously flagged. PR #39 closed a related
  gap in the general JSON validator. `agent/NEXT_TASK.md` self-declares
  `status: done` and CI is green, but no independent third-party review of this
  exact head is recorded — get one before relying on it. Separately,
  automation-platform must still deploy and wire persistent Postgres cost
  reservation/reconciliation, the EU Vertex adapter, Drive adapters,
  orchestration, canonical approval and runtime. Repository components are not
  deployed/working business AI.
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
- [ ] **Website producer (WEB-001, website PR #78) — repository-merged, not
  deployed.** Head `b0e3a656cf6659b893810e11a15b9f515527ab79` is historical
  last-reviewed evidence only; `1baedaf732088edcc3fa4e40892d23d42b140d7b` is the
  historical seven-issue-blocked delivery/data-race head. PR #78 (`feat(lead-
  intake): versioned lead.created v1 contract with PII separation (WEB-001)`)
  merged 2026-08-01T22:54:57Z as `6770e749fbf1345bfe15f260e574da93fa4df329`,
  superseding the stale `a23b194f` draft reference, and adds the
  `lead.created` v1 schema/docs plus a WordPress producer plugin
  (`wp-content/plugins/adapteng-core/includes/lead-intake.php`). The manual,
  confirmation-phrase-gated `Deploy to Cloudways` plugin workflow that would
  ship `adapteng-core` live has run repeatedly, but every recorded run (last
  success `30720691975`, 2026-08-01T22:10:23Z) predates this merge — no plugin
  deploy is evidenced to carry the lead-intake code. No review-clean or
  cutover-ready claim is made. Require an exact-head independent review-clean
  result on `6770e749` before following the [producer cutover
  sequence](../runbooks/n8n-operations.md#website-producer-cutover-safety):
  actual WordPress/Fluent Forms producer T1–T4, atomic mode switch with no
  dual-write, seven-day reconciliation and rollback proof; MM-18 retirement
  last. Keep model-provider legal placeholders unpublished.
  Separately, a **theme-only** deployment track (unrelated to WEB-001) was
  authorized by the owner and merged as PRs #121–#130; `main` now carries an
  active `main-protected` ruleset (all five active repos, 2026-08-02T15:30
  CEST), and the `Deploy theme to Cloudways` workflow's run `30766896787`
  (head `18767bd1...`, 2026-08-02T21:00:26Z) completed with its snapshot,
  deploy and production-smoke-test steps all `success` per GitHub Actions
  metadata — independently re-verify the live site before treating that as
  accepted.
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
  The object store is reachable and its delete path is proven (run
  `30752237109`, 2026-08-02), and the non-secret configuration now lives in the
  `PGBACKREST_REPO1_*` repository variables; **the backup itself is still not
  configured**. It is not provider-managed PostgreSQL
  backup, the 2026-07-25 Coolify logical backup is insufficient, and the Baserow
  all-in-one backup is unrelated. Follow
  [`runbooks/backup-and-restore.md`](../runbooks/backup-and-restore.md): land the
  separately reviewed compatible image manifests/build, collectors, status
  harness and scheduler; approve the complete provider quote; require bucket
  Object Lock disabled; take a fresh full; and pass post-backup `check` plus
  parsed selected-set `verify`. Populate and independently review the exact
  image, single-container runner, challenge-bound provider broker/signature and capability-complete
  inventory-exporter manifests; their current `NOT_CONFIGURED` state must stop
  execution. Establish the declared dedicated PostgreSQL/backup host scope
  (or separately review an exclusive broker replacement); the current shared
  Coolify scope is not accepted. The exporter must prove no user manager/linger,
  pin the exclusive repository-write principal,
  encrypted credential, direct full/differential jobs and every other
  installed/loaded/generated/transient systemd unit, per-user unit source,
  cron/anacron/at spool, container and all-UID capability-bearing process
  identity; unknown,
  deleted, opaque or Docker-socket-capable surfaces fail. Use only the tracked
  guarded entrypoint on three independent clean A/B/C hosts, fresh one-use
  provider operations that prove empty `private_net`, authoritative ID-only
  never-started target validation, Docker-measured image/runner identity and
  descriptor-streamed transaction probe. Prove retention from the canonical
  fixed accepted packet and fresh scheduler/repository inventories. Rehearse A exact
  pre-migration baseline; B exact 007 + Drive-008 plus DML transaction rollback
  with zero durable synthetic state; and independent C ending in B's exact
  migrated catalog state. Record digest-only evidence, capture C final exact
  status before cleanup, then delete the host/volumes and revoke the read-only
  key. A separately reviewed automation evidence-lifecycle schema, validator,
  fixtures and consumer PR is an explicit blocker; no final schema version or
  compatibility is claimed. Current
  status is `NOT_READY_PENDING_AUTOMATION_EVIDENCE_LIFECYCLE_PR`; rollout
  authorization remains blocked until that PR merges and validates these
  exact local fields: `completed_at`, `selected_set_info_sha256`,
  `scheduler_inventory_sha256`, `scheduler_inventory_observed_at`, and
  `retention_valid_until`. Do not dispatch
  approved-assets before that dependency merges and a reviewed sanitized
  `PASS` validates.
- [ ] **Migrations not live:** 002 (run ledger), 003 (approval/outbox), 005 (AI
  gateway), 006 (integrity), 007 (source-identity reservation), Drive-008
  (replay reservations) and AI-Gateway-008 (runtime hardening) remain repo-only
  and unapplied. The approved-source runtime authorization chain is now merged
  through automation-platform PRs #93, #94 and #98; no
  `Migrate Approved Assets` dispatch has run as of the 2026-08-05 read-only
  check. Apply only 007 and Drive-008 through that workflow after its documented
  production-backup evidence, isolated restore/rehearsal, external review,
  short-lived exact-subject phase authorization and disposable private-network
  runner are all present. `008_ai_gateway_runtime_hardening.sql` remains
  forbidden in the approved-assets workflow; it requires its separate
  first-model-proof migration path before any live model call.
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
