"""Strict GET-only Coolify API client. Nothing raw or sensitive is returned to ChatGPT."""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Any, Mapping
from urllib.parse import urlsplit
import httpx

COLLECTIONS = frozenset(("projects", "servers", "applications", "services", "databases"))
UUID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{2,63}$")
COMMON = ("uuid", "name", "status", "created_at", "updated_at")
FIELDS = {
    "projects": ("uuid", "name", "description"),
    "servers": COMMON + ("is_reachable", "is_usable", "proxy_type"),
    "applications": COMMON + ("fqdn", "git_repository", "git_branch", "git_commit_sha",
                             "health_check_enabled", "environment_id", "server_id"),
    "services": COMMON + ("service_type", "environment_id", "server_id"),
    "databases": COMMON + ("type", "environment_id", "server_id"),
}
BOOL_FIELDS = frozenset(("is_reachable", "is_usable", "health_check_enabled"))

@dataclass(frozen=True)
class Settings:
    base_url: str
    read_token: str
    public_url: str
    github_client_id: str
    github_client_secret: str
    jwt_key: str
    storage_key: str
    github_login: str

def https_origin(value: str, host: str | None = None) -> str:
    part = urlsplit(value.strip())
    if (part.scheme != "https" or not part.hostname or part.username or part.password
            or part.query or part.fragment or part.path not in ("", "/")
            or part.port not in (None, 443)):
        raise ValueError("HTTPS origin required (without path or credentials)")
    if host and part.hostname.lower() != host:
        raise ValueError("Unreviewed upstream Coolify hostname")
    return "https://" + part.hostname.lower()

def read_settings(env: Mapping[str, str] | None = None) -> Settings:
    env = os.environ if env is None else env
    names = ("COOLIFY_READ_TOKEN", "MCP_PUBLIC_BASE_URL", "GITHUB_CLIENT_ID",
             "GITHUB_CLIENT_SECRET", "JWT_SIGNING_KEY", "STORAGE_ENCRYPTION_KEY",
             "MCP_ALLOWED_GITHUB_LOGIN")
    missing = [key for key in names if not env.get(key, "").strip()]
    if missing:
        raise ValueError("Missing runtime configuration: " + ", ".join(missing))
    if len(env["JWT_SIGNING_KEY"]) < 32:
        raise ValueError("JWT signing key must be at least 32 characters")
    login = env["MCP_ALLOWED_GITHUB_LOGIN"].strip()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]{0,37}", login):
        raise ValueError("Expected one explicit GitHub login")
    coolify_credential = env["COOLIFY_READ_TOKEN"].strip()
    if "\r" in coolify_credential or "\n" in coolify_credential:
        raise ValueError("Invalid token format")
    return Settings(
        base_url=https_origin(env.get("COOLIFY_API_BASE_URL", "https://coolify.adapteng.com"),
                              host="coolify.adapteng.com"),
        read_token=coolify_credential, public_url=https_origin(env["MCP_PUBLIC_BASE_URL"]),
        github_client_id=env["GITHUB_CLIENT_ID"].strip(),
        github_client_secret=env["GITHUB_CLIENT_SECRET"].strip(),
        jwt_key=env["JWT_SIGNING_KEY"], storage_key=env["STORAGE_ENCRYPTION_KEY"].strip(),
        github_login=login)

def is_allowed_identity(claims: Mapping[str, Any] | None, login: str) -> bool:
    observed = claims.get("login") if isinstance(claims, Mapping) else None
    return isinstance(observed, str) and observed.casefold() == login.casefold()

def safe_resource(kind: str, raw: Mapping[str, Any]) -> dict[str, Any]:
    if kind not in FIELDS:
        raise ValueError("Resource kind forbidden")
    def scalar(value: Any, key: str) -> Any:
        if key in BOOL_FIELDS:
            return value if isinstance(value, bool) else None
        if isinstance(value, str):
            return value[:220]
        if isinstance(value, (float, int)) and not isinstance(value, bool):
            return value
        return None
    return {k: v for k in FIELDS[kind] if (v := scalar(raw.get(k), k)) is not None}

def project_list(kind: str, payload: Any) -> dict[str, Any]:
    if kind not in COLLECTIONS:
        raise ValueError("Resource kind forbidden")
    if not isinstance(payload, list) or len(payload) > 120 or not all(isinstance(x, dict) for x in payload):
        raise RuntimeError("Unexpected Coolify inventory response shape")
    return {"kind": kind, "count": len(payload),
            "resources": [safe_resource(kind, x) for x in payload]}

class CoolifyClient:
    def __init__(self, settings: Settings):
        self.settings = settings

    async def _get(self, path: str) -> Any:
        if not path.startswith("/api/v1/"):
            raise ValueError("Unapproved Coolify endpoint")
        async with httpx.AsyncClient(timeout=12, follow_redirects=False, trust_env=False) as client:
            try:
                response = await client.get(
                    self.settings.base_url + path,
                    headers={"Authorization": "Bearer " + self.settings.read_token,
                             "Accept": "application/json"})
            except httpx.RequestError:
                raise RuntimeError("Coolify API unreachable") from None
        if response.status_code != 200:
            raise RuntimeError(f"Coolify API HTTP {response.status_code}")
        if path == "/api/v1/health":
            return response.text.strip().lower() in ("ok", '"ok"')
        try:
            return response.json()
        except ValueError:
            raise RuntimeError("Unexpected Coolify JSON") from None

    async def health(self) -> dict:
        return {"coolify_api_healthy": await self._get("/api/v1/health")}

    async def list_resources(self, kind: str) -> dict:
        if kind not in COLLECTIONS:
            raise ValueError("Resource kind forbidden")
        return project_list(kind, await self._get("/api/v1/" + kind))

    async def detail(self, kind: str, uuid: str) -> dict:
        if kind not in COLLECTIONS - {"projects"} or not UUID.fullmatch(uuid):
            raise ValueError("Resource selector forbidden")
        result = await self._get("/api/v1/" + kind + "/" + uuid)
        if not isinstance(result, dict):
            raise RuntimeError("Unexpected Coolify resource response")
        return {"kind": kind, "resource": safe_resource(kind, result)}

    async def overview(self) -> dict:
        result = {"health": await self.health()}
        for kind in sorted(COLLECTIONS):
            result[kind] = await self.list_resources(kind)
        return result
