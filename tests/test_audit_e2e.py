
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
    # Return a Mock object that mimics AuditLogger
    logger = Mock()
    logger.log_event = Mock()
    logger.log_event_async = Mock()
    return logger

def test_audit_e2e_success(client, mock_audit_logger):
    # Setup: Allow "unknown" routes to pass through auth (or mock auth)
    # The middleware requires AuthContext to be set.
    # We can mock the auth middleware OR use a real token.
    # Using a real token is complex due to DB deps.
    # Let's mock `get_auth_context` to return a success context and set state.
    
    from app.middleware.auth_public import get_auth_context, AuthContext
    from app.domain.registry import SurfaceItem
    from app.adapters.postgres.session import get_db
    import app.dependencies as deps_module
    from app.dependencies import get_model_group_store, get_audit_logger, get_surface_registry

    # Mock DB
    app.dependency_overrides[get_db] = lambda: Mock()
    
    mock_mg_store = Mock()
    mock_mg_store.list_model_groups.return_value = [{"id": "gpt-4"}]
    app.dependency_overrides[get_model_group_store] = lambda: mock_mg_store
    
    # Patch direct singleton instance for middleware
    deps_module._audit_logger_instance = mock_audit_logger
    
    # Mock Registry
    mock_registry = Mock()
    mock_surface = SurfaceItem(
        id="test.op", type="http", required_scopes=[], attestation_required=False,
        audit_action="test.action", data_classification="public", audit_meta_allowlist=["field"]
    )
    mock_registry.match_request.return_value = mock_surface
    
    app.dependency_overrides[get_surface_registry] = lambda: mock_registry
    
    from fastapi import Request
    
    # Mock Auth Context
    async def mock_get_auth(request: Request):
        ctx = AuthContext(
            key_id="key-1", team_id="team-1", org_id="org-1", scopes=["llm.invoke", "*"], 
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
    print(f"DEBUG: Status={resp.status_code}, Body={resp.text}")
    
    # Assert
    assert mock_audit_logger.log_event.called
    # log_event args: (surface, principal, http_info, status, ...)
    # Retrieve call args
    call_args = mock_audit_logger.log_event.call_args
    _, kwargs = call_args
    
    assert kwargs["status"] == "success"
    assert kwargs["principal"]["principal_id"] == "p-1"
    assert kwargs["metadata"]["field"] == "value"
    
    # Note: The Mock logger won't produce the final 'event' structure unless we replicate build_event logic.
    # But we can verify inputs to log_event.
    pass

