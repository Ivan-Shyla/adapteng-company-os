# Runbook — rotate a secret / token

Secrets never live in this repository. They live in the provider (Coolify env,
n8n credential, Baserow, GCP, GitHub) and are referenced here **by name only**
(see `owner/access-map.md`).

## The revocation lesson (do not skip)

**Storing a new token in Coolify does NOT revoke the old one.** Rotation is two
independent steps:

1. **Issue + install** the new value in the consumer (e.g. Coolify env
   `BASEROW_API_TOKEN={{environment.BASEROW_API_TOKEN}}`, then redeploy).
2. **Revoke the old value at the source** (Baserow → Settings → API tokens →
   delete the old token; provider console for others).

A literal Baserow token value was committed to Git history. Current tracked
files are sanitized, but history is immutable evidence of compromise. Treat the
credential as **COMPROMISED** regardless of prior runtime changes. The owner
must revoke it at the provider, issue/install a least-privilege replacement and
verify the old value fails before recording rotation complete. Do not rewrite
history; track the open action in `owner/action-items.md`.

## General rotation procedure

1. Confirm which consumers use the secret (grep `registry/` for the name;
   check Coolify env references).
2. Create the new value in the provider.
3. Install it in each consumer (Coolify shared/env variable → redeploy the
   dependent service). For a container behind a `{{environment.X}}` reference,
   redeploy so it re-reads the value.
4. Verify the consumer works with the new value (a governed canal call that
   returns success).
5. **Revoke/delete the old value at the source.**
6. Verify the old value now **fails** (a call with the old value should be
   rejected). Only then is rotation complete.
7. Update rotation status/date in `owner/access-map.md` (name + date only).

## Repository regression check

Run from the repository root:

```powershell
python scripts/validate_sensitive_references.py
```

The check is offline and inspects tracked files only. It rejects exact Google
Drive/Docs resource URLs, raw Google resource-ID fields, credential-bearing
URLs, literal secret assignments and leaked-token literals. Safe provider
documentation links, redacted secret names/fingerprints and
`company-drive://...` aliases remain allowed.

## Tokens in scope (names only)

- Baserow `Company Operations` API token — in Coolify env `BASEROW_API_TOKEN`.
- Adapter internal bearer `ADAPTER_SERVICE_TOKEN` — Coolify env (rotated post-canary).
- n8n webhook header token (`X-Webhook-Token`) — n8n `httpHeaderAuth` credential.
- n8n public API key (`X-N8N-API-KEY`) — owner-held; used for workflow ops.
- Coolify API token — owner-held; rotate post-launch.
- GCP service account (AI Gateway / Drive) — owner-held; required for live AI/Drive.
