from fastapi.testclient import TestClient
from app.main import app
from app.settings import settings
import pytest
from unittest.mock import patch, AsyncMock, MagicMock
from datetime import datetime
from app.middleware.auth_public import get_auth_context
from app.dependencies import get_audit_store, get_rate_limit_store, get_usage_store, get_routing_service

client = TestClient(app)
AUTH_HEADERS = {"Authorization": "Bearer sk-test-key"}

@pytest.fixture(autouse=True)
def clear_overrides():
    app.dependency_overrides = {}
    yield
    app.dependency_overrides = {}

@pytest.fixture
def mock_auth_context():
    auth_ctx = MagicMock()
    auth_ctx.key_id = "key-123"
    auth_ctx.team_id = "team-1"
    auth_ctx.org_id = "org-1"
    auth_ctx.scopes = ["a2a.invoke", "llm.invoke", "a2a.stream"]
    auth_ctx.allowed_model_groups = ["*"]
    return auth_ctx

@pytest.fixture
def mock_settings_visibility():
    orig = settings.a2a_agent_card_visibility
    yield
    settings.a2a_agent_card_visibility = orig

def test_agent_card_public(mock_settings_visibility):
    settings.a2a_agent_card_visibility = "public"
    response = client.get("/.well-known/agent-card.json")
    assert response.status_code == 200
    data = response.json()
    assert data["profile"]["profile_id"] == "a2a-compat"
    assert "Cache-Control" in response.headers

def test_agent_card_auth_required_fail(mock_settings_visibility):
    settings.a2a_agent_card_visibility = "auth_required"
    response = client.get("/.well-known/agent-card.json")
    assert response.status_code == 401

def test_agent_card_disabled(mock_settings_visibility):
    settings.a2a_agent_card_visibility = "disabled"
    response = client.get("/.well-known/agent-card.json")
    assert response.status_code == 404

def test_jsonrpc_auth_required():
    # Attempt without auth headers
    response = client.post("/a2a/v1/", json={"method": "foo"})
    assert response.status_code == 401

def test_jsonrpc_invalid_request(mock_auth_context):
    app.dependency_overrides[get_auth_context] = lambda: mock_auth_context
    # Valid auth, but invalid JSON-RPC (missing jsonrpc version)
    response = client.post("/a2a/v1/", json={"method": "foo"}, headers=AUTH_HEADERS)
    assert response.status_code == 200
    data = response.json()
    assert "error" in data, f"Response: {data}"
    assert data["error"]["code"] == -32600

def test_jsonrpc_method_not_found(mock_auth_context):
    app.dependency_overrides[get_auth_context] = lambda: mock_auth_context
    response = client.post("/a2a/v1/", json={
        "jsonrpc": "2.0",
        "method": "tasks.foo",
        "id": 1,
        "params": {}
    }, headers=AUTH_HEADERS)
    data = response.json()
    assert "error" in data, f"Response: {data}"
    # Since schema enforces method pattern, this behaves as Invalid Request (-32600)
    assert data["error"]["code"] in [-32600, -32601]

def test_tasks_send_missing_scope(mock_auth_context):
    mock_auth_context.scopes = ["llm.invoke"] # Missing a2a
    app.dependency_overrides[get_auth_context] = lambda: mock_auth_context
    
    response = client.post("/a2a/v1/", json={
        "jsonrpc": "2.0",
        "method": "tasks.send",
        "id": 1,
        "params": {
            "profile": {
                 "profile_id": "a2a-compat",
                 "profile_version": "0.1",
                 "spec_source": "a2a-protocol"
            },
            "input": [{"role": "user", "content": [{"text": "hi"}]}] 
            # Note: Must be valid params to pass envelope validation first
        }
    }, headers=AUTH_HEADERS)
    
    data = response.json()
    assert "error" in data, f"Response: {data}"
    assert data["error"]["code"] == -32000
    assert data["error"]["data"]["talos_code"] == "RBAC_DENIED"

