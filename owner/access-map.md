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
| `X-Worker-Token` (n8n outbound header) | n8n `httpHeaderAuth` credential, `Adapteng` project | Ivan | `L1 - Baserow Systems Read Proof` → adapter | Present, verified by name 2026-09-05; value never read |
| `X-N8N-API-KEY` (n8n public API key) | n8n instance; owner-held | Ivan | workflow build/ops | Current; expires per JWT `exp` |
| Coolify API token | Coolify; owner-held | Ivan | deploy automation | Rotate post-launch (owner action ⚪) |
| `GOOGLE_SERVICE_ACCOUNT_JSON_B64` (SA `adapteng-ai-operator@adapteng-workspace-automation`) | Coolify runtime secret | Ivan | governed Drive bridge, drive-adapter, integrity-reconciler Drive reader; intended ai-gateway identity | Provided 2026-07-26; Drive DWD scope verified as configured. Governed Drive bridge is the first live consumer in build; Vertex IAM/ADC permission is not yet evidenced |
| `GDRIVE_SA_JSON` (SA `media-worker@adapteng.iam.gserviceaccount.com`) | Coolify application env | Ivan | current live media-worker | **Legacy binding** — old project/account; replace after snapshot + canary, then revoke |
| `ADAPTENG_Google_Drive` (`googleDriveOAuth2Api`) | n8n Cloud credential store | Ivan | current MM source/media workflows | **Legacy OAuth** — reads personal My Drive; does not have corporate Shared Drive write access (404 proven 2026-07-26) |
| `adapteng_ops` Postgres DSN | internal Coolify network | Ivan | adapter, governed workflows | Internal-only (SSL disabled on internal net) |
| GitHub repo access | GitHub | Ivan | all repos | Actions budget $10/mo hard stop |

## Presence verification — 2026-09-06

An authenticated read-only pass listed GitHub Actions storage **by name and
scope only** across the five authoritative repositories. No value was requested,
returned, decrypted, compared or logged, and none is reproduced here. The pass
established where each reference actually lives, which had previously been
guessed:

| Scope | Location | Holds (names only) |
|---|---|---|
| Repository | `adapteng-company-os` | 12 secrets and 19 variables covering Coolify, object storage, pgBackRest, production SSH and Workspace admin |
| Repository | `adapteng-automation-platform` | 3 secrets, 4 variables |
| Environment | `adapteng-automation-platform` / `approved-assets-preflight`, `approved-assets-import` | the approved-assets, canonical-folder, Workspace service-account and cross-repository read references, plus 3 Workspace variables |
| Environment | `adapteng-automation-platform` / `approved-assets-migrations` | the approved-assets database reference |
| Environment | `adapteng-automation-platform` / `company-os-vertex-runtime-readiness` | the three Vertex identity references |
| Repository | `adapteng-website` | 10 secrets, and 3 preflight attestation variables set 2026-09-06 |
| Repository | `adapteng-marketing`, `ai-dev-loop-control-plane` | none |

Most references previously reported as absent are present at **environment**
scope rather than repository scope. Environment scope is the stricter placement,
so this is a correction to the inventory, not a change to the platform.

Two references named in current default-branch configuration have no GitHub
binding at any scope and are supplied by the runtime instead: the Drive-bridge
replay database reference and the Workspace delegated-user reference, for which
GitHub carries the Workspace admin variable under a different name.

## Presence verification — 2026-09-05

An authenticated read-only pass over the connected n8n credential store listed
**15 credentials by name and type only**. No credential value was requested,
returned, decrypted or logged, and none is reproduced here. The pass confirmed
that `X-Worker-Token` exists and is reusable by reference, which was the open
question for the L1 read proof.

Two access facts were established in the same session and belong to the gap
table rather than to this map:

- The **Coolify API is currently failing** (`HTTP 502` on two authorized
  read-only probes, 2026-09-05, and again on two more, 2026-09-06 20:54 and
  20:55). The token reference itself is unchanged and worked on 2026-08-13, so
  this is a service condition, not a credential one. Deployed applications are
  unaffected — the self-hosted n8n API answered 200 throughout — but deployment
  revision and network-alias questions have no read path while it persists.
- No Coolify, Docker, Postgres, Baserow or B2 client, token or network route is
  present in the automation session, so provider runtime cannot be observed from
  here except through existing repository operations.

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
