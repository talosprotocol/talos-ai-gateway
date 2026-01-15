"""Integration tests for MCP Discovery API."""
from uuid import uuid4
from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)

# Test key from auth_public.py mock
TEST_KEY = "sk-test-key-1"
HEADERS = {"Authorization": f"Bearer {TEST_KEY}"}
ADMIN_HEADERS = {"X-Talos-Principal": "admin@talos.io"}


class TestMcpDiscovery:
    """Tests for /mcp/v1 endpoints."""

    def test_list_servers_requires_auth(self):
        """Should return 401 without auth."""
        response = client.get("/v1/mcp/servers")
        assert response.status_code == 401

    def test_list_servers_with_auth(self):
        """Should return servers with valid key."""
        response = client.get("/v1/mcp/servers", headers=HEADERS)
        assert response.status_code == 200
        data = response.json()
        assert "servers" in data
        assert len(data["servers"]) >= 1

    def test_list_tools(self):
        """Should return tools for a server."""
        response = client.get("/v1/mcp/servers/filesystem/tools", headers=HEADERS)
        assert response.status_code == 200
        data = response.json()
        assert "tools" in data
        assert len(data["tools"]) >= 1

    def test_get_tool_schema(self):
        """Should return schema with hash."""
        response = client.get("/v1/mcp/servers/filesystem/tools/read_file/schema", headers=HEADERS)
        assert response.status_code == 200
        data = response.json()
        assert "json_schema" in data
        assert "schema_hash" in data
        assert len(data["schema_hash"]) == 16  # Truncated SHA256

    def test_call_tool(self):
        """Should invoke tool and return output."""
        response = client.post(
            "/v1/mcp/servers/filesystem/tools/read_file:call",
            headers=HEADERS,
            json={"input": {"path": "/tmp/test.txt"}}
        )
        assert response.status_code == 200
        data = response.json()
        assert "output" in data
        assert "timing_ms" in data


class TestAdminMcpRegistry:
    """Tests for /admin/v1/mcp endpoints."""

    def test_list_mcp_servers_requires_rbac(self):
        """Should require RBAC auth."""
        response = client.get("/admin/v1/mcp/servers")
        assert response.status_code == 401

    def test_list_mcp_servers_with_admin(self):
        """Should return servers for admin."""
        response = client.get("/admin/v1/mcp/servers", headers=ADMIN_HEADERS)
        assert response.status_code == 200
        data = response.json()
        assert "servers" in data

    def test_register_mcp_server(self):
        """Should register new server."""
        unique_id = f"test-server-{uuid4().hex[:8]}"
        response = client.post(
            "/admin/v1/mcp/servers",
            headers=ADMIN_HEADERS,
            json={
                "id": unique_id,
                "name": "Test Server",
                "transport": "http",
                "endpoint": "http://localhost:9000"
            }
        )
        assert response.status_code == 201
        data = response.json()
        assert data["id"] == unique_id

    def test_viewer_cannot_register_server(self):
        """Viewer should be denied write access."""
        response = client.post(
            "/admin/v1/mcp/servers",
            headers={"X-Talos-Principal": "viewer@talos.io"},
            json={"name": "Test", "transport": "stdio", "command": "echo"}
        )
        assert response.status_code == 403
