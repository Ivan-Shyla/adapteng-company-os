import unittest
from unittest.mock import patch
from gateway_core import CoolifyClient, is_allowed_identity, project_list, read_settings, safe_resource

def env():
    return {
        "COOLIFY_READ_TOKEN": "synthetic-test-only",
        "MCP_PUBLIC_BASE_URL": "https://ops-mcp.adapteng.com",
        "GITHUB_CLIENT_ID": "synthetic-client-id",
        "GITHUB_CLIENT_SECRET": "synthetic-secret",
        "JWT_SIGNING_KEY": "x" * 48,
        "STORAGE_ENCRYPTION_KEY": "synthetic-not-real",
        "MCP_ALLOWED_GITHUB_LOGIN": "Ivan-Shyla",
    }

class SecurityTests(unittest.TestCase):
    def test_fail_closed_no_config(self):
        with self.assertRaises(ValueError):
            read_settings({})

    def test_expected_upstream(self):
        self.assertEqual(read_settings(env()).base_url, "https://coolify.adapteng.com")

    def test_rejects_external_host(self):
        values = env()
        values["COOLIFY_API_BASE_URL"] = "https://attacker.example"
        with self.assertRaises(ValueError):
            read_settings(values)

    def test_rejects_http_public(self):
        values = env()
        values["MCP_PUBLIC_BASE_URL"] = "http://ops-mcp.adapteng.com"
        with self.assertRaises(ValueError):
            read_settings(values)

    def test_rejects_short_signing_key(self):
        values = env()
        values["JWT_SIGNING_KEY"] = "weak"
        with self.assertRaises(ValueError):
            read_settings(values)

    def test_identity_allowlist(self):
        self.assertTrue(is_allowed_identity({"login": "ivan-shyla"}, "Ivan-Shyla"))
        self.assertFalse(is_allowed_identity({}, "Ivan-Shyla"))
        self.assertFalse(is_allowed_identity(None, "Ivan-Shyla"))
        self.assertFalse(is_allowed_identity({"login": "other-user"}, "Ivan-Shyla"))

    def test_projection_drops_sensitive_values(self):
        raw = {"uuid": "abc123", "name": "test", "status": "running",
               "password": "SECRET", "docker_compose_raw": "SECRET",
               "environment_variables": [{"value": "SECRET"}],
               "private_key_id": 3, "ip": "10.0.1.5"}
        output = safe_resource("applications", raw)
        self.assertEqual(output, {"uuid": "abc123", "name": "test", "status": "running"})
        self.assertNotIn("SECRET", repr(output))

    def test_nested_server_data_dropped(self):
        self.assertEqual(safe_resource("servers", {"name": "localhost",
              "settings": {"sentinel_token": "SECRET"}}), {"name": "localhost"})

    def test_refuses_unapproved_collection(self):
        with self.assertRaises(ValueError):
            project_list("tokens", [])

    def test_refuses_unexpected_large_payload(self):
        with self.assertRaises(RuntimeError):
            project_list("servers", [{}] * 121)

class ClientTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.client = CoolifyClient(read_settings(env()))

    async def test_selector_blocks_path_traversal(self):
        with patch.object(self.client, "_get") as request:
            with self.assertRaises(ValueError):
                await self.client.detail("applications", "../tokens")
            request.assert_not_called()

    async def test_selector_blocks_unapproved(self):
        with patch.object(self.client, "_get") as request:
            with self.assertRaises(ValueError):
                await self.client.list_resources("secrets")
            request.assert_not_called()

    async def test_detail_does_not_proxy_raw_response(self):
        with patch.object(self.client, "_get", return_value={
                "uuid": "abc123", "name": "n8n", "password": "SECRET"}):
            output = await self.client.detail("applications", "abc123")
            self.assertEqual(output["resource"], {"uuid": "abc123", "name": "n8n"})

if __name__ == "__main__":
    unittest.main()
