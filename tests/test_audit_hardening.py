
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
    assert event["http"]["client_ip_hash_alg"] == "HMAC-SHA256"
    assert event["http"]["client_ip_hash_key_id"] == "test-ip-key-v1"
    
    # 4. Normative Format: ts (3 ms digits), event_id (uuid7)
    assert event["ts"].endswith("Z")
    assert "." in event["ts"]
    ms_part = event["ts"].split(".")[1][:-1]
    assert len(ms_part) == 3
    
    # UUID7 pattern check
    import re
    uuid7_regex = r"^[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
    assert re.match(uuid7_regex, event["event_id"])
    
    # 5. Outcome
    assert event["outcome"] == "success"

    # 6. Hash Determinism
    # Re-calculate hash manually using strict JCS
    clean = {k: v for k, v in event.items() if k != "event_hash"}
    canonical = json.dumps(clean, sort_keys=True, separators=(',', ':'), ensure_ascii=False)
    expected_hash = hashlib.sha256(canonical.encode('utf-8')).hexdigest()
    assert event["event_hash"] == expected_hash

@patch("app.dependencies.get_surface_registry")
def test_single_emission_on_denied(mock_get_reg, client, mock_sink):
    """Verify that a denied request only emits ONE audit event (from AuthMiddleware)."""
    from app.adapters.postgres.session import get_db
    from app.dependencies import get_key_store, get_principal_store, get_attestation_verifier
    
    # 1. Setup Surface and Mock Registry
    mock_surface = SurfaceItem(
        id="test.denied", type="http", required_scopes=["required.scope"], 
        attestation_required=False, audit_action="test.deny",
        data_classification="public", audit_meta_allowlist=["error"],
        path_template="/v1/models"
    )
    
    mock_reg_obj = Mock()
    mock_reg_obj.match_request.return_value = mock_surface
    mock_get_reg.return_value = mock_reg_obj
    
    # Concrete dummies to avoid Pydantic/FastAPI validation issues with bare Mocks
    class DummyKS:
        def hash_key(self, k): return "hash"
        def lookup_by_hash(self, h): return None # 401
    
    class DummyVerifier:
        async def verify_request(self, *args, **kwargs): return "key-1"
        
    async def ks_dep(): return DummyKS()
    async def verifier_dep(): return DummyVerifier()
    
    # Override DB deps
    app.dependency_overrides[get_db] = lambda: Mock()
    app.dependency_overrides[get_key_store] = ks_dep
    app.dependency_overrides[get_principal_store] = lambda: Mock()
    app.dependency_overrides[get_attestation_verifier] = verifier_dep
    app.dependency_overrides[get_surface_registry] = lambda: mock_reg_obj
    
    # 2. Mock Auth Failure
    # Force a 401 by not providing a valid key in the mock
    resp = client.get("/v1/models", headers={
        "Authorization": "Bearer some-token",
        "X-Talos-Signature": "dummy-sig"
    }) 
    assert resp.status_code == 401
    
    # 3. Verify Emission Count
    # AuthMiddleware logs On Exception -> increments audit_emitted.
    # AuditMiddleware checks audit_emitted -> skips.
    assert mock_sink.emit.call_count == 1
    
    # 4. Verify Content
    call_args = mock_sink.emit.call_args[0][0]
    assert call_args["outcome"] == "denied"
    assert "error" in call_args["meta"]

def test_missing_ip_omits_fields(real_logger_with_mock_sink):
    """Verify that 'unknown' or localhost IPs omit the hashing fields."""
    surface = SurfaceItem(
        id="test.op", type="http", required_scopes=[], attestation_required=False,
        audit_action="test.action", data_classification="public", audit_meta_allowlist=[],
        path_template="/"
    )
    principal = {"principal_id": "p-1", "team_id": "t-1", "auth_mode": "bearer"}
    
    # Case: Unknown IP
    event_unknown = real_logger_with_mock_sink._build_event(
        surface, principal, {"method": "GET", "path": "/", "status_code": 200, "client_ip": "unknown"}, 
        "success", "req-1", {}
    )
    assert "client_ip_hash" not in event_unknown["http"]
    
    # Case: Localhost
    event_local = real_logger_with_mock_sink._build_event(
        surface, principal, {"method": "GET", "path": "/", "status_code": 200, "client_ip": "127.0.0.1"}, 
        "success", "req-2", {}
    )
    assert "client_ip_hash" not in event_local["http"]

def test_scalar_meta_enforcement(real_logger_with_mock_sink):
    """Verify that non-scalar meta values are dropped."""
    surface = SurfaceItem(
        id="test.op", type="http", required_scopes=[], attestation_required=False,
        audit_action="test.action", data_classification="public", audit_meta_allowlist=["obj", "arr", "str"],
        path_template="/"
    )
    metadata = {
        "str": "valid",
        "obj": {"nested": "fail"},
        "arr": [1, 2, 3]
    }
    
    event = real_logger_with_mock_sink._build_event(
        surface, {"principal_id": "p-1", "team_id": "t-1", "auth_mode": "bearer"},
        {"method": "GET", "path": "/"}, "success", "req-1", metadata
    )
    
    assert event["meta"]["str"] == "valid"
    assert "obj" not in event["meta"]
    assert "arr" not in event["meta"]

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
