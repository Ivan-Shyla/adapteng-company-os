"""ASGI-level OAuth discovery and unauthenticated MCP denial smoke tests.

No real token, network request to GitHub, or Coolify access is required.
"""
from __future__ import annotations

import asyncio
import os
import tempfile
import unittest
from unittest.mock import patch

import httpx
from cryptography.fernet import Fernet


class OAuthHTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="adapteng-mcp-test-")
        cfg = {
            "COOLIFY_READ_TOKEN": "synthetic-read-reference",
            "MCP_PUBLIC_BASE_URL": "https://ops-mcp.adapteng.com",
            "MCP_ALLOWED_GITHUB_LOGIN": "Ivan-Shyla",
            "GITHUB_CLIENT_ID": "synthetic-github-client",
            "GITHUB_CLIENT_SECRET": "synthetic-github-credential",
            "JWT_SIGNING_KEY": "x" * 48,
            "STORAGE_ENCRYPTION_KEY": Fernet.generate_key().decode(),
            "OAUTH_DATA_DIR": cls.tmp.name,
        }
        with patch.dict(os.environ, cfg):
            from server import create_server
            cls.server = create_server()
            cls.app = cls.server.http_app(path="/mcp")

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    async def request(self, method, path, **kwargs):
        async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=self.app),
                base_url="https://ops-mcp.adapteng.com",
                follow_redirects=False) as client:
            return await client.request(method, path, **kwargs)

    def test_oauth_server_discovery(self):
        r = asyncio.run(self.request("GET", "/.well-known/oauth-authorization-server"))
        self.assertEqual(r.status_code, 200, r.text[:400])
        body = r.json()
        self.assertIn("authorization_endpoint", body)
        self.assertIn("token_endpoint", body)
        self.assertTrue(body["authorization_endpoint"].startswith("https://ops-mcp.adapteng.com/"))

    def test_protected_resource_discovery(self):
        r = asyncio.run(self.request("GET", "/.well-known/oauth-protected-resource/mcp"))
        self.assertEqual(r.status_code, 200, r.text[:400])
        self.assertIn("resource", r.json())

    def test_unauthenticated_mcp_denied(self):
        request = {"jsonrpc":"2.0","id":1,"method":"tools/list"}
        r = asyncio.run(self.request(
            "POST", "/mcp", json=request,
            headers={"Accept":"application/json, text/event-stream",
                     "Content-Type":"application/json"}))
        self.assertIn(r.status_code, (401, 403))
        self.assertNotIn("coolify_health", r.text)

if __name__ == "__main__":
    unittest.main()
