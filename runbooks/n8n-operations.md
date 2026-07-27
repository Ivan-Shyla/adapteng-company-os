# Runbook — n8n governed workflow operations

How to build, activate and debug a **governed** self-hosted n8n workflow via
the public API. Proven building AUT-001 (`NsWG1hD8VmIRRwCv`) and WEB-002
(`05ytz5If9kHUOYuA`).

## Principles

- Governed workflows never write business systems directly. They call the
  **baserow-adapter** (`http://adapteng-baserow-adapter:8080/v1/upsert`) over
  the internal `coolify` network — no public exposure.
- Every externally-reachable webhook is **header-authenticated**
  (`X-Webhook-Token` via an n8n `httpHeaderAuth` credential). No token → 403.
- The workflow must **fail closed**: a downstream error must return a non-2xx
  so the producer retries, never an empty 200 (see "The 200-on-error trap").

## Website producer cutover safety

Website PR #78's `lead.created` body is WEB-002-compatible in principle, but its
current transport is **unsafe and held**: it has only the old n8n Cloud
allowlist, no host-only `X-Webhook-Token`, no durable retry, no HTTP 409
dead-letter, and no producer E2E. Merging also auto-deploys `wp-content/**`.

Safe sequence:

1. Preserve the existing flat MM-18 path behind a host-only legacy-default flag.
2. Add a **dark** WEB-002 mode with an allowlisted self-hosted URL and host-only
   token/header authentication. Do not send live leads yet.
3. Persist only Fluent Forms entry references in the producer outbox — no copied
   lead payload or parallel business authority.
4. Handle responses exactly: 2xx acknowledges/removes the queued reference;
   5xx or transport failure stays queued and retries durably; 409 moves to
   dead-letter/manual review; every other 4xx raises a configuration alert and
   does not blind-retry.
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

## Verify after any change

- `GET /workflows/{id}` → confirm `active=true`, node URLs point at the
  internal adapter, webhook `authentication=headerAuth`.
- Negative auth test: POST without / with a wrong `X-Webhook-Token` → **403**.
- Positive governed test with a synthetic id offset (e.g. lead ids use
  `100000 + rid`) so test rows never collide with real allocator ids; then read
  back from Baserow and delete the synthetic rows (owner).
