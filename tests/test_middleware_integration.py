import pytest
from unittest.mock import AsyncMock, Mock
from fastapi import HTTPException
from app.middleware.auth_public import get_auth_context
from app.domain.registry import SurfaceItem

@pytest.fixture
def mock_deps():
    return {
        "key_store": Mock(),
        "verifier": AsyncMock(),
        "principal_store": Mock(),
        "registry": Mock(),
        "audit_logger": Mock()
    }

@pytest.mark.asyncio
async def test_auth_flow_success(mock_deps):
    """Test full happy path: Registry -> Bearer -> Scope -> Attestation -> Binding."""
    # Setup Logic
    path = "/v1/chat/completions"
    method = "POST"
    
    # 1. Registry Match (Strict Opcode)
    surface = SurfaceItem(
        id="llm.chat.completions",
        type="http",
        required_scopes=["llm.invoke"],
        attestation_required=True,
        audit_action="test.action",
        data_classification="sensitive"
    )
    mock_deps["registry"].match_request.return_value = surface
    
    # 2. Key Store (Bearer)
    key_data = Mock()
    key_data.id = "key-123"
    key_data.team_id = "team-abc"
    key_data.org_id = "org-xyz"
    key_data.scopes = ["llm.invoke"] # Has required scope
    key_data.revoked = False
    mock_deps["key_store"].lookup_by_hash.return_value = key_data
    
    # 3. Principal Store (Binding)
    principal = {"id": "principal-999", "team_id": "team-abc"} # Matches team
    mock_deps["principal_store"].get_principal.return_value = principal
    
    # 4. Verifier
    mock_deps["verifier"].verify_request.return_value = "key-signer-111"

    # Request
    request = Mock()
    request.method = method
    request.route.path = path
    request.scope = {"raw_path": path.encode("ascii"), "query_string": b""}
    request.headers = {"Authorization": "Bearer foo", "X-Talos-Signature": "sig"}
    request.body = AsyncMock(return_value=b"{}")
    
    # Execute
    ctx = await get_auth_context(
        request, 
        authorization="Bearer foo", 
        x_talos_signature="sig",
        **mock_deps
    )
    
    # Assertions
    assert ctx.principal_id == "principal-999"
    assert ctx.team_id == "team-abc"
    mock_deps["registry"].match_request.assert_called_with(method, path)
    # Ensure verifier called with correct opcode from surface
    mock_deps["verifier"].verify_request.assert_called()
    assert mock_deps["verifier"].verify_request.call_args[0][4] == "llm.chat.completions"

@pytest.mark.asyncio
async def test_auth_flow_missing_scope(mock_deps):
    """Test RBAC Denial."""
    surface = SurfaceItem(
        id="test.op", type="http", required_scopes=["admin.read"], 
        attestation_required=False, audit_action="a", data_classification="public"
    )
    mock_deps["registry"].match_request.return_value = surface
    
    key_data = Mock()
    key_data.revoked = False
    key_data.scopes = ["user.read"] # Missing admin.read
    mock_deps["key_store"].lookup_by_hash.return_value = key_data

    request = Mock()
    request.route.path = "/admin"
    request.method = "GET"
    request.headers = {}
    
    with pytest.raises(HTTPException) as exc:
        await get_auth_context(request, authorization="Bearer foo", **mock_deps)
    
    err = exc.value.detail["error"]
    assert exc.value.status_code == 403
    assert err["code"] == "RBAC_DENIED"

@pytest.mark.asyncio
async def test_auth_flow_attestation_missing(mock_deps):
    """Test Attestation Required but missing header."""
    surface = SurfaceItem(
        id="secure.op", type="http", required_scopes=["scope"], 
        attestation_required=True, audit_action="a", data_classification="public"
    )
    mock_deps["registry"].match_request.return_value = surface
    
    key_data = Mock()
    key_data.revoked = False
    key_data.scopes = ["scope"]
    mock_deps["key_store"].lookup_by_hash.return_value = key_data

    request = Mock()
    request.route.path = "/secure"
    request.method = "POST"
    request.headers = {}
    
    with pytest.raises(HTTPException) as exc:
        await get_auth_context(
            request, 
            authorization="Bearer foo", 
            x_talos_signature=None, # Missing
            **mock_deps
        )
    
    err = exc.value.detail["error"]
    assert exc.value.status_code == 401
    assert err["code"] == "AUTH_INVALID"
    assert "Attestation required" in err["message"]

@pytest.mark.asyncio
async def test_auth_flow_identity_binding_fail(mock_deps):
    """Test Team ID Mismatch."""
    surface = SurfaceItem(
        id="op", type="http", required_scopes=["scope"], 
        attestation_required=True, audit_action="a", data_classification="public"
    )
    mock_deps["registry"].match_request.return_value = surface
    
    key_data = Mock()
    key_data.id = "key-A"
    key_data.team_id = "Team-A"
    key_data.scopes = ["scope"]
    key_data.revoked = False
    mock_deps["key_store"].lookup_by_hash.return_value = key_data
    
    # Principal is from Team-B -> Mismatch!
    principal = {"id": "p-1", "team_id": "Team-B"}
    mock_deps["principal_store"].get_principal.return_value = principal
    mock_deps["verifier"].verify_request.return_value = "signer-key"

    request = Mock()
    request.route.path = "/"
    request.method = "GET"
    request.scope = {"raw_path": b"/", "query_string": b""}
    request.body = AsyncMock(return_value=b"")
    request.headers = {"Authorization": "Bearer foo", "X-Talos-Signature": "sig"}

    with pytest.raises(HTTPException) as exc:
        await get_auth_context(
             request, authorization="Bearer foo", x_talos_signature="sig", **mock_deps
        )
    
    err = exc.value.detail["error"]
    assert exc.value.status_code == 403
    assert err["code"] == "RBAC_DENIED"
    assert "binding mismatch" in err["message"]
