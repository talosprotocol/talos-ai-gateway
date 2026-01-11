
import pytest
from unittest.mock import MagicMock, AsyncMock, patch
from fastapi.testclient import TestClient
from app.main import app
from app.middleware.auth_public import get_auth_context, AuthContext
from app.dependencies import get_audit_store, get_rate_limit_store, get_usage_store, get_routing_service, get_mcp_client
from app.adapters.mcp.client import McpClient

client = TestClient(app)

AUTH_HEADERS = {"Authorization": "Bearer test-token"}

@pytest.fixture
def mock_auth_context():
    auth = MagicMock(spec=AuthContext)
    auth.key_id = "test-key"
    auth.team_id = "test-team"
    auth.org_id = "test-org"
    auth.scopes = ["a2a.invoke", "mcp.invoke"] # Both required
    auth.allowed_mcp_servers = ["*"]
    auth.can_access_mcp_server.return_value = True
    return auth

@pytest.mark.asyncio
async def test_mcp_allowed_tool_call(mock_auth_context):
    app.dependency_overrides[get_auth_context] = lambda: mock_auth_context
    
    with patch("app.dependencies.get_audit_store") as m_audit, \
         patch("app.dependencies.get_rate_limit_store") as m_rl, \
         patch("app.dependencies.get_mcp_client") as m_mcp, \
         patch("app.domain.mcp.registry.get_server") as m_get_server, \
         patch("app.domain.mcp.registry.is_tool_allowed") as m_is_allowed:

         # Mocks
         m_rl.return_value.check_limit = AsyncMock(return_value=MagicMock(allowed=True))
         
         # Registry
         m_is_allowed.return_value = True
         m_get_server.return_value = {"id": "srv-1", "transport": "stdio"}
         
         # Audit
         m_audit.return_value.log_event = AsyncMock()

         # Client
         m_mcp.return_value.call_tool = AsyncMock(return_value={
             "is_error": False,
             "content": [{"type": "text", "text": "Tool Result"}]
         })

         app.dependency_overrides[get_audit_store] = lambda: m_audit.return_value
         app.dependency_overrides[get_rate_limit_store] = lambda: m_rl.return_value
         app.dependency_overrides[get_mcp_client] = lambda: m_mcp.return_value

         payload = {
            "jsonrpc": "2.0",
            "method": "tasks.send",
            "id": 100,
            "params": {
                "profile": {
                    "profile_id": "a2a-compat",
                    "profile_version": "0.1",
                    "spec_source": "a2a-protocol"
                },
                "input": [{"role": "user", "content": [{"text": "use tool"}]}],
                "tool_call": {
                    "server_id": "srv-1",
                    "tool_name": "echo",
                    "arguments": {"msg": "hello"}
                }
            }
         }

         response = client.post("/a2a/v1/", json=payload, headers=AUTH_HEADERS)
         assert response.status_code == 200
         data = response.json()
         
         assert "result" in data, f"Error: {data}"
         task = data["result"]
         assert task["status"] == "completed"
         assert task["output"][0]["content"][0]["text"] == "Tool Result"
         
         # Verify call
         m_mcp.return_value.call_tool.assert_awaited_once()

@pytest.mark.asyncio
async def test_mcp_denied_tool(mock_auth_context):
    app.dependency_overrides[get_auth_context] = lambda: mock_auth_context
    
    with patch("app.dependencies.get_rate_limit_store") as m_rl, \
         patch("app.domain.mcp.registry.is_tool_allowed") as m_is_allowed:
         
         m_rl.return_value.check_limit = AsyncMock(return_value=MagicMock(allowed=True))
         app.dependency_overrides[get_rate_limit_store] = lambda: m_rl.return_value
         
         # Deny policy
         m_is_allowed.return_value = False 
         
         payload = {
            "jsonrpc": "2.0",
            "method": "tasks.send",
            "id": 101,
            "params": {
                "profile": {
                    "profile_id": "a2a-compat",
                    "profile_version": "0.1",
                    "spec_source": "a2a-protocol"
                },
                "tool_call": {
                    "server_id": "srv-bad",
                    "tool_name": "rm_rf",
                    "arguments": {}
                },
                "input": [{"role": "user", "content": [{"text": "pic"}]}]
            } 
         }

         response = client.post("/a2a/v1/", json=payload, headers=AUTH_HEADERS)
         data = response.json()
         
         assert "error" in data
         assert data["error"]["code"] == -32000, f"Error: {data}"
         assert data["error"]["data"]["talos_code"] == "MCP_DENIED_TOOL"

@pytest.mark.asyncio
async def test_mcp_missing_scope(mock_auth_context):
    mock_auth_context.scopes = ["a2a.invoke"] # Missing mcp.invoke
    app.dependency_overrides[get_auth_context] = lambda: mock_auth_context
    
    with patch("app.dependencies.get_rate_limit_store") as m_rl:
         m_rl.return_value.check_limit = AsyncMock(return_value=MagicMock(allowed=True))
         app.dependency_overrides[get_rate_limit_store] = lambda: m_rl.return_value
         
         payload = {
            "jsonrpc": "2.0",
            "method": "tasks.send",
            "id": 102,
            "params": {
                "profile": {
                    "profile_id": "a2a-compat",
                    "profile_version": "0.1",
                    "spec_source": "a2a-protocol"
                },
                "input": [{"role": "user", "content": [{"text": "hi"}]}],
                "tool_call": {
                    "server_id": "srv-1", "tool_name": "echo", "arguments": {}
                }
            }
         }
         response = client.post("/a2a/v1/", json=payload, headers=AUTH_HEADERS)
         data = response.json()
         
         assert "error" in data, f"Response: {data}"
         assert data["error"]["code"] == -32000
         assert data["error"]["data"]["talos_code"] == "RBAC_DENIED"
