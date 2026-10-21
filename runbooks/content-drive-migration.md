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
| MM-21 is active but manual | The repository export is paused with a manual trigger, yet 44 source package files were written to the personal My Drive root, mostly at about 02:00 UTC on Mondays from 2026-07-27 to 2026-10-19. A scheduled live variant or another writer exists that the repository does not contain. It could not be identified because Cloud returned 401. |
| Outputs go to Baserow | MM-01, MM-15, MM-21, MM-23, MM-23C and MM-23D write to **n8n Data Tables** (Raw_Media, Content_Cases, Photo_Content_Queue, Publication_Radar, Publication_Analysis, Website_Content, Website_Content_Sync, LinkedIn_Drafts). Those tables live inside the n8n instance that runs them. |
| The Google connection n8n uses may reach the Shared Drive | Recorded in `owner/action-items.md` as **proven false on 2026-07-26**: the legacy Drive OAuth connection reads a personal My Drive and has no corporate Shared Drive write access. Nothing seen since contradicts that. It was not retested here. |
| Case media belongs under `40_Content` | `company-drive.md` places case photos and videos under `00_Case_Uploads` and `30_Projects_Cases`. `40_Content/<article>/01_Source` holds only a link manifest to the case. The manifest follows the runbook. |

## Evidence table

| Current source | Corporate destination | n8n workflow change | Duplicate guard | Verification |
|---|---|---|---|---|
| 3 review Google Docs (CFD flue-gas flow, flue gas temperature and dust, common CEMS problems), personal My Drive root, written by MM-23C | `40_Content/AE-CGR-NNNN_<slug>/03_Review`, one folder per article | MM-23C writes to the article `03_Review` folder and looks up `website_id` first. MM-23D takes the corporate document ID. Both stay inactive until the canary. | `legacy_key`, `source_sha256`, reserved `AE-CGR` ID; replay creates nothing | Names, MIME types and export SHA-256 match the manifest; originals untouched; replay copies zero files |
| 3 matching source package `.txt` files (SSRN 6086433, Bitlis Fen 1719869, EPE 214925) | `01_Source` of the matching article | MM-21 writes to `01_Source`, not the My Drive root | Same, plus DOI cross-check | Same |
| 39 recurring source packages, 5 distinct DOIs (one DOI repeated 20 times) | None yet | Find the scheduled writer; add a `pub_id` and DOI existence check before any Drive write | `plan_copy` skips repeated file IDs and repeated (article, hash) pairs | Owner picks which sources become articles; copy at most one canonical file per DOI |
| 2 test fixtures; 2 unrelated Russian-language documents | None | None | Excluded in the manifest | Not applicable |
| Case media `CASE_GMS810_WOHLER_001`: 3 photos, 1 video (about 140 MB), 1 case note, in the watched personal `01_Inbox` | `00_Case_Uploads/CASE-YYYY-NNN`, link manifest in `40_Content` | MM-01 gets a `drive_file_id` existence check before both inserts, then its trigger moves to the corporate intake folder. MM-15 stays paused because it watches the same folder. | `drive_file_id` against Raw_Media and Content_Cases | Held until redaction review (EXIF/GPS and visual) is confirmed; then counts and hashes match |
| Legacy tracker workbook `AdaptEng Marketing System` | None yet | None | Not applicable | Owner decides keep, archive or copy |
| 21 published website posts (7 topics in EN, CZ, RU); none matches the three review docs | Receipts only, in `05_Published`, when each article has a folder | MM-24 unchanged; final publishing stays manual | Slug and WordPress post ID | Public sitemap count equals the manifest |
| Data Tables on n8n Cloud | Self-hosted n8n Data Tables | Export from Cloud, import to self-hosted before any workflow moves | Table row counts before and after | Row counts equal; sample rows compared |

MM-41 Case Pipeline Orchestrator (`z2dYx1mvy9rXOsLY`) has no repository export and
is safety-frozen. Export and sanitize it before deciding anything. MM-03 is
inactive; no LinkedIn autopublishing is assumed.

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

## Owner actions

Single next action: **re-establish read access to n8n Cloud.** The n8n connector
returns 401, so Cloud workflows, schedules and Data Tables cannot be read.
In n8n Cloud, open Settings, then n8n API, create a key, and store it in the
connector by reference only; do not paste it in chat. If the key cannot be
limited to read access on the current plan, say so and the export will be done
through a read-only operation instead.

The same visit settles the subscription question: record the plan name, monthly
cost and renewal date from Settings, then Usage and plan, on both Cloud and the
self-hosted instance.

Later, once that is done, the Drive side needs one more batch from the owner:

| Provider and screen | Name to set | Purpose | Minimum scope | Verification |
|---|---|---|---|---|
| GitHub, `adapteng-automation-platform`, Settings, Environments | The governed Drive bridge environment values for the first article folder | Lets `ensure_folder` run for one article | One environment, not repository-wide | The folder appears once, and a replay creates nothing |
| Google Workspace, Shared Drive `AdaptEng Company` | Membership for the company service account on the content folders | Lets the adapter write | Contributor on `40_Content` and `00_Case_Uploads` only | A read-only listing from the adapter shows the folders |

## Order of work after the owner action

1. Export Cloud workflows and Data Tables (read-only), diff them against the
   repository, and identify the Monday 02:00 writer.
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
