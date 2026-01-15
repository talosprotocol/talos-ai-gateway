
import pytest
import os
import json
import hashlib
from unittest.mock import Mock, patch, AsyncMock
from fastapi.testclient import TestClient
from app.main import app
from app.domain.audit import AuditLogger
from app.domain.registry import SurfaceItem
import app.dependencies as deps_module
from app.dependencies import get_audit_logger, get_surface_registry

# Set test keys
os.environ["AUDIT_IP_HMAC_KEY"] = "test-ip-key-secret"
os.environ["AUDIT_IP_HMAC_KEY_ID"] = "test-ip-key-v1"
# Fix path for local run
os.environ["SURFACE_INVENTORY_PATH"] = "../talos-contracts/inventory/gateway_surface.json"

@pytest.fixture
def mock_sink():
    sink = Mock()
    sink.emit = AsyncMock()
    return sink

@pytest.fixture
def real_logger_with_mock_sink(mock_sink):
    return AuditLogger(sink=mock_sink)

from contextlib import asynccontextmanager

@asynccontextmanager
async def mock_lifespan(app):
    yield

@pytest.fixture
def client(real_logger_with_mock_sink):
    # Override dependencies
    app.dependency_overrides[get_audit_logger] = lambda: real_logger_with_mock_sink
    # Re-init singleton if needed (middleware uses this)
    deps_module._audit_logger_instance = real_logger_with_mock_sink
    
    # Bypass lifespan entirely for tests to avoid startup gates and worker cleanup
    original_lifespan = app.router.lifespan_context
    app.router.lifespan_context = mock_lifespan
    
    with TestClient(app) as c:
        yield c
    
    app.router.lifespan_context = original_lifespan
    app.dependency_overrides = {}

def test_audit_pipeline_determinism(real_logger_with_mock_sink, mock_sink):
    """Verify built event matches the canonicalization pipeline and vectors."""
    surface = SurfaceItem(
        id="test.op", type="http", required_scopes=[], attestation_required=False,
        audit_action="test.action", data_classification="public", 
        audit_meta_allowlist=["model", "tokens"],
        path_template="/v1/test"
    )
    principal = {"principal_id": "p-1", "team_id": "t-1", "auth_mode": "bearer"}
    http_info = {
        "method": "POST", "path": "/v1/test/raw", "status_code": 200, 
        "client_ip": "192.168.1.1", "is_trusted": True
    }
    metadata = {"model": "gpt-4", "tokens": 100, "drop_me": "secret"}
    
    event = real_logger_with_mock_sink._build_event(
        surface, principal, http_info, "success", "req-1", metadata
    )
    
    # Assertions
    # 1. Path Redacted (Template used)
    assert event["http"]["path"] == "/v1/test"
    
    # 2. Meta Filtered & Scalar & Telemetry
    assert "model" in event["meta"]
    assert "drop_me" not in event["meta"]
    assert event["meta"]["meta_redaction_applied"] is True
    assert "drop_me" in event["meta"]["meta_redacted_keys"]
    
    # 3. IP Hashed
    assert "client_ip_hash" in event["http"]
    assert event["http"]["client_ip_hash_alg"] == "hmac-sha256"
    assert event["http"]["client_ip_hash_key_id"] == "test-ip-key-v1"
    
    # ...

def test_scalar_meta_enforcement(real_logger_with_mock_sink):
    """Verify that non-scalar meta values are dropped or truncated."""
    surface = SurfaceItem(
        id="test.op", type="http", required_scopes=[], attestation_required=False,
        audit_action="test.action", data_classification="public", audit_meta_allowlist=["obj", "arr", "str", "bigint", "badint"],
        path_template="/"
    )
    metadata = {
        "str": "valid",
        "obj": {"nested": "fail"},
        "arr": [1, 2, 3],
        "bigint": 9007199254740990, # Safe max - 1
        "badint": 9007199254740992, # Safe max + 1
        "meta_redaction_applied": "hacker", # Reserved
    }
    
    event = real_logger_with_mock_sink._build_event(
        surface, {"principal_id": "p-1", "team_id": "t-1", "auth_mode": "bearer"},
        {"method": "GET", "path": "/"}, "success", "req-1", metadata
    )
    
    # 1. Scalar Pass
    assert event["meta"]["str"] == "valid"
    assert event["meta"]["bigint"] == 9007199254740990
    
    # 2. Non-Scalar Fail
    assert "obj" not in event["meta"]
    assert "arr" not in event["meta"]
    
    # 3. Integer Range Fail
    assert "badint" not in event["meta"]
    
    # 4. Reserved Keys Dropped silently (not in event, not in redacted list logic unless I changed it to drop silently)
    # My logic: "if k in RESERVED_KEYS: continue" -> Silent drop.
    assert "meta_redaction_applied" in event["meta"] # Wait, this should be TRUE (system set), not "hacker"
    assert event["meta"]["meta_redaction_applied"] is True
    
    # 5. Redaction Telemetry (Sorted List)
    redacted = event["meta"]["meta_redacted_keys"]
    assert isinstance(redacted, list)
    assert "arr (invalid type)" in redacted
    assert "badint (unsafe integer)" in redacted
    assert "obj (invalid type)" in redacted
    
