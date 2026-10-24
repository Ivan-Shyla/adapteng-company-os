# AdaptEng Coolify MCP Gateway — read-only OAuth bridge

Status: IMPLEMENTED IN PR / NOT DEPLOYED / NOT YET ACCEPTED.

## Architectural decision

ChatGPT custom MCP connections support OAuth, whereas Coolify's built-in MCP needs a static Bearer token. Instead of publicly exposing every Coolify MCP action, this isolated FastMCP service presents four fixed read-only tools and uses only the approved Coolify REST GET endpoints. GitHub OAuth establishes owner identity; every tool also enforces an exact GitHub-login allowlist.

## Contract

- Public MCP URL after a separately approved release: https://ops-mcp.adapteng.com/mcp
- OAuth provider: GitHub OAuth App with auth-code flow, PKCE and a persistent encrypted OAuth client/token store.
- Only accepted GitHub login is set by MCP_ALLOWED_GITHUB_LOGIN.
- Upstream: https://coolify.adapteng.com/api/v1 with a new Root Team token with read permission ONLY.
- Endpoints: /health, /projects, /servers, /applications, /services, /databases; resource lookup by constrained UUID.
- Returned fields are positively allowlisted. No raw compose, credentials, sensitive fields, IPs, env values, logs or arbitrary URLs.
- No deploy, restart, stop, delete, rotate, write, credential change or production mutation.
- GitHub remains source of truth; there is no automatic deployment or DNS change in this PR.

## Owner actions (do not paste credentials into chat)

1. In Coolify > Security > API Tokens, revoke the root token that was pasted into the conversation. Create a fresh Root Team token with permission READ only. Save it directly under COOLIFY_READ_TOKEN in the NEW application's Coolify runtime secret store.
2. In https://github.com/settings/developers create a separate GitHub OAuth App. Homepage URL: https://ops-mcp.adapteng.com. Authorization callback URL: https://ops-mcp.adapteng.com/auth/callback. Store GITHUB_CLIENT_ID and GITHUB_CLIENT_SECRET directly in Coolify runtime secrets.
3. Generate JWT_SIGNING_KEY (at least 48 random characters) and STORAGE_ENCRYPTION_KEY (Fernet.generate_key().decode()). Store directly in Coolify; do not paste or commit values.
4. Following PR review and separate explicit approval, create a new Coolify application sourced from this repo, build context services/coolify-mcp-gateway, internal port 8000, one replica, auto-deploy disabled. Add a dedicated persistent volume at /data/oauth. Use an isolated container; do not restart the Coolify control plane or n8n.
5. Under a separate DNS change approval, create ops-mcp.adapteng.com -> existing managed Coolify ingress, provision TLS and configure MCP_PUBLIC_BASE_URL=https://ops-mcp.adapteng.com.
6. In the ChatGPT custom MCP form use https://ops-mcp.adapteng.com/mcp with OAuth authentication. Do not use Coolify native /mcp as the URL.

## Validation before release

- Code unit tests and dependency-import check pass in GitHub Actions.
- Before deploying: pin the Docker base image by immutable SHA digest and pin transitive dependencies by reviewed hashes / SBOM. Current Dockerfile deliberately marks this gate open.
- Before claiming readiness: test public HTTPS discovery (protected-resource and authorization-server metadata), OAuth code + PKCE S256, and refresh behaviour with the exact ChatGPT callback.
- Try sign-in from a disallowed GitHub account; EVERY tool must fail, not merely hide the UI.
- Confirm Coolify permissions are read only and reject write paths. Compare sanitized inventory with existing GitHub Actions diagnostics.
- Restart only the gateway; confirm OAuth session persists via encrypted volume.
- Arrange offsite encrypted copy plus restore drill of OAuth persistent state. Never confuse backup with a successful restore.
- Inspect CI and gateway container logs for credential leakage; avoid logging raw headers/payloads.

## Rollback

Uninstall/disable ChatGPT plugin, stop the NEW gateway app only, revoke its OAuth app secret and read-only Coolify token, and revert ONLY the new DNS record. Existing Coolify, n8n, Postgres, WordPress, GitHub deployment jobs and scheduled backups remain untouched.

## Local verification

From services/coolify-mcp-gateway run: python -m unittest -v test_gateway_core

External references: https://coolify.io/docs/mcp/setup and https://developers.openai.com/plugins/build/auth
