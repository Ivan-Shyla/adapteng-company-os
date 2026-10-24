# Article and marketing content: personal Drive to the company Shared Drive

Status: **prepared, not executed, and incomplete.** Nothing was copied, created, moved,
activated or cancelled. The recurring writer is now identified from live Cloud
evidence (below); the plan stays open until the fixes and the remaining gates are done. The reviewed plan is
[`registry/content-drive-migration.json`](../registry/content-drive-migration.json),
checked by `scripts/content_migration_manifest.py`. Evidence date: 2026-10-24.

This runbook extends [`company-drive.md`](company-drive.md) and
[`company-administration.md`](company-administration.md). It does not replace
their rules: the personal Drive stays a read-only source, files are copied and
never moved, and each workflow is rewired alone.

## What the evidence changed

| Starting assumption | What was found |
|---|---|
| MM-01, MM-21, MM-23 and MM-24 are live on `n8n.adapteng.com` | The self-hosted status operation (run 39605383384, `confirmed_self_hosted=true`) lists **six** workflows, none named MM. The MM workflows run on n8n Cloud. |
| MM-21 is active but manual | The repository export is paused and manual, but **live MM-21 is active** and is called by live MM-20 every Monday. 44 source package files sit in the personal My Drive root, written in 4s per Monday run from 2026-09-28 to 2026-10-19 and in similar numbers before. The repository exports of MM-20 and MM-21 are stale against the live graphs. See below. |
| Outputs go to Baserow | MM-01, MM-15, MM-21, MM-23, MM-23C and MM-23D write to **n8n Data Tables** (Raw_Media, Content_Cases, Photo_Content_Queue, Publication_Radar, Publication_Analysis, Website_Content, Website_Content_Sync, LinkedIn_Drafts). Those tables live inside the n8n instance that runs them. |
| The Google connection n8n uses may reach the Shared Drive | Recorded in `owner/action-items.md` as **proven false on 2026-07-26**: the legacy Drive OAuth connection reads a personal My Drive and has no corporate Shared Drive write access. Nothing seen since contradicts that. It was not retested here. |
| Case media belongs under `40_Content` | `company-drive.md` places case photos and videos under `00_Case_Uploads` and `30_Projects_Cases`. `40_Content/<article>/01_Source` holds only a link manifest to the case. The manifest follows the runbook. |

## Evidence table

| Current source | Corporate destination | n8n workflow change | Duplicate guard | Verification |
|---|---|---|---|---|
| 3 review Google Docs (unpublished drafts, titles withheld because this repository is public), personal My Drive root, written by MM-23C | `40_Content/AE-CGR-NNNN_<slug>/03_Review`, one folder per article | MM-23C writes to the article `03_Review` folder and looks up `website_id` first. MM-23D takes the corporate document ID. Both stay inactive until the canary. | `legacy_key`, `source_sha256`, reserved `AE-CGR` ID; replay creates nothing | Names, types and export SHA-256 match the manifest; originals untouched; replay copies zero files |
| 3 matching source package `.txt` files created 2026-06-15 | `01_Source` of the matching article | MM-21 writes to `01_Source`, not the My Drive root | Same, plus a DOI cross-check | Same |
| 39 recurring source packages, 5 distinct DOIs (one DOI repeated 20 times), written by live MM-20 then MM-21 | None yet | MM-20 stops writing protected fields on existing rows; MM-21 reads candidates once and drops repeated `pub_id`; MM-21 checks the radar row and the destination folder before writing; the owner pauses or gates MM-20's Monday schedule first | `rediscovery_row`, `protected_field_mappings`, `dedupe_candidates`, `should_write_source_package`; `plan_copy` for the copy itself | The radar rows of the publications approved on 2026-10-19 are unchanged after the 2026-10-26 run, and a test run on the rewired path creates one file per publication |
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

### What was read from n8n Cloud

Two governed runs of the `n8n-cloud-export` workflow on `main` (preflight run
40084327093 and evidence run 40084363965) printed structure, run times and
status counts only. Cloud has 146 workflows, 32 active. Seven of the eight
migration Data Tables exist; **`Website_Content_Sync` does not**, so MM-23D has
no table to write to today.

Live MM-20 Publication Scout is active. Its Monday 07:00 trigger fires at
**02:00 UTC**, so the Cloud workflow time zone is five hours ahead of UTC and
that earlier open question is closed. After discovery it upserts
`Publication_Radar`, a filter auto-approves candidates, a cap node limits this
to 2 per run, `Set Approved For Fetch` writes the status, and the chain then
calls MM-21, MM-22 (disabled), MM-23 and **MM-24**. Live MM-20 also has OpenAlex
and Europe PMC searches, a DOI-based dedupe key step and a Telegram digest;
none of those are in the repository export.

Live MM-21 is active and called by MM-20. It writes each source package to the
My Drive root and then writes `drive_file_id`, `status` and `notes` back.

### What the evidence shows

- MM-21 ran on 2026-10-19, 10-12, 10-05 and 09-28, each at 02:00:34 UTC. The
  source files of those days were created seconds later, **4 per Monday**,
  which is 2 publications twice each. Earlier history is not retained by Cloud.
- The radar has 254 rows and no repeated `dedupe_key`. Statuses: 116
  `keep_in_radar_only`, 67 `review_required`, 62 `candidate_for_fetch`, 4 `new`,
  3 `analyzed`, 2 `approved_for_analysis`. Only 5 rows are in a processed state,
  yet 44 source files exist and about ten distinct sources were fetched. That
  fits publications being reset to a fetchable state after each fetch.
