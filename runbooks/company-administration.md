# Company administration foundation — 2026-10-21

Status: migration design and source inventory only. No corporate files moved, permissions changed, reminders activated, or company facts published by this runbook.

## Authority and current access

- The legal/administrative source is the owner-controlled `S.R.O` folder in personal My Drive. The connected Drive account sees that folder and its eleven numbered first-level sections, but no Shared Drives. Its root and sampled passport and bank-reference files currently have “anyone with the link: reader” access. Remove that link permission before copying sensitive material; verify permission inheritance and each sensitive file.
- The destination is the already-provisioned organization-owned Shared Drive `AdaptEng Company`, with eight governed aliases in [company-drive.md](company-drive.md). It is not visible through the currently connected personal Drive session. Do not create a second corporate drive.
- An externally owned personal folder cannot be moved into a Workspace Shared Drive. Copy approved files from a corporate identity into the existing Shared Drive, compare inventory and source/destination content, then leave originals in place until an explicit retirement decision. Do not use a personal OAuth credential as the long-term company write identity.
- The personal source includes live legal/tax/bank records, personal identity documents, formation drafts, OSVČ transition material and archives. The entire tree is not a general AI knowledge base.

## Classification and destination

| Source material | Destination alias | Handling |
|---|---|---|
| Final company registration, trade licence, approved public legal facts | `company-drive://10_Company` | Official versions only; mark issuer, date, review owner and current/superseded state |
| Tax registrations, filings, datovka receipts | restricted area under `company-drive://10_Company` | Director/accounting access; separate deadlines from the stored document |
| Business bank agreement and current official bank confirmation | restricted area under `company-drive://10_Company` | Approved payment fields may be exposed to proposals through a separate approved facts view; never expose the bank contract |
| Director/shareholder passports, residence IDs, birth-number documents, powers of attorney | restricted identity/formation area under `company-drive://10_Company`, only if needed for company compliance | No access for the general AI assistant or proposal generator; otherwise retain in a restricted personal archive |
| Current company name/address/IDs/contact/payment instructions | approved “Company Facts” register under `company-drive://10_Company` | Per-field source, owner, verification date, validity and approval required |
| Logo, brand assets, approved proposal/invoice templates | `company-drive://50_Templates` | Versioned approved set; previous variants to `90_Archive` |
| Approved proposals and commercial correspondence | `company-drive://20_Commercial` | Per opportunity; no auto-send |
| Draft notary documents, superseded forms and old OSVČ files | `company-drive://90_Archive` or remain personal/restricted | Never selected as current company fact |

Copy one class at a time; source and destination manifests must agree in count, name, MIME type and checksum where bytes are available. Resolve duplicate or contradictory official versions with the owner. Do not delete or move the source in the first pass.

## System responsibilities

| System | Responsibility |
|---|---|
| Shared Drive | Canonical files, signed originals, approved facts register, brand and template assets |
| Baserow `Documents_Evidence` | Document index: status, classification, approved version, Drive link, verification date |
| Baserow `Actions` | Deadline and task: owner, due date, completion state and linked document |
| n8n self-hosted | Daily deadline scan, freshness/difference checks and a digest to the owner; replay-safe, no legal submission |
| Postgres `adapteng_ops` | Run IDs, idempotency, approval/cost evidence if the workflow needs durable execution state |
| AI Gateway / agent-runtime | Optional extraction or drafting from allowlisted, approved source files only; bounded cost and owner review |
| GitHub | Schemas, field ownership, workflow export, tests and runbook, never company documents or account/identity values |

The existing eight-table Baserow schema already contains `Actions.due_at` and `Documents_Evidence` status/classification/verification/Drive-reference fields. Reuse these before introducing a new table. The proposed low-frequency “Company Facts” register is a restricted, versioned Drive Sheet or equivalent approved structured document. Its fields are `key`, `value`, `source_document`, `valid_from`, `verified_at`, `approved_by`, `status`, `classification`; a bank-account fact has a narrower reader group than a public legal name. This is a design contract, not a claim that the register exists today.

## Administrator workflow, phase 1

1. A human registers an obligation in `Actions` with deadline, owner, source document and recurrence rule; the agent must not infer a statutory date from an old draft.
2. n8n scans due actions daily and reports due soon, overdue, missing owner or missing source. It never marks a filing submitted from a draft alone.
3. On a new official file, the assistant extracts *candidate* fields and a source citation into a review queue. The owner approves facts and their effective date before the active register changes.
4. Each reminder cites the exact action and official file. After filing, a human attaches the submission/receipt, verifies it, and closes the action. Repeatable annual obligations are scheduled from an owner-approved rule.
5. A proposal generator reads only the currently approved facts and templates; it records the versions used, prepares a draft in `20_Commercial` and waits for Ivan's approval before issue/send. Identity/KYC, tax filings and archived drafts are excluded.

No autonomous tax/legal filing, bank update, email to a third party, public publication, or silent change of official details.

## Execution order and acceptance

1. Restrict the link access on the personal source and sensitive descendants, preserving named-person access needed for migration. Verify access by a second read.
2. Connect a corporate Workspace identity with access to the existing `AdaptEng Company` Shared Drive. Verify drive identity and eight folders; use a corporate service account with approved delegated scope for automation, not the personal account.
3. Inventory and classify source files; resolve the legal entity and bank-reference versions against signed/current official documents.
4. Copy an approved small batch to the correct destination alias; compare manifests and confirm originals are unchanged. Then migrate remaining eligible classes. No bulk folder move.
5. Publish the approved Company Facts register and approved templates with restricted readers; link them in Baserow.
6. Add the deadline workflow inactive, test with synthetic dates and an owner-only digest, then activate it after reviewing one real obligation.
7. Test one draft proposal that uses exact approved name, address, bank details, logo and template versions, with no archived or identity documents in the input.

Completion requires corporate ownership, no public-link exposure of identity/bank documents, verified copy receipts, at least one owner-approved deadline with a working reminder, and a draft proposal with cited current facts. If any verification fails, stop the batch; originals remain the rollback source.
