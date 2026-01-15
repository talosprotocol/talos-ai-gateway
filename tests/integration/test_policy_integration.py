import pytest
from unittest.mock import Mock
from fastapi.testclient import TestClient
from fastapi import Depends
from app.main import app
from app.dependencies import get_key_store, get_policy_engine, get_surface_registry, get_attestation_verifier, get_audit_logger, get_principal_store
from app.policy import DeterministicPolicyEngine
from app.domain.registry import SurfaceItem
from app.middleware.auth_public import get_auth_context

# Mock Data
MOCK_ROLES = {
    "role-integration": {
        "id": "role-integration",
        "permissions": ["test:policy_access"]
    }
}

MOCK_BINDINGS = {
    "01946765-c7e0-798c-8c65-22d7a64b91f5": [
        {
            "principal_id": "01946765-c7e0-798c-8c65-22d7a64b91f5",
            "role_id": "role-integration",
            "scope": {"type": "platform"}
        }
    ]
}

@pytest.fixture
def policy_engine_with_binding():
    return DeterministicPolicyEngine(MOCK_ROLES, MOCK_BINDINGS)

def test_policy_engine_grants_access(policy_engine_with_binding):
    """
    Verify that Policy Engine grants access even if legacy scopes are missing.
    """
    # 1. Mock Key Store with Key that has NO legacy scopes
    class MockKeyData:
        id = "01946765-c7e0-798c-8c65-22d7a64b91f5" # Valid UUIDv7
        team_id = "01946765-c7e0-798c-8c65-22d7a64b91f6" # Valid UUIDv7
        org_id = "01946765-c7e0-798c-8c65-22d7a64b91f7" # Valid UUIDv7
        scopes = ["legacy:other"] # Does NOT match required scope
        revoked = False
        allowed_model_groups = ["*"]
        allowed_mcp_servers = ["*"]

    class MockKS:
        def hash_key(self, k): return "hash"
        def lookup_by_hash(self, h): return MockKeyData()

    # 2. Mock Registry (Require specific permission)
    mock_surface = SurfaceItem(
        id="test.op", type="http", required_scopes=["test:policy_access"], 
        attestation_required=False, audit_action="test.action", 
        data_classification="public", audit_meta_allowlist=[],
        path_template="/policy-check"
    )
    mock_reg = Mock()
    mock_reg.match_request.return_value = mock_surface

    # 3. Overrides
    app.dependency_overrides[get_key_store] = lambda: MockKS()
    app.dependency_overrides[get_policy_engine] = lambda: policy_engine_with_binding
    app.dependency_overrides[get_surface_registry] = lambda: mock_reg
    # Mock others to avoid errors
    app.dependency_overrides[get_attestation_verifier] = lambda: Mock()
    app.dependency_overrides[get_audit_logger] = lambda: Mock(log_event=Mock())
    app.dependency_overrides[get_principal_store] = lambda: Mock(get_principal=lambda pid: None)

    # 4. Route
    @app.get("/policy-check")
    def policy_route(auth=Depends(get_auth_context)):
         return {"status": "ok"}

    client = TestClient(app)
    
    # 5. Execute
    resp = client.get("/policy-check", headers={"Authorization": "Bearer token"})
    
    # 6. Assert
    if resp.status_code != 200:
        print("Response:", resp.json())
    assert resp.status_code == 200
    app.dependency_overrides = {}

def test_policy_engine_denies_access(policy_engine_with_binding):
    """
    Verify that access is denied if neither Policy nor Legacy Scopes grant it.
    """
    class MockKeyData:
        id = "key-policy-denied" # No bindings for this ID
        team_id = "team-1"
        org_id = "org-1"
        scopes = ["legacy:other"] # Still no match
        revoked = False
    
    class MockKS:
        def hash_key(self, k): return "hash"
        def lookup_by_hash(self, h): return MockKeyData()
        
    # Same surface setup...
    mock_surface = SurfaceItem(
        id="test.op", type="http", required_scopes=["test:policy_access"], 
        attestation_required=False, audit_action="test.action", 
        data_classification="public", audit_meta_allowlist=[],
        path_template="/policy-check-deny"
    )
    mock_reg = Mock()
    mock_reg.match_request.return_value = mock_surface

    app.dependency_overrides[get_key_store] = lambda: MockKS()
    app.dependency_overrides[get_policy_engine] = lambda: policy_engine_with_binding
    app.dependency_overrides[get_surface_registry] = lambda: mock_reg
    app.dependency_overrides[get_attestation_verifier] = lambda: Mock()
    app.dependency_overrides[get_audit_logger] = lambda: Mock(log_event=Mock())
    app.dependency_overrides[get_principal_store] = lambda: Mock(get_principal=lambda pid: None)

    @app.get("/policy-check-deny")
    def policy_route_deny(auth=Depends(get_auth_context)):
         return {"status": "ok"}

    client = TestClient(app)
    resp = client.get("/policy-check-deny", headers={"Authorization": "Bearer token"})
    
    assert resp.status_code == 403
    assert "Policy denied" in resp.json()["detail"]["error"]["message"]
    app.dependency_overrides = {}