- The live upsert maps `status`, `drive_file_id` and `analysis_id` from the
  discovered item, whose last two are empty (confirmed by the owner).

### Two causes that multiply (reset is inferred, fan-out is inferred)

1. **Reset.** Rediscovery overwrites the status and clears the Drive and
   analysis ids of an existing row, so the same top-scored publications pass
   the auto-approval filter again every Monday.
2. **Fan-out.** MM-21's table read runs once per incoming item, so two approved
   items return the two candidates twice and the Drive node writes four files in
   one execution.

Status: the writer is **identified**. The reset itself has not been seen as a
before and after change on one row, so it is called strongly supported, not
proven. The check costs nothing: snapshot the two rows approved on 2026-10-19
now and again after the 2026-10-26 02:00 UTC run, changing nothing.

### Not yet known

- Whether MM-23 and MM-24, which run after MM-21 every Monday, created website
  draft rows, LinkedIn draft rows or WordPress drafts. The 2026-10-05 and
  2026-09-28 runs ended in an error after the files were written.
- Whether the fan-out explanation is exactly right; it is an inference from 4
  files, 1 execution and a cap of 2.

### Rules the rewired path must meet

All are in `scripts/content_migration_manifest.py` with tests.

- Discovery inserts a row only when none exists and never writes `status`,
  `drive_file_id` or `analysis_id` on an existing row (`rediscovery_row`,
  `protected_field_mappings`). A reviewer can run the second function on the
  upsert node's column mapping; it must return an empty list.
- MM-21 drops repeated `pub_id` values from its candidates before writing
  (`dedupe_candidates`).
- MM-21 writes only when the radar row has no `drive_file_id` and the
  destination folder holds no file for that `pub_id`
  (`should_write_source_package`).
- Until these hold, the Cloud schedule for MM-20 is paused or gated by the
  owner. This plan does not pause it; that is a change to a live Cloud workflow
  and stays with the owner. Pausing is reversible and stops new duplicates and
  any weekly WordPress draft creation.

The manifest carries `completion_gates`. One is satisfied (the writer is
identified); seven are open. The plan is **not complete** while any gate is open,
and the validator rejects a manifest that says it is.

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
  audit recorded 89 non-archived workflows, 31 active; the live read on
  2026-10-24 shows 146 workflows, 32 active. Ending a Cloud plan
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

## n8n Cloud access

Cloud is now readable through the governed `n8n-cloud-export` workflow
([`n8n-cloud-export.md`](n8n-cloud-export.md)): a short-lived key stored as an
environment secret, GET-only, log-safe output. Preflight and one evidence run
succeeded on 2026-10-24.

The `n8n` connector on this machine still answers 401. It is an MCP link to a
Cloud workflow whose own stored key is rejected; it does not use the new key and
nothing here repairs it. That link was frozen in the registry for letting the
model choose any read path, so it is left as it is.

Plan and scopes. n8n documents scoped API keys as an Enterprise feature, and
the Cloud API key screen shows a Scopes column, so scoped keys appear to be
available on this account. The plan name is still not recorded; read it from
Settings, Usage and plan. A key for the export should carry only read scopes for
workflows, executions and data tables.

Two keys were pasted into a chat during setup. Treat both as exposed: delete the
broad key (full scopes, long expiry) and delete the export key once the work that
needs it is done. They were never written to a repository or a log.

## Owner actions

1. Delete the two exposed Cloud API keys (Cloud, Settings, n8n API) when the
   reads below are finished, and create a new short-lived read-scoped key only
   if more reads are needed.
2. Pause or gate **MM-20's Monday schedule** on Cloud. This stops new duplicate
   files and any weekly MM-23/MM-24 side effects while the fixes are built. It is
   reversible and is a change to a live Cloud workflow, so it stays with the owner.
3. Before the 2026-10-26 02:00 UTC run, confirm whether a snapshot of the two
   radar rows approved on 2026-10-19 can be taken by the evidence workflow (it
   can; ask and it will be run twice, before and after, without changing
   anything), or pause the schedule first and skip that check.
4. Record the Cloud plan name, monthly cost and renewal date (Settings, Usage and
   plan), and the same for the self-hosted instance.
5. Decide whether `Website_Content_Sync` should exist: MM-23D writes to it and
   Cloud has no such table.

Later, the Drive side needs this batch:

| Provider and screen | Name to set | Purpose | Minimum scope | Verification |
|---|---|---|---|---|
| GitHub, `adapteng-automation-platform`, Settings, Environments | The governed Drive bridge environment values for the first article folder | Lets `ensure_folder` run for one article | One environment, not repository-wide | The folder appears once, and a replay creates nothing |
| Google Workspace, Shared Drive `AdaptEng Company` | Membership for the company service account on the content folders | Lets the adapter write | Contributor on `40_Content` and `00_Case_Uploads` only | A read-only listing from the adapter shows the folders |

## Order of work

1. Done: the writer is identified from live Cloud evidence. Next, have the owner
   pause or gate MM-20's Monday schedule, then refresh the repository exports of
   MM-20, MM-21, MM-23 and MM-24 from the live graphs (they are stale) and rewire
   MM-20 and MM-21 to the rules above. Table contents for the migration move by a
   separate private path, never through this public repository.
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
