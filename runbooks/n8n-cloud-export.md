# n8n Cloud export (read-only, bounded)

Status: **built and tested, not dispatched.** Nothing here has read n8n Cloud.

**This repository is public.** Its build logs and artifacts can be read by anyone,
so the workflow prints only aggregate facts and uploads **only ciphertext**. The first
design of this tool uploaded a full snapshot as an artifact and described the
repository as private; that was wrong and was fixed before merge.
The code is `scripts/n8n_cloud_export.py`, the workflow is
`.github/workflows/n8n-cloud-export.yml`, and the tests are
`scripts/test_n8n_cloud_export.py`.

Purpose: give the content migration the evidence it is missing before any
workflow moves or the Cloud plan is questioned. That means the live workflow
definitions, the run times of MM-20 and MM-21 to line up with the Drive file
creation times, and the eight Data Tables that only exist inside Cloud. See
[`content-drive-migration.md`](content-drive-migration.md).

This is not the self-hosted driver. It has its own environment, secret and
base URL, and it refuses any address that is not an `*.n8n.cloud` origin.

## What it can do

Only HTTP GET, only these paths. Anything else is refused before a request is
made, and each refusal is covered by a test.

| Reads | Path | Limits |
|---|---|---|
| Workflow list | `/workflows` | `limit`, `cursor` only |
| Each workflow definition | `/workflows/{id}` | none |
| Execution metadata | `/executions` | `includeData=false` is mandatory; `workflowId` must be MM-20 (`gJMEoOMUI0uS0D1Y`) or MM-21 (`kB21JwdMDZt7nT5a`) |
| Data table list | `/data-tables`, or `/datatables` if the first is absent | `limit`, `cursor` only |
| Rows of one table | `{table path}/{id}/rows` | only for the eight tables below, resolved by exact name |

The two data table paths exist because n8n's own page names `/datatables` and
other references name `/data-tables`. The tool tries them in that fixed order
and uses the first that answers. Preflight prints which one did.

Eight tables, by exact name: `Raw_Media`, `Content_Cases`,
`Photo_Content_Queue`, `Publication_Radar`, `Publication_Analysis`,
`Website_Content`, `Website_Content_Sync`, `LinkedIn_Drafts`. Every other table
is listed by name only to find these and is never read. Two tables with the
same name stop the run. A missing table is reported, and the result is
"partial" with a non-zero exit.

## What it never reads or writes

- Credentials, users, variables, audit, tags, projects, or any write endpoint.
- Execution payloads. `includeData` is forced to `false`, and only these
  fields of an execution are kept: `id`, `workflowId`, `mode`, `status`,
  `finished`, `startedAt`, `stoppedAt`, `retryOf`, `waitTill`.
- Pinned test data and static data in workflow definitions are dropped.
- Credential references in workflow nodes are cut down to `id` and `name`.
- Values that look like secrets (bearer or basic headers, JWTs, private key
  blocks, long mixed letter-and-digit values under a key named like a key,
  token, secret, password, authorization or cookie) are replaced with
  `[redacted]`. Expressions that start with `=` are kept.
- The same masking runs over every Data Table cell. The count per workflow or
  table is in the manifest. This is best effort and is not a promise that
  nothing sensitive remains: treat the output as confidential business data.

## What is printed, and where the full snapshot may go

The workflow offers three operations. The first two upload nothing.

- `preflight`: whether the key works, that the account is the legacy Cloud one,
  the workflow count, the data table path that answered, and which of the eight
  tables exist.
- `evidence`: for MM-20 and MM-21 only, the graph shape (node names and types,
  schedule numbers, the called workflow id, the data table and column names with
  whether each is mapped from the incoming item, whether a Drive node writes to
  the root folder, the connections), run counts, start slots and times, and the
  statuses of `Publication_Radar` rows. For each radar row it prints the
  publication id, status, discovery time and whether `drive_file_id` and
  `analysis_id` are empty. It prints no title, abstract, note, parameter value,
  payload or Drive id; values that do not match a strict safe pattern print as
  `[not printed]`. A test feeds it sentinel text in every such field and checks
  none appears.

### The encrypted snapshot (`export-encrypted`)

The full snapshot writes one cleaned file per workflow, the two execution
lists, one file per table and a `_manifest.json` with counts, redaction counts
and SHA-256 hashes. On the runner it is written to a temporary directory, packed,
and encrypted with a fresh random AES-256 key (PBKDF2, 200000 rounds). That key is
wrapped with the **public** RSA key in `registry/n8n-export-transport.pub.pem`
(4096 bit, OAEP with SHA-256). The plaintext directory, the archive and the AES
key are deleted before upload. Only `export.tgz.enc` and `key.enc` are uploaded,
as the artifact `n8n-cloud-export-encrypted-<run id>`, kept 3 days.

Anyone can download those two files from this public repository and none can read
them. The matching private key is generated and held on the machine of whoever
analyses the snapshot, outside every repository and every GitHub secret. If it is
lost, the snapshot is unreadable and a new one is made; to change the key,
replace the public key file in a reviewed PR.

