# Runbook — n8n governed workflow operations

How to build, activate and debug a **governed** self-hosted n8n workflow and
record n8n Cloud containment without confusing repository evidence with a live
runtime change. Proven building AUT-001 (`NsWG1hD8VmIRRwCv`) and WEB-002
(`05ytz5If9kHUOYuA`).

## Principles

- Governed workflows never write business systems directly. They call the
  **baserow-adapter** (`http://adapteng-baserow-adapter:8080/v1/upsert`) over
  the internal `coolify` network — no public exposure.
- Every externally-reachable webhook is **header-authenticated**
  (`X-Webhook-Token` via an n8n `httpHeaderAuth` credential). No token → 403.
- The workflow must **fail closed**: a downstream error must return a non-2xx
  so the producer retries, never an empty 200 (see "The 200-on-error trap").

## n8n Cloud containment and MCP closure

Editing this runbook or the registries changes repository status only. It does
not change n8n Cloud. The supplied 2026-07-27 live evidence is:

- a fresh audit reconfirmed 89 non-archived / 33 active;
- reversible freeze-now actions produced 89 non-archived / 31 active /
  58 inactive, with chain `42 → 40 → 38 → 37 → 36 → 35 → 34 → 33 → 31`;
- MM-Visual-Evidence-Intake `uBVRMTCKwnUG91kU` and MM-08
  `RAPjKSnj6EY7axtb` are unpublished and reject manual/draft and production
  probes without creating an execution; no post-freeze executions appeared;
- already-inactive MM-10 `39CAjeKcZD64VM25` and MM-29
  `at9H54krWF9ULdtT` received defense-in-depth node disabling with no count
  effect.

Keep the two latest freezes reversible. Retain MM-Visual's old published version
for audit and keep its Manual, Telegram and worker nodes disabled. Do not
reactivate it until a founder/principal allowlist precedes every command and
media-worker is reachable only from validated-ready output. Keep MM-08's
webhook and lead-write nodes disabled; replace it only with authentication,
schema validation, rate limiting and stable deduplication.

MCP exposure is a separate control plane and remains unresolved. Audited
workflows still have **Available in MCP** enabled because the available update
API rejected that unsupported field. Official n8n semantics require
instance-level MCP, per-workflow availability and an authenticated user
together to expose supported workflows. Disabling **Available in MCP** does not
stop ordinary webhook, schedule, manual or internal triggers.

Owner closure sequence:

1. Disable instance-level MCP globally and keep it disabled unless an approved
   MCP use case exists. If it must remain enabled, define the approved workflow
   allowlist first.
2. Use a supported n8n UI/API to change per-workflow availability; do not force
   the rejected field through the unsupported workflow-update path.
3. Verify the effective MCP exposure equals the explicit allowlist only (empty
   when instance-level MCP is disabled).
4. Confirm normal non-MCP triggers required by retained workflows still operate
   independently, then record only non-secret counts and evidence in
   `registry/workflows.yaml` and `ARCHITECTURE.md`.

Do not freeze MM-18 while recent successful webhooks prove it is the current
website lead path. Retain it until the reviewed randomized-path/header-auth
WEB-002 producer cutover is proven atomically. Before any further freeze
decision, add the EC-02 principal allowlist before models; repair MM-20/MM-24
approval, dependency and idempotency controls; make MM-07 allowlist logging
redacted; and publish JM-09's suppression fix while preserving error bindings.

The containment status update itself performs no payload or credential review,
model call, publication, website cutover or other live mutation.

## Website producer cutover safety

At reviewed draft head `b0e3a656cf6659b893810e11a15b9f515527ab79`,
website PR #78 implements the randomized
`/webhook/web002-lead-<8 lowercase hex>` allowlist, `X-Webhook-Token`,
identity-only durable mode-bound outbox, legacy-default flat MM-18
compatibility, transport/5xx retry, 409 terminal identity review and other-4xx
terminal configuration/validation handling. It remains **draft, unmerged and
undeployed**. Merging auto-deploys `wp-content/**`; repository tests do not prove
actual WordPress/Fluent Forms producer T1–T4 or a seven-day cutover.

Safe sequence:

1. Keep PR #78 draft/unmerged until an approved cutover window. Preserve the
   existing flat MM-18 mode as the default.
2. Store the real randomized WEB-002 URL and token only as encrypted host config;
   verify the exact allowlisted path shape without copying the secret slug into
   Git, PR text or logs.