@pytest.mark.asyncio
async def test_security_regression_tool_bypass(mock_auth_context):
    app.dependency_overrides[get_auth_context] = lambda: mock_auth_context
    
    # We must mock upstream to verify what is actually sent to LLM
    with patch("app.dependencies.get_audit_store") as m_audit, \
         patch("app.dependencies.get_rate_limit_store") as m_rl, \
         patch("app.dependencies.get_usage_store") as m_usage, \
         patch("app.dependencies.get_routing_service") as m_routing, \
         patch("app.domain.a2a.dispatcher.invoke_openai_compatible", new_callable=AsyncMock) as mock_invoke:

         m_rl.return_value.check_limit = AsyncMock(return_value=MagicMock(allowed=True))
         m_routing.return_value.select_upstream.return_value = {
             "upstream": {"endpoint": "http://mock", "id": "u1"},
             "model_name": "gpt-4"
         }
         mock_invoke.return_value = {
             "choices": [{"message": {"content": "ignored tool"}}],
             "usage": {"prompt_tokens": 1, "completion_tokens": 1}
         }
         
         app.dependency_overrides[get_audit_store] = lambda: m_audit.return_value
         app.dependency_overrides[get_rate_limit_store] = lambda: m_rl.return_value
         app.dependency_overrides[get_usage_store] = lambda: m_usage.return_value
         app.dependency_overrides[get_routing_service] = lambda: m_routing.return_value

         response = client.post("/a2a/v1/", json={
            "jsonrpc": "2.0",
            "method": "tasks.send",
            "id": 1,
            "params": {
                    "profile": {
                         "profile_id": "a2a-compat",
                         "profile_version": "0.1",
                         "spec_source": "a2a-protocol"
                    }, 
                    "input": [{"role": "user", "content": [{"text": "hi"}]}],
                    "tool_call": "some_func" 
            }
         }, headers=AUTH_HEADERS)
    
         # Request should succeed (ignoring extra param)
         assert response.status_code == 200
         
         # Verify LLM invoke was called WITHOUT tool_call data
         args, kwargs = mock_invoke.call_args
         # kwargs['messages'] should match input
         # kwargs['tools'] should be None or empty
         assert kwargs.get("tools") is None
         assert len(kwargs["messages"]) == 1

def test_tasks_get_not_implemented(mock_auth_context):
    app.dependency_overrides[get_auth_context] = lambda: mock_auth_context
    response = client.post("/a2a/v1/", json={
        "jsonrpc": "2.0",
        "method": "tasks.get",
        "id": 1,
        "params": {"task_id": "123"}
    }, headers=AUTH_HEADERS)
    data = response.json()
    assert "error" in data
    assert data["error"]["code"] == -32000
    assert data["error"]["data"]["talos_code"] == "NOT_IMPLEMENTED"

@pytest.mark.asyncio
async def test_tasks_send_happy_path(mock_auth_context):
    app.dependency_overrides[get_auth_context] = lambda: mock_auth_context
    
    # Mock stores
    with patch("app.dependencies.get_audit_store") as m_audit, \
         patch("app.dependencies.get_rate_limit_store") as m_rl, \
         patch("app.dependencies.get_usage_store") as m_usage, \
         patch("app.dependencies.get_routing_service") as m_routing, \
         patch("app.domain.a2a.dispatcher.invoke_openai_compatible", new_callable=AsyncMock) as mock_invoke:

            m_rl.return_value.check_limit = AsyncMock(return_value=MagicMock(allowed=True))
            
            # Mock select_upstream to return something
            select_res = {
                "upstream": {"endpoint": "http://mock", "id": "u1"},
                "model_name": "gpt-4"
            }
            m_routing.return_value.select_upstream.return_value = select_res

            # Mock invoke result
            mock_invoke.return_value = {
                "choices": [{"message": {"content": "Hello A2A"}}],
                "usage": {"prompt_tokens": 5, "completion_tokens": 5}
            }

            app.dependency_overrides[get_audit_store] = lambda: m_audit.return_value
            app.dependency_overrides[get_rate_limit_store] = lambda: m_rl.return_value
            app.dependency_overrides[get_usage_store] = lambda: m_usage.return_value
            app.dependency_overrides[get_routing_service] = lambda: m_routing.return_value

            payload = {
                "jsonrpc": "2.0",
                "method": "tasks.send",
                "id": "req-1",
                "params": {
                    "profile": {
                        "profile_id": "a2a-compat",
                        "profile_version": "0.1", 
                        "spec_source": "a2a-protocol"
                    },
                    "input": [
                        {"role": "user", "content": [{"text": "Hi"}]}
                    ]
                }
            }
            
            response = client.post("/a2a/v1/", json=payload, headers=AUTH_HEADERS)
            assert response.status_code == 200, f"Error: {response.text}"
            data = response.json()
            
            assert "result" in data, f"Error: {data.get('error')}"
            task = data["result"]
            assert task["status"] == "completed"
            assert task["artifacts"][0]["content"]["text"] == "Hello A2A"
