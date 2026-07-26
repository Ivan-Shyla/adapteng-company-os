# Access map (names only)

**No secret values here — ever.** This lists which credentials/accounts exist,
where their value lives, who holds them, and rotation status. To rotate any of
these, follow `runbooks/secret-rotation.md`.

| Credential (name) | Lives in | Held by | Used by | Rotation status |
|---|---|---|---|---|
| `BASEROW_API_TOKEN` (Company Operations) | Coolify env variable | Ivan | baserow-adapter | Rotated 2026-07-25. **Old leaked token not yet revoked at source — see action-items 🔴** |
| `ADAPTER_SERVICE_TOKEN` (internal bearer) | Coolify env variable | Ivan | governed n8n → adapter | Rotated post-canary 2026-07-25 |
| `X-Webhook-Token` (n8n webhook header) | n8n `httpHeaderAuth` credential | Ivan | AUT-001, WEB-002 webhooks | Current |
| `X-N8N-API-KEY` (n8n public API key) | n8n instance; owner-held | Ivan | workflow build/ops | Current; expires per JWT `exp` |
| Coolify API token | Coolify; owner-held | Ivan | deploy automation | Rotate post-launch (owner action ⚪) |
| GCP service account (EU Vertex + Drive) | GCP; owner-held | Ivan | ai-gateway, drive-adapter, integrity-reconciler | Required for AI/Drive live — not yet wired |
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
`BASEROW_API_TOKEN={{environment.BASEROW_API_TOKEN}}`. The repo references the
**name** `BASEROW_API_TOKEN`; the value is only ever in Coolify.
