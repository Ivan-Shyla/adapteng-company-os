# Article and marketing content: personal Drive to the company Shared Drive

Status: **prepared, not executed.** Nothing was copied, created, moved, activated
or cancelled. The reviewed plan is
[`registry/content-drive-migration.json`](../registry/content-drive-migration.json),
checked by `scripts/content_migration_manifest.py`. Evidence date: 2026-10-21.

This runbook extends [`company-drive.md`](company-drive.md) and
[`company-administration.md`](company-administration.md). It does not replace
their rules: the personal Drive stays a read-only source, files are copied and
never moved, and each workflow is rewired alone.

## What the evidence changed

| Starting assumption | What was found |
|---|---|
| MM-01, MM-21, MM-23 and MM-24 are live on `n8n.adapteng.com` | The self-hosted status operation (run 39605383384, `confirmed_self_hosted=true`) lists **six** workflows, none named MM. The MM workflows run on n8n Cloud. |
| MM-21 is active but manual | The repository export is paused with a manual trigger, yet 44 source package files sit in the personal My Drive root, mostly written at about 02:00 UTC on Mondays from 2026-07-27 to 2026-10-19. The owner inspected the live MM-20 Publication Scout and reports a weekly Monday 07:00 trigger, auto-approval of radar candidates and a call to MM-21. That is a plausible cause, not a proven one. See below. |
| Outputs go to Baserow | MM-01, MM-15, MM-21, MM-23, MM-23C and MM-23D write to **n8n Data Tables** (Raw_Media, Content_Cases, Photo_Content_Queue, Publication_Radar, Publication_Analysis, Website_Content, Website_Content_Sync, LinkedIn_Drafts). Those tables live inside the n8n instance that runs them. |
| The Google connection n8n uses may reach the Shared Drive | Recorded in `owner/action-items.md` as **proven false on 2026-07-26**: the legacy Drive OAuth connection reads a personal My Drive and has no corporate Shared Drive write access. Nothing seen since contradicts that. It was not retested here. |
| Case media belongs under `40_Content` | `company-drive.md` places case photos and videos under `00_Case_Uploads` and `30_Projects_Cases`. `40_Content/<article>/01_Source` holds only a link manifest to the case. The manifest follows the runbook. |

## Evidence table

| Current source | Corporate destination | n8n workflow change | Duplicate guard | Verification |
|---|---|---|---|---|
| 3 review Google Docs (CFD flue-gas flow, flue gas temperature and dust, common CEMS problems), personal My Drive root, written by MM-23C | `40_Content/AE-CGR-NNNN_<slug>/03_Review`, one folder per article | MM-23C writes to the article `03_Review` folder and looks up `website_id` first. MM-23D takes the corporate document ID. Both stay inactive until the canary. | `legacy_key`, `source_sha256`, reserved `AE-CGR` ID; replay creates nothing | Names, MIME types and export SHA-256 match the manifest; originals untouched; replay copies zero files |
| 3 matching source package `.txt` files (SSRN 6086433, Bitlis Fen 1719869, EPE 214925) | `01_Source` of the matching article | MM-21 writes to `01_Source`, not the My Drive root | Same, plus DOI cross-check | Same |
| 39 recurring source packages, 5 distinct DOIs (one DOI repeated 20 times), written by the suspected MM-20 to MM-21 path | None yet | MM-20 stops writing protected fields on existing rows; MM-21 checks the radar row and the destination folder before writing; MM-20's Cloud schedule is paused or gated by the owner first | `rediscovery_row`, `protected_field_mappings`, `should_write_source_package`; `plan_copy` for the copy itself | Cloud executions and rows confirm the writer; a test run on the rewired path creates no second file |
| 2 test fixtures; 2 unrelated Russian-language documents | None | None | Excluded in the manifest | Not applicable |
| Case media `CASE_GMS810_WOHLER_001`: 3 photos, 1 video (about 140 MB), 1 case note, in the watched personal `01_Inbox` | `00_Case_Uploads/CASE-YYYY-NNN`, link manifest in `40_Content` | MM-01 gets a `drive_file_id` existence check before both inserts, then its trigger moves to the corporate intake folder. MM-15 stays paused because it watches the same folder. | `drive_file_id` against Raw_Media and Content_Cases | Held until redaction review (EXIF/GPS and visual) is confirmed; then counts and hashes match |
| Legacy tracker workbook `AdaptEng Marketing System` | None yet | None | Not applicable | Owner decides keep, archive or copy |
| 21 published website posts (7 topics in EN, CZ, RU); none matches the three review docs | Receipts only, in `05_Published`, when each article has a folder | MM-24 unchanged; final publishing stays manual | Slug and WordPress post ID | Public sitemap count equals the manifest |
| Data Tables on n8n Cloud | Self-hosted n8n Data Tables | Export from Cloud, import to self-hosted before any workflow moves | Table row counts before and after | Row counts equal; sample rows compared |

