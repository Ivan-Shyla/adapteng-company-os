# Runbook — incident response

First response when a Company OS live path misbehaves. Diagnose read-only
first; never "fix" by disabling governance.

## Triage order

1. **Is the adapter up?** `GET http://adapteng-baserow-adapter:8080/healthz`
   (from inside the network / an n8n node) → expect `{"status":"ok"}`.
2. **Is the workflow active?** `GET /workflows/{id}` on n8n → `active=true`,
   node URLs internal, webhook `authentication=headerAuth`.
3. **What failed?** Inspect the latest execution (`status`, `lastNode`). n8n
   executions show the erroring node and message.

## Symptom → cause → action

| Symptom | Likely cause | Action |
|---|---|---|
| Webhook returns **403** | Missing/wrong `X-Webhook-Token` | Expected for unauthenticated callers. Confirm the producer sends the header credential. |
| Webhook returns empty **HTTP 200** on failure | A node errored before a Respond node (the 200-on-error trap) | Ensure each write node has `onError=continueErrorOutput` wired to `Respond500`. See `n8n-operations.md`. |
| Webhook returns **500** `upstream_write_failed` | A governed upsert failed (adapter/Baserow/Postgres) | This is fail-closed working as designed. Fix the upstream; the producer's retry completes only the missing entities (idempotent `business_id`). |
| Partial write (some entities created, some not) | Mid-chain failure | Re-submit the same event. Stable `business_id` makes existing entities `created:false`; only the missing ones are created. Verify no duplicates in Baserow. |
| Postgres node hangs / SSL error | `allowUnauthorizedCerts=true` forcing SSL on an SSL-disabled internal DB | Use the credential with `ssl='disable'` + `allowUnauthorizedCerts=false`. |
| Adapter reachable publicly | Public URL not removed | Adapter must be internal-only; remove the public domain in Coolify and verify `503` externally. |

## Recovery principles

- **Idempotent retry over manual repair.** The governed path is retry-safe;
  re-driving the source event is safer than hand-editing Baserow/Postgres.
- **No manual business-field writes** to Baserow to "patch" state — that breaks
  field ownership and reconciliation. Drive changes through the governed path.
- **Capture evidence** (execution id, timestamps, what you changed) and reflect
  any status change in `ARCHITECTURE.md` §11.

## Escalation (owner-only)

Restoring GitHub Actions billing, revoking/rotating provider tokens, restoring a
database backup, and any DNS/TLS change are owner actions — see
`owner/action-items.md`.
