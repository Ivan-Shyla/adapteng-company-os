"""Owner-authenticated, read-only MCP bridge for AdaptEng Coolify."""
from __future__ import annotations

import os
from typing import Literal
from gateway_core import CoolifyClient, is_allowed_identity, read_settings

def create_server():
    from cryptography.fernet import Fernet
    from fastmcp import FastMCP
    from fastmcp.server.auth.providers.github import GitHubProvider
    from fastmcp.server.dependencies import get_access_token
    from key_value.aio.stores.disk import DiskStore
    from key_value.aio.wrappers.encryption import FernetEncryptionWrapper

    cfg = read_settings()  # fail closed on incomplete setup
    encrypted_store = FernetEncryptionWrapper(
        key_value=DiskStore(directory=os.environ.get("OAUTH_DATA_DIR", "/data/oauth")),
        fernet=Fernet(cfg.storage_key.encode("ascii")))
    auth = GitHubProvider(
        client_id=cfg.github_client_id,
        client_secret=cfg.github_client_secret,
        base_url=cfg.public_url,
        jwt_signing_key=cfg.jwt_key,
        client_storage=encrypted_store,
        allowed_client_redirect_uris=["https://chatgpt.com/*", "https://*.chatgpt.com/*"])
    mcp = FastMCP("AdaptEng Coolify Read Only", auth=auth)
    backend = CoolifyClient(cfg)

    def owner_only():
        oauth_session = get_access_token()
        claims = getattr(oauth_session, "claims", None) if oauth_session else None
        if not is_allowed_identity(claims, cfg.github_login):
            raise PermissionError("GitHub account not authorized")

    @mcp.tool()
    async def coolify_health() -> dict:
        """Check Coolify API health; do not change any runtime state."""
        owner_only()
        return await backend.health()

    @mcp.tool()
    async def coolify_inventory() -> dict:
        """Read sanitized servers, applications, services, databases and projects."""
        owner_only()
        return await backend.overview()

    @mcp.tool()
    async def coolify_list_resources(kind: Literal["projects", "servers", "applications", "services", "databases"]) -> dict:
        """List one approved Coolify resource kind; never return secrets or raw config."""
        owner_only()
        return await backend.list_resources(kind)

    @mcp.tool()
    async def coolify_resource_detail(kind: Literal["servers", "applications", "services", "databases"], uuid: str) -> dict:
        """Get safe status and identity fields for one Coolify resource."""
        owner_only()
        return await backend.detail(kind, uuid)

    return mcp

if __name__ == "__main__":
    create_server().run(transport="http", host="0.0.0.0", port=8000, path="/mcp")