3. Stage the implemented self-hosted mode without dual-write. Confirm the outbox
   persists only `formId:entryId`, remains bound to one delivery mode, and
   rebuilds payloads from the canonical Fluent Forms row.
4. Verify the implemented response policy: 2xx acknowledges; transport/5xx
   retries durably; 409 is terminal identity review; every other 4xx is terminal
   configuration/validation failure.
5. Run T1–T4 from the **actual WordPress/Fluent Forms producer**, not a direct
   webhook client: T1 new lead/2xx ack, T2 exact replay/idempotency, T3 conflict/
   409 dead-letter, T4 injected 5xx or transport failure followed by a clean
   retry with no loss or duplicate.
6. Switch the host-only mode flag atomically. Never dual-write to MM-18 and
   WEB-002.
7. Reconcile Fluent Forms entry references against Postgres/Baserow outcomes for
   seven days.
8. Retire MM-18 last, only after the reconciliation window is clean and rollback
   is proven.

Keep all model-provider legal placeholders unpublished throughout this cutover.

## Access

- Base URL: `https://n8n.adapteng.com/api/v1`
- Auth header: `X-N8N-API-KEY: <key>` (owner-provided; store only in your
  session, never in the repo).
- Credentials referenced by id (not value): Postgres, adapter bearer, webhook
  header-auth. See `registry/services.yaml`.

## Create / update a workflow (PowerShell)

```powershell
$base = "https://n8n.adapteng.com/api/v1"
$h = @{ "X-N8N-API-KEY" = $key }
# POST /workflows with a JSON body of { name, nodes, connections, settings }.
# Round-trip an existing workflow for edits with GET then PUT (strip id/active/etc.,
# send only name/nodes/connections/settings).
```

## Gotchas (each cost real debugging time)

1. **UTF-8 BOM breaks POST /workflows** → the API 500s. Write request bodies as
   UTF-8 **without** BOM: `New-Object System.Text.UTF8Encoding($false)`.
2. **Activate/deactivate needs JSON content-type.** `POST /workflows/{id}/activate`
   with the default form encoding returns `400 unsupported media type
   application/x-www-form-urlencoded`. Pass `-ContentType 'application/json'`.
3. **`$pid` is read-only in PowerShell.** Don't name a workflow-id variable
   `$pid`; use `$wfid`.
4. **Postgres SSL on the internal network.** n8n *forces* SSL when
   `allowUnauthorizedCerts=true`, but the internal `adapteng-ops-db` has SSL
   disabled → connections hang/fail. Correct credential: `ssl='disable'` AND
   `allowUnauthorizedCerts=false`.
5. **Same-statement snapshot invisibility.** A single SQL statement that both
   INSERTs (via a function) and reads the same row back (LEFT JOIN) returns the
   id as NULL — the freshly inserted row isn't visible to the outer scan. Fix:
   **two separate Postgres nodes** (call the function, then SELECT the id); n8n
   autocommits per node so the second statement sees the committed row.
6. **Code nodes returning arrays** must run in **Run Once for All Items** mode
   (`runOnceForAllItems`) when they use `.first()` / aggregate — not per-item.

## The 200-on-error trap (critical)

With webhook `responseMode=responseNode`, if any node throws **before** a
Respond node runs, n8n returns an empty **HTTP 200**. A producer reads that as
success and never retries → silent data loss.

**Fix pattern (used in WEB-002):**

- Set each write/HTTP node `onError = "continueErrorOutput"` (a node-level
  property, sibling of `parameters`; this creates output index 1).
- Wire every error output to a single `Respond500` `respondToWebhook` node
  (`responseCode: 500`, body `{ "error": "upstream_write_failed", "retry": true }`).
- Result: the run completes `status=success`, `Respond500` fires, the producer
  sees 500 and retries. On retry, idempotent `business_id`s make already-written
  entities `created:false` and complete only the missing ones — zero duplicates.

## Verify after any governed self-hosted change

- `GET /workflows/{id}` → confirm `active=true`, node URLs point at the
  internal adapter, webhook `authentication=headerAuth`.
- Negative auth test: POST without / with a wrong `X-Webhook-Token` → **403**.
- Positive governed test with a synthetic id offset (e.g. lead ids use
  `100000 + rid`) so test rows never collide with real allocator ids; then read
  back from Baserow and delete the synthetic rows (owner).