MM-41 Case Pipeline Orchestrator (`z2dYx1mvy9rXOsLY`) has no repository export and
is safety-frozen. Export and sanitize it before deciding anything. MM-03 is
inactive; no LinkedIn autopublishing is assumed.

## Recurring writer and processed state

Copying files is only half of the duplicate problem. Something keeps creating
new source packages, so a one-off copy would be undone by the next run.

What the repository shows. MM-20's export upserts `Publication_Radar` on
`dedupe_key`, and the same upsert writes `status`, `drive_file_id` and
`analysis_id` from the newly discovered item. A freshly discovered item carries
empty strings for the last two. A publication found again can therefore lose
its Drive file and analysis links and return to a fetchable status.

What the owner reports from the live graph. A weekly Monday 07:00 trigger,
auto-approval of selected candidates, and a call to MM-21, whose Drive node
writes to the personal My Drive root. The repository export differs (Tuesday
07:00, no auto-approval, no MM-21 call), so the live graph is not in Git.

Status: **plausible, not proven.** It is not stated as the root cause until
Cloud executions and rows confirm it. Open points: whether the file creation
times line up with the MM-20 and MM-21 executions; whether the repeated DOIs
had empty `drive_file_id` and `analysis_id` after an earlier success; and the
Cloud instance timezone, because Monday 07:00 matches Monday 02:00 UTC only if
the instance runs five hours ahead of UTC.

Rules the rewired path must meet (all in `scripts/content_migration_manifest.py`):

- Discovery inserts a row only when none exists and never writes `status`,
  `drive_file_id` or `analysis_id` on an existing row (`rediscovery_row`,
  `protected_field_mappings`). A reviewer can run the second function on the
  upsert node's column mapping; it must return an empty list.
- MM-21 writes only when the radar row has no `drive_file_id` and the
  destination folder holds no file for that `pub_id`
  (`should_write_source_package`).
- Until both hold, the Cloud schedule for MM-20 is paused or gated by the
  owner. This runbook does not pause it; that is a change to a live Cloud
  workflow and stays with the owner.

The manifest carries `completion_gates`. The plan is **not complete** while any
gate is open, and the validator rejects a manifest that says it is.

## Why the existing credential cannot simply be repointed

A personal OAuth credential that has no access to the Shared Drive cannot be
fixed by changing a folder ID, and the owner policy forbids rotating or
replacing credentials as part of this work. Writes to the Shared Drive go through
the governed Drive adapter and the company service account, as in
`company-drive.md`. The n8n Cloud credential is left as it is until the last
workflow that uses it has moved ("retire the legacy Drive OAuth credential" in
`owner/action-items.md`).

No folder was created under `40_Content`: the connected personal session cannot
see Shared Drives, and no governed folder operation was authorized for this step.
The first folder operation should be `ensure_folder` with a fixed idempotency key
per article, which the adapter already treats as replay-safe.

## n8n subscription

Cancelling is **not safe now**, and the evidence does not yet say what the
payment covers.

- The self-hosted instance (n8n 2.31.5 on Coolify) shows no enterprise features
  on its public settings (SAML, LDAP, OIDC all off). That is consistent with a
  free community install but does not prove it; the plan page needs an
  authenticated view.
- The legacy Marketing Machine and Lead Monitor workflows, their Data Tables,
  their credentials and their execution history live on n8n Cloud. The last
  audit recorded 89 non-archived workflows, 31 active. Ending a Cloud plan
  would stop those workflows and may remove the instance and its Data Tables
  after the provider's retention period, which has not been looked up. MM-18 Website Lead Form Intake is active on
  a webhook and tied to the website's lead flow until WEB-002 is cut over.
- Personal projects (Job Monitor, English Coach, Kraken) are out of scope and
  are not to be migrated.
- Self-hosted community n8n is expected to be the cost-free landing place, but
  features such as projects and role-based sharing differ by plan. Confirm which
  of those the company actually uses before moving.

