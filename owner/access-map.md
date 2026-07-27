# Access map (names only)

**No secret values here — ever.** This lists which credentials/accounts exist,
where their value lives, who holds them, and rotation status. To rotate any of
these, follow `runbooks/secret-rotation.md`.

| Credential (name) | Lives in | Held by | Used by | Rotation status |
|---|---|---|---|---|
| `baserow-company-os-primary` (fingerprint redacted) | Provider + runtime secret store | Ivan | baserow-adapter | **COMPROMISED** - literal value remains in Git history; owner revoke/rotate and old-value failure verification pending |
| Baserow temporary cleanup token (name/fingerprint redacted) | Provider | Ivan | one-off synthetic-row cleanup (done 2026-07-26) | Revocation evidence retained; no literal reference in Git |
| `ADAPTER_SERVICE_TOKEN` (internal bearer) | Coolify env variable | Ivan | governed n8n → adapter | Rotated post-canary 2026-07-25 |
| `X-Webhook-Token` (n8n webhook header) | n8n `httpHeaderAuth` credential | Ivan | AUT-001, WEB-002 webhooks | Current |
| `X-N8N-API-KEY` (n8n public API key) | n8n instance; owner-held | Ivan | workflow build/ops | Current; expires per JWT `exp` |
| Coolify API token | Coolify; owner-held | Ivan | deploy automation | Rotate post-launch (owner action ⚪) |
| `GOOGLE_SERVICE_ACCOUNT_JSON_B64` (SA `adapteng-ai-operator@adapteng-workspace-automation`) | Coolify runtime secret | Ivan | governed Drive bridge, drive-adapter, integrity-reconciler Drive reader; intended ai-gateway identity | Provided 2026-07-26; Drive DWD scope verified as configured. Governed Drive bridge is the first live consumer in build; Vertex IAM/ADC permission is not yet evidenced |
| `GDRIVE_SA_JSON` (SA `media-worker@adapteng.iam.gserviceaccount.com`) | Coolify application env | Ivan | current live media-worker | **Legacy binding** — old project/account; replace after snapshot + canary, then revoke |
| `ADAPTENG_Google_Drive` (`googleDriveOAuth2Api`) | n8n Cloud credential store | Ivan | current MM source/media workflows | **Legacy OAuth** — reads personal My Drive; does not have corporate Shared Drive write access (404 proven 2026-07-26) |
| `adapteng_ops` Postgres DSN | internal Coolify network | Ivan | adapter, governed workflows | Internal-only (SSL disabled on internal net) |
| GitHub repo access | GitHub | Ivan | all repos | Actions budget $10/mo hard stop |

## Where values must never appear

- This repository (any file).
- n8n workflow JSON exports committed to git.
- Coolify build **args/logs** (a prior leak happened this way — prefer runtime
  env references `{{environment.X}}`, not build args, for secrets).
- ADRs, runbooks, registry YAML, or status PRs.

## Reference-by-name pattern

Consumers read secrets at runtime, e.g. Coolify:
`BASEROW_API_TOKEN={{environment.BASEROW_API_TOKEN}}`. Current tracked files
reference only the **name** `BASEROW_API_TOKEN`; a historical literal remains
compromised until the owner completes rotation and old-value failure proof.
