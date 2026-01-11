
import pytest
from unittest.mock import Mock, patch
from fastapi.testclient import TestClient
from app.main import app
from app.domain.audit import AuditLogger
import json
import hashlib

@pytest.fixture
def client():
    return TestClient(app)

@pytest.fixture
def mock_audit_logger():
    with patch("app.domain.audit.audit_logger") as mock:
        yield mock

def test_audit_e2e_success(client, mock_audit_logger):
    # Setup: Allow "unknown" routes to pass through auth (or mock auth)
    # The middleware requires AuthContext to be set.
    # We can mock the auth middleware OR use a real token.
    # Using a real token is complex due to DB deps.
    # Let's mock `get_auth_context` to return a success context and set state.
    
    from app.middleware.auth_public import get_auth_context, AuthContext
    from app.domain.registry import get_surface_registry, SurfaceItem
    
    # Mock Registry
    mock_registry = Mock()
    mock_surface = SurfaceItem(
        id="test.op", type="http", required_scopes=[], attestation_required=False,
        audit_action="test.action", data_classification="public", audit_meta_allowlist=["field"]
    )
    mock_registry.match_request.return_value = mock_surface
    
    app.dependency_overrides[get_surface_registry] = lambda: mock_registry
    
    # Mock Auth Context
    async def mock_get_auth(request):
        ctx = AuthContext(
            key_id="key-1", team_id="team-1", org_id="org-1", scopes=["*"], 
            allowed_model_groups=["*"], allowed_mcp_servers=["*"], principal_id="p-1"
        )
        request.state.auth = ctx
        request.state.surface = mock_surface
        request.state.audit_meta = {"field": "value", "secret": "hide"}
        return ctx

    app.dependency_overrides[get_auth_context] = mock_get_auth
    
    # Make Request
    # We need a route that exists. /health is unbound audit-wise?
    # No, we need a route that uses Depends(get_auth_context)
    # Let's use /v1/models (llm.models.list) which is mapped.
    
    resp = client.get("/v1/models")
    
    # Assert
    assert mock_audit_logger.info.called
    log_call = mock_audit_logger.info.call_args[0][0]
    event = json.loads(log_call)
    
    assert event["status"] == "success"
    assert event["principal"]["principal_id"] == "p-1"
    assert event["meta"]["field"] == "value"
    assert "secret" not in event["meta"] # Dropped by allowlist
    
    # Verify Hash
    assert "event_hash" in event
    # Re-calculate hash to verify deterministic
    event_no_hash = {k:v for k,v in event.items() if k != "event_hash"}
    canonical = json.dumps(event_no_hash, sort_keys=True, separators=(',', ':')).encode('utf-8')
    expected_hash = hashlib.sha256(canonical).hexdigest()
    assert event["event_hash"] == expected_hash