Open it locally, in an empty directory outside any repository:

```
openssl pkeyutl -decrypt -inkey <private key> -pkeyopt rsa_padding_mode:oaep -pkeyopt rsa_oaep_md:sha256 -in key.enc -out session.key
openssl enc -d -aes-256-cbc -pbkdf2 -iter 200000 -in export.tgz.enc -out export.tgz -pass file:session.key
tar -xzf export.tgz
```

What is in it is still business data: workflow definitions with credential
references (id and name only), execution metadata of MM-20 and MM-21, and the
rows of the eight tables. The decrypted files must not be committed to this
repository or posted. Cleaned workflow definitions that are needed for review go
into the private `adapteng-automation-platform` repository, not here.

The script itself refuses `export` unless `EXPORT_DESTINATION_PRIVATE=true`; the
workflow sets that only for `export-encrypted`.

## How to review it before the first run

1. Read `scripts/n8n_cloud_export.py`: `PATH_RULES`, `check_request`,
   `EXPORT_TABLES`, `CORRELATED_WORKFLOWS` and `clean_workflow`.
2. Run `python -m unittest scripts.test_n8n_cloud_export`. It covers wrong
   hosts, non-GET methods, forbidden paths, execution payload requests,
   non-allowlisted tables, redaction, the 401 message, the missing-table result
   the evidence output (no content), the export gate and the workflow file (manual
   dispatch only, own environment, ciphertext-only upload, no use of
   the self-hosted key).
3. Dispatch `preflight` first. It checks the key, confirms the account is the
   legacy Cloud one (the known MM-08 workflow is present and no self-hosted
   workflow is), reports which of the eight tables exist and writes nothing.
4. Only then dispatch `evidence`. `since` defaults to 2026-06-01, before the first
   source package files.
5. Dispatch `export-encrypted` only when someone holds the private key and is
   ready to open the result.

## What the 401 turned out to be

- The `n8n` connector on this machine is a user-configured MCP link to an
  endpoint on the Cloud instance. Its single tool forwards GET requests to the
  Cloud public API under `/api/v1`. A request for `/workflows` reaches the real
  route and is answered `401 Unauthorized`; a request that repeats `/api/v1`
  gets `404 not found`. So the link, the path handling and the Cloud API are all
  working, and the key held on the Cloud side of this link is the part that is
  rejected. It was retried more than once and answered the same each time.
- The local Claude config entry holds no Cloud API key. The only value in it is
  the header that authenticates to the MCP endpoint, which accepts it. Repairing
  the 401 therefore cannot be done from this repository or this machine.
- The live MM-20 graph was read through some connection on the owner's side that
  works. No such connection is attached to this session (the session lists the
  `n8n` entry and no other n8n connector), so it is a different path, or a key
  that is valid there.
- n8n API keys carry an expiry chosen at creation, which would explain a key
  that worked earlier and now does not. That is a likely cause, not a confirmed
  one.
- The registry records that the Cloud MCP gateway lets the model pick any read
  path and was frozen for that reason. Repairing it is not recommended; this
  allowlisted export is the replacement.

## Smallest next owner action

Either of these ends the 401 problem. The first needs no new key.

1. If the connection you used to read MM-20 can be added to this session, add it
   and say so. No credential is created. I can then read definitions and run
   history directly. Table rows still need the key, because the connector tool
   only forwards GET requests and the key is what it lacks.
2. Otherwise follow the private procedure below, which is the one setup the
   export needs.

## Private setup procedure (only if a new key is needed)

Do not call the key read-only. n8n's documentation says scoped keys are an
Enterprise feature and that keys on other plans have full access to the account.
The Cloud plan is not recorded, so assume full access. The limits come from the
export tool, not from the key.

1. In n8n Cloud, open Settings, then Usage and plan, and note the plan name. If it
   is not Enterprise, the key will have full access.
2. In n8n Cloud, open Settings, then n8n API, and create a key labelled
   `company-os-migration-export` with an expiry of 7 days. Copy it once.
3. In GitHub, open `Ivan-Shyla/adapteng-company-os`, Settings, Environments, and
   create an environment named `n8n-cloud-export`. Restrict deployment to the
   `main` branch.
4. In that environment add the **secret** `N8N_CLOUD_API_KEY` (paste the key here
   and nowhere else) and the **variable** `N8N_CLOUD_BASE_URL` set to the bare
   origin `https://ivanshyla.app.n8n.cloud`.
5. Reply "ready". Do not send any value.
6. After the export, delete the key in Settings, then n8n API. It also lapses on
   its own after 7 days.

Do not touch `X_N8N_API_KEY` or `N8N_BASE_URL`; they belong to the self-hosted
instance. The credential the Cloud MCP link uses is left as it is.

The workflow file can be dispatched only after this change is merged to `main`
and the environment exists. Neither has happened.
