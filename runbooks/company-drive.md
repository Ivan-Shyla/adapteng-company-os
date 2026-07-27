# Runbook — company Drive and daily file placement

The company document authority is the organization-owned Shared Drive
**AdaptEng Company**:

<https://drive.google.com/drive/folders/0AC0RFKG8iI-TUk9PVA>

The drive and all eight standing folders were created live and re-verified by
the sanctioned read-only provisioning dry run on 2026-07-26. New company files
must not be created in a personal My Drive.

## Where to put a file

| Material | Corporate destination |
|---|---|
| New case photos/videos and intake note | [`00_Case_Uploads`](https://drive.google.com/drive/folders/1mickIyT8nkiiYPt8xjcH8iLcg2Cbbq-K) |
| Unsorted controlled input | [`01_Inbox`](https://drive.google.com/drive/folders/1pEvvJuIwb6iiRuyxA9rRk5HUZ5xT-vQF) |
| Company policies, approved claims, legal-entity files | [`10_Company`](https://drive.google.com/drive/folders/1qm0ilYsy0muHBoXxfJcw3JPLQgywsZKH) |
| Proposals, partner and RFQ material | [`20_Commercial`](https://drive.google.com/drive/folders/1U7Xq2aTEaFWccG39HIKsShhp5Ued60oS) |
| Active delivery/project/case evidence | [`30_Projects_Cases`](https://drive.google.com/drive/folders/1GK8lS8gfMDyKLnlmho6_QqT6vkbvryGu) |
| Case, article and channel drafts | [`40_Content`](https://drive.google.com/drive/folders/1vV163MvgmdLlYAAjZZq7KzMFw-QFTw13) |
| Approved reusable templates | [`50_Templates`](https://drive.google.com/drive/folders/10rRpyG19cfqgPuny6PYA68lAxni4BPf1) |
| Closed/obsolete material | [`90_Archive`](https://drive.google.com/drive/folders/1SGe_5JeJmRLTrnLek6Q-ewb_jvh_qwfu) |

## New case intake

1. Under `00_Case_Uploads`, create one folder named `CASE-YYYY-NNN`.
2. Upload the original photos/videos without renaming them.
3. Add `CASE_NOTE.md` with only facts that may be used in a draft.
4. Add `READY_FOR_INTAKE.json` only when the source set is complete.
5. Do not place client-confidential evidence in a model path until its
   classification and allowed-use decision are recorded.
6. Keep the originals. Processing creates copies/derived artifacts; it never
   moves or deletes the intake source.

`CASE-2026-001` is the first governed raw-source/case migration and
evidence-bounded deterministic case draft. Its legacy folder contains one intake
marker, one note, four HEIC images and two MOV videos. The source remains
untouched until the governed service-account copy is verified. All media and
publication records remain blocked until a human reconciles the live Sheet
redaction state with Git.

This CASE is not the first live model proof. The planned proof uses exact,
already-approved/published July public article-radar package `ART-2026-001` and
approved source set `SRC-2026-001`. It may write only a new pending/draft
artifact through the canonical Company OS gateway, AG-008 and governed Company
Drive path; historical publication does not authorize republication. Never
reactivate or route around frozen direct-model workflow MM-22.

## Content lifecycle

One topic uses one folder under `40_Content`:

```text
AE-CGR-NNNN_short-title/
├── 01_Source/
├── 02_Drafts/
├── 03_Review/
├── 04_Approved/
└── 05_Published/
```

- `01_Source`: approved references or a manifest linking to the case source.
- `02_Drafts`: editable working drafts; never treated as approved.
- `03_Review`: the version Ivan is reviewing.
- `04_Approved`: approval-adapter output only; content-addressed snapshot.
- `05_Published`: publication receipt/export, not an alternate source of truth.

Baserow `Content_Items` stores status, owner and links. Drive stores the actual
file. Postgres stores run/approval/cost evidence. Git stores only schemas,
policies and sanitized fixtures.

## Automation implementation gate

The adapter currently on automation-platform `main` is **folder-only**: it can
find/create case/content folders and provision the eight-folder base structure.
It has no general file/tree listing, file copy, pending-artifact creation or
deterministic partial-failure replay state. The successful base-structure and
folder smoke does not prove any of those capabilities.

Current repository code also expects `GOOGLE_SERVICE_ACCOUNT_JSON` plus
`GOOGLE_WORKSPACE_ADMIN`; the actual runtime contract is
`GOOGLE_SERVICE_ACCOUNT_JSON_B64` plus `GOOGLE_WORKSPACE_DELEGATED_USER`. No
reviewed safe PR-A/PR-B and no live company file copy exists. Open implementation
attempts are not deployment/readiness evidence.

Delivery is strictly ordered:

1. **PR-A — library/controlled execution:** typed allowlisted copy and
   pending-artifact operations, Google client, deterministic partial-failure
   replay, the actual B64/delegated-user env config, and dispatch/CLI.
2. Review and accept PR-A as a standalone non-live change.
3. **PR-B — service:** only after that review, stack the bearer-authenticated
   internal HTTP service on PR-A.
4. Approve any deployment or controlled copy separately; neither PR authorizes a
   live write.

## Transition rule

The old personal Drive is **read-only migration source**, not the current
company workspace. Several n8n Cloud workflows still point at its legacy
`01_Inbox` / `00_Case_Uploads`; therefore:

- do not upload new company material there;
- do not delete or move legacy originals during migration;
- copy with the company service account, verify destination metadata/count,
  then rewire one workflow at a time;
- disable the cloud twin only after a successful self-hosted shadow/canary and a
  documented rollback.

The permanent runtime credential is
`adapteng-ai-operator@adapteng-workspace-automation.iam.gserviceaccount.com`
through the locked Coolify secret `GOOGLE_SERVICE_ACCOUNT_JSON_B64`, with
delegated-user config in `GOOGLE_WORKSPACE_DELEGATED_USER`. Personal OAuth must
not become the long-term write credential.

## Verification after a copy

1. Destination is under `AdaptEng Company`, not My Drive.
2. File count, names and MIME types match the approved source inventory.
3. The source still exists and was not modified.
4. A replay creates no duplicate folder/file.
5. Any media intended for publication has a human redaction/privacy review.
6. Baserow receives only stable IDs, status and Drive links — never the binary.
   For automatic case/content/document creation, reserve/reuse the business ID
   from a SHA-256 source identity first. Never retry a COUNTER upsert with
   `business_id` omitted: that operation intentionally allocates a new ID.
