"""Idempotency Hardening Tests for AI Gateway."""
import json
import pytest
from fastapi.testclient import TestClient
from unittest.mock import MagicMock, patch, AsyncMock

from app.api.a2a.routes import router
from fastapi import FastAPI
from app.middleware.auth_public import AuthContext

app = FastAPI()
app.include_router(router)

@pytest.fixture
def mock_auth():
    return AuthContext(
        key_id="test-key",
        team_id="test-team",
        org_id="test-org",
        scopes=["a2a.send"],
        allowed_model_groups=["*"],
        allowed_mcp_servers=["*"],
        principal_id="test-principal",
    )

def test_idempotency_deduplication():
    """Verify that multiple requests with the same nonce return the cached response."""
    from app.domain.a2a.dispatcher import A2ADispatcher
    
    with patch("app.api.a2a.routes.get_jsonrpc_auth_context") as mock_auth_dep, \
         patch("app.api.a2a.routes.get_routing_service"), \
         patch("app.api.a2a.routes.get_audit_store"), \
         patch("app.api.a2a.routes.get_rate_limit_store"), \
         patch("app.api.a2a.routes.get_usage_store"), \
         patch("app.api.a2a.routes.get_task_store"), \
         patch("app.api.a2a.routes.get_mcp_client"), \
         patch("app.api.a2a.routes.get_capability_validator"):
        
        mock_auth_dep.return_value = AuthContext(
            key_id="k", team_id="t", org_id="o", scopes=["a2a.send"], 
            allowed_model_groups=["*"], allowed_mcp_servers=["*"], principal_id="p"
        )
        
        with patch.object(A2ADispatcher, "_get_redis") as mock_get_redis:
            mock_redis = AsyncMock()
            mock_get_redis.return_value = mock_redis
            
            # First call: not in cache
            mock_redis.get.return_value = None
            
            # Mock the actual execution
            with patch.object(A2ADispatcher, "handle_send") as mock_send:
                mock_send.return_value = {"task_id": "123", "status": "queued"}
                
                client = TestClient(app)
                payload = {
                    "jsonrpc": "2.0",
                    "method": "tasks.send",
                    "params": {
                        "profile": {
                            "profile_id": "a2a-compat",
                            "profile_version": "0.1",
                            "spec_source": "a2a-protocol"
                        },
                        "input": [{
                            "role": "user",
                            "content": [{"type": "text", "text": "hello"}]
                        }]
                    },
                    "id": 1
                }
                
                # Request 1
                resp1 = client.post(
                    "/",
                    headers={"X-Talos-Nonce": "nonce-1", "Authorization": "Bearer sk-test-key"},
                    json=payload
                )
                assert resp1.status_code == 200
                assert resp1.json()["result"]["task_id"] == "123"                
                # Verify it was saved to Redis
                mock_redis.setex.assert_called_once()
                
                # Request 2: same nonce, should return cached
                mock_redis.get.return_value = json.dumps(resp1.json())
                
                resp2 = client.post(
                    "/",
                    headers={"X-Talos-Nonce": "nonce-1", "Authorization": "Bearer sk-test-key"},
                    json=payload
                )
                assert resp2.status_code == 200
                assert resp2.json() == resp1.json()
                
                # Verify handle_send was NOT called again
                mock_send.assert_called_once()