Conditions to cancel, all evidenced and none met yet:

1. The Cloud plan, price, renewal date and owner are recorded (the vendor
   baseline item in `owner/action-items.md`).
2. Every Cloud workflow with an execution in the last 90 days is migrated,
   retired on purpose, or exported and archived.
3. All eight Data Tables are exported and imported with matching row counts.
4. Credentials are re-created on self-hosted by the owner, by reference.
5. Self-hosted backup and restore rehearsal covers the imported tables.
6. A final Cloud export is stored outside n8n before the plan ends.

## n8n Cloud access (401)

What is configured. The `n8n` connector on this machine is not a stored Cloud
API key. It is an MCP link to an endpoint on the Cloud instance, and the tool it
exposes forwards GET requests to the Cloud REST API. The 401 body is the REST
API's own "Unauthorized", so the link and the workflow behind it respond and the
API key they use inside Cloud is what fails. The self-hosted `X_N8N_API_KEY` and
`N8N_BASE_URL` belong to a different host and are not involved. Nothing here
touches them.

Likely cause: n8n API keys carry an expiry chosen at creation, so the key held
in the Cloud credential store may have expired or been deleted. This could not
be confirmed without Cloud access.

Caution before repairing that route. The registry records that the Cloud MCP
gateway lets the model choose any read path without an allowlist, and it was
frozen for that reason. Prefer the owner-side check below and, if a new key is
needed, a governed export that uses only listed paths.

Plan and scopes. Scoped API keys are an Enterprise feature; on other plans a key
has full access to the account (n8n docs). The Cloud plan for this account is
not recorded, so **do not describe any new Cloud key as read-only** unless
Settings, Usage and plan shows Enterprise and the key is created with read
scopes only. n8n's pricing page lists Starter and Pro with unlimited active
workflows and data tables; which plan is paid here is still unknown.

## Owner actions

1. **Check the existing key first** (Cloud, Settings, n8n API). If the key used
   by the connector is listed and expired or removed, that explains the 401. If it
   is listed and valid, report that and nothing else changes.
2. **Only if a new key is needed**, create it there with the shortest expiry that
   covers the export and store it under a distinct name that is not the
   self-hosted one:
   - Cloud credential store (Header Auth, header `X-N8N-API-KEY`): `ADAPTENG_N8N_CLOUD_API_KEY`
   - If a GitHub export workflow is later approved: environment secret
     `N8N_CLOUD_API_KEY` and variable `N8N_CLOUD_BASE_URL` in a new environment
     `n8n-cloud-export`.
   - Never overwrite `X_N8N_API_KEY` or `N8N_BASE_URL`; never paste the key in
     chat, Git, a PR or a log.
3. Record the Cloud plan name, monthly cost and renewal date (Settings, Usage and
   plan), and the same for the self-hosted instance.

Later, the Drive side needs this batch:

| Provider and screen | Name to set | Purpose | Minimum scope | Verification |
|---|---|---|---|---|
| GitHub, `adapteng-automation-platform`, Settings, Environments | The governed Drive bridge environment values for the first article folder | Lets `ensure_folder` run for one article | One environment, not repository-wide | The folder appears once, and a replay creates nothing |
| Google Workspace, Shared Drive `AdaptEng Company` | Membership for the company service account on the content folders | Lets the adapter write | Contributor on `40_Content` and `00_Case_Uploads` only | A read-only listing from the adapter shows the folders |

## Order of work after the owner action

1. Export Cloud workflows and Data Tables (read-only), diff them against the
   repository, and confirm or refute the MM-20 to MM-21 path as the writer.
   Then rewire MM-20 and MM-21 to the rules above and have the owner pause or
   gate the Cloud schedule before any Drive cutover.
2. Create the article folders, copy the three review docs and their source
   packages, and compare against the manifest. Replay.
3. Import the Data Tables into self-hosted n8n.
4. Rebuild MM-23C, then MM-23D, then MM-21, then MM-01 on self-hosted, each
   inactive first, with a manual canary against a test folder and the
   duplicate guard in place. Switch one trigger at a time.
5. Pause the Cloud twin of a workflow only after its canary passes. Do not
   delete it.

## Rollback

Originals are never changed. To roll back a step, reactivate the paused Cloud
twin and delete only the corporate copies created under the recorded
idempotency keys. Do not delete a Cloud workflow or Data Table until the
cancellation conditions above are all met.