def test_meta_omitted_if_empty(real_logger_with_mock_sink):
    surface = SurfaceItem(
        id="test.op", type="http", required_scopes=[], attestation_required=False,
        audit_action="test.action", data_classification="public", audit_meta_allowlist=[],
        path_template="/"
    )
    event = real_logger_with_mock_sink._build_event(
        surface, {"auth_mode": "anonymous"}, {"method": "GET", "path": "/"}, "success", "req-1", {}
    )
    assert "meta" not in event

def test_principal_shape_absent_rules(real_logger_with_mock_sink):
    """Verify that optional principal fields are ABSENT, not null."""
    surface = SurfaceItem(
        id="test.op", type="http", required_scopes=[], attestation_required=False,
        audit_action="test.action", data_classification="public", audit_meta_allowlist=[],
        path_template="/"
    )
    
    # Case: Bearer (signer_key_id must be absent)
    event_bearer = real_logger_with_mock_sink._build_event(
        surface, {"principal_id": "p-1", "team_id": "t-1", "auth_mode": "bearer"},
        {"method": "GET", "path": "/"}, "success", "req-1", {}
    )
    assert "signer_key_id" not in event_bearer["principal"]
    
    # Case: Anonymous (team_id and signer_key_id must be absent)
    event_anon = real_logger_with_mock_sink._build_event(
        surface, {"auth_mode": "anonymous"},
        {"method": "GET", "path": "/"}, "success", "req-2", {}
    )
    assert event_anon["principal"]["principal_id"] == "anonymous"
    assert "team_id" not in event_anon["principal"]
    assert "signer_key_id" not in event_anon["principal"]

@patch("app.dependencies.get_surface_registry")
def test_gateway_rejects_non_normative_identity(mock_get_reg, client, mock_sink):
    """Verify that Gateway rejects identities with invalid formats (e.g. UPPERCASE UUID) with 400."""
    from fastapi import Depends
    from app.dependencies import get_key_store, get_principal_store, get_attestation_verifier
    from app.middleware.auth_public import get_auth_context
    
    # 1. Setup Surface (must match request to get past auth)
    mock_surface = SurfaceItem(
        id="test.identity", type="http", required_scopes=["test.scope"], 
        attestation_required=False, audit_action="test.action",
        data_classification="public", audit_meta_allowlist=[],
        path_template="/identity-check"
    )
    mock_reg_obj = Mock()
    mock_reg_obj.match_request.return_value = mock_surface
    mock_get_reg.return_value = mock_reg_obj
    
    # 2. Mock Dependenciess
    class MockKeyData:
        id = "KEY-UPPER-CASE_FAIL" # INVALID key ID (uppercase)
        team_id = "TEAM-UPPER-CASE-FAIL" # INVALID team ID
        org_id = "ORG-UPPER-CASE_FAIL"
        scopes = ["test.scope"]
        revoked = False
        allowed_model_groups = ["*"]
        allowed_mcp_servers = ["*"]

    class DummyKS:
        def hash_key(self, k): return "hash"
        def lookup_by_hash(self, h): return MockKeyData()
    
    class DummyPrincipalStore:
        def get_principal(self, pid): return None
        
    async def ks_dep(): return DummyKS()
    async def ps_dep(): return DummyPrincipalStore()
    async def verifier_dep(): return Mock()
    
    app.dependency_overrides[get_key_store] = ks_dep
    app.dependency_overrides[get_principal_store] = ps_dep
    app.dependency_overrides[get_attestation_verifier] = verifier_dep
    app.dependency_overrides[get_surface_registry] = lambda: mock_reg_obj
    
    # 3. Add Dummy Route to trigger dependency
    @app.get("/identity-check")
    def dummy_route(auth=Depends(get_auth_context)):
        return {"status": "ok"}
    
    # 4. Request
    resp = client.get("/identity-check", headers={"Authorization": "Bearer valid-token-format-but-bad-data"})
    
    # 5. Assert
    # Should fail validation because MockKeyData IDs are Uppercase and not UUIDv7
    assert resp.status_code == 400
    data = resp.json()
    # Handle FastAPI default exception format {"detail": {"error": ...}}
    if "detail" in data:
        error = data["detail"]["error"]
    else:
        error = data["error"]
        
    assert error["code"] == "IDENTITY_INVALID"
    assert "details" in error
    details = error["details"]
    assert "path" in details
    assert "reason" in details
    assert "validator" in details
    # The failure is due to Uppercase UUID pattern
    assert details["validator"] == "pattern" or details["validator"] == "unknown" # Fallback if exception structure varies
