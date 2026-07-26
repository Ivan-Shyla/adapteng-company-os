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

A real incident of this: after the `Company Operations` Baserow token was
rotated in Coolify, the **previously-leaked token still authenticated** against
Baserow because it was never deleted at the source. Until step 2 is done, the
old credential remains valid. There is an open owner action to revoke it — see
`owner/action-items.md`.

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

## Tokens in scope (names only)

- Baserow `Company Operations` API token — in Coolify env `BASEROW_API_TOKEN`.
- Adapter internal bearer `ADAPTER_SERVICE_TOKEN` — Coolify env (rotated post-canary).
- n8n webhook header token (`X-Webhook-Token`) — n8n `httpHeaderAuth` credential.
- n8n public API key (`X-N8N-API-KEY`) — owner-held; used for workflow ops.
- Coolify API token — owner-held; rotate post-launch.
- GCP service account (AI Gateway / Drive) — owner-held; required for live AI/Drive.
