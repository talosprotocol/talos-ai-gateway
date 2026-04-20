import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from unittest.mock import MagicMock, patch
from app.middleware.auth_admin import get_rbac_context, RbacContext
from app.adapters.postgres.models import RoleBinding, Role
from sqlalchemy.orm import Session
from app.main import app
import asyncio

# To run async functions in tests
def run_async(coro):
    return asyncio.run(coro)

def test_get_rbac_context_no_auth():
    """Verify that missing Authorization header raises 401."""
    with pytest.raises(HTTPException) as excinfo:
        run_async(get_rbac_context(authorization=None, db=MagicMock(spec=Session)))
    assert excinfo.value.status_code == 401
    assert excinfo.value.detail["error"]["code"] == "AUTH_MISSING"

def test_get_rbac_context_invalid_header():
    """Verify that non-Bearer Authorization header raises 401."""
    with pytest.raises(HTTPException) as excinfo:
        run_async(get_rbac_context(authorization="Basic dXNlcjpwYXNz", db=MagicMock(spec=Session)))
    assert excinfo.value.status_code == 401
    assert excinfo.value.detail["error"]["code"] == "AUTH_MISSING"

@patch("app.middleware.auth_admin.get_admin_validator")
def test_get_rbac_context_invalid_token(mock_get_validator):
    """Verify that invalid token raises 401."""
    validator = MagicMock()
    validator.validate_token.side_effect = Exception("Invalid token")
    mock_get_validator.return_value = validator
    
    with pytest.raises(HTTPException) as excinfo:
        run_async(get_rbac_context(authorization="Bearer invalid-token", db=MagicMock(spec=Session)))
    assert excinfo.value.status_code == 401
    assert excinfo.value.detail["error"]["code"] == "AUTH_INVALID"

@patch("app.middleware.auth_admin.get_admin_validator")
def test_get_rbac_context_no_permissions(mock_get_validator):
    """Verify that valid token but no RBAC permissions raises 403."""
    validator = MagicMock()
    validator.validate_token.return_value = {"sub": "user-without-perms"}
    mock_get_validator.return_value = validator
    
    db = MagicMock(spec=Session)
    # Mock query for RoleBinding returning empty list
    db.query.return_value.filter.return_value.all.return_value = []
    
    with pytest.raises(HTTPException) as excinfo:
        run_async(get_rbac_context(authorization="Bearer valid-token", db=db))
    assert excinfo.value.status_code == 403
    assert excinfo.value.detail["error"]["code"] == "RBAC_DENIED"

@patch("app.middleware.auth_admin.get_admin_validator")
def test_get_rbac_context_success(mock_get_validator):
    """Verify that valid token and permissions returns RbacContext."""
    validator = MagicMock()
    validator.validate_token.return_value = {"sub": "admin-user"}
    mock_get_validator.return_value = validator
    
    db = MagicMock(spec=Session)
    
    binding = RoleBinding(principal_id="admin-user", role_id="admin-role")
    role = Role(id="admin-role", permissions=["llm.read", "llm.admin"])
    
    # Mock binding lookup
    query_mock = MagicMock()
    db.query.return_value = query_mock
    query_mock.filter.return_value.all.return_value = [binding]
    # Mock role lookup
    query_mock.filter.return_value.first.return_value = role
    
    context = run_async(get_rbac_context(authorization="Bearer valid-token", db=db))
    
    assert context.principal_id == "admin-user"
    assert "llm.read" in context.effective_permissions
    assert "llm.admin" in context.effective_permissions

@patch("app.middleware.auth_admin.get_admin_validator")
def test_get_rbac_context_session_permissions_narrow_rbac(mock_get_validator):
    """Session JWT permissions narrow DB-granted RBAC for the request session."""
    validator = MagicMock()
    validator.validate_token.return_value = {
        "sub": "admin-user",
        "sid": "postman-session",
        "rbac_permissions": ["llm.read"],
    }
    mock_get_validator.return_value = validator

    db = MagicMock(spec=Session)
    binding = RoleBinding(principal_id="admin-user", role_id="admin-role")
    role = Role(id="admin-role", permissions=["llm.read", "llm.admin"])

    query_mock = MagicMock()
    db.query.return_value = query_mock
    query_mock.filter.return_value.all.return_value = [binding]
    query_mock.filter.return_value.first.return_value = role

    context = run_async(get_rbac_context(authorization="Bearer valid-token", db=db))

    assert context.principal_id == "admin-user"
    assert context.has_permission("llm.read")
    assert not context.has_permission("llm.admin")
    assert context.effective_permissions == {"llm.read"}

@patch("app.middleware.auth_admin.get_admin_validator")
def test_get_rbac_context_rejects_ungranted_session_permissions(mock_get_validator):
    """Session JWT permissions cannot broaden DB-granted RBAC."""
    validator = MagicMock()
    validator.validate_token.return_value = {
        "sub": "admin-user",
        "sid": "postman-session",
        "rbac_permissions": ["llm.admin"],
    }
    mock_get_validator.return_value = validator

    db = MagicMock(spec=Session)
    binding = RoleBinding(principal_id="admin-user", role_id="viewer-role")
    role = Role(id="viewer-role", permissions=["llm.read"])

    query_mock = MagicMock()
    db.query.return_value = query_mock
    query_mock.filter.return_value.all.return_value = [binding]
    query_mock.filter.return_value.first.return_value = role

    with pytest.raises(HTTPException) as excinfo:
        run_async(get_rbac_context(authorization="Bearer valid-token", db=db))

    assert excinfo.value.status_code == 403
    assert excinfo.value.detail["error"]["code"] == "RBAC_DENIED"

@patch("app.middleware.auth_admin.get_admin_validator")
def test_get_rbac_context_rejects_unregistered_session_permissions(mock_get_validator):
    """Session JWT permissions must be registered concrete admin permissions."""
    validator = MagicMock()
    validator.validate_token.return_value = {
        "sub": "admin-user",
        "sid": "postman-session",
        "rbac_permissions": ["does.not.exist"],
    }
    mock_get_validator.return_value = validator

    db = MagicMock(spec=Session)
    binding = RoleBinding(principal_id="admin-user", role_id="admin-role")
    role = Role(id="admin-role", permissions=["*"])

    query_mock = MagicMock()
    db.query.return_value = query_mock
    query_mock.filter.return_value.all.return_value = [binding]
    query_mock.filter.return_value.first.return_value = role

    with pytest.raises(HTTPException) as excinfo:
        run_async(get_rbac_context(authorization="Bearer valid-token", db=db))

    assert excinfo.value.status_code == 403
    assert excinfo.value.detail["error"]["code"] == "RBAC_DENIED"

def test_dashboard_route_requires_auth():
    """Verify that the dashboard root route is protected."""
    client = TestClient(app)
    response = client.get("/")
    # Should be 401 because get_rbac_context (via require_permission) will raise it
    assert response.status_code == 401
    assert response.json()["detail"]["error"]["code"] == "AUTH_MISSING"
@patch("app.middleware.auth_admin.get_admin_validator")
def test_dashboard_route_success(mock_get_validator):
    """Verify that the dashboard root route works with valid auth."""
    # We need to mock the dependencies used in the route and the middleware
    client = TestClient(app)

    validator = MagicMock()
    validator.validate_token.return_value = {"sub": "admin-user"}
    mock_get_validator.return_value = validator

    db = MagicMock(spec=Session)
    
    # Use FastAPI dependency overrides for db and stores
    from app.dependencies import get_db, get_read_db, get_upstream_store, get_model_group_store
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_read_db] = lambda: db
    app.dependency_overrides[get_upstream_store] = lambda: MagicMock()
    app.dependency_overrides[get_model_group_store] = lambda: MagicMock()
    
    binding = RoleBinding(principal_id="admin-user", role_id="admin-role")
    role = Role(id="admin-role", permissions=["llm.read"])
    
    # Mock DB queries
    query_mock = MagicMock()
    db.query.return_value = query_mock
    query_mock.filter.return_value.all.return_value = [binding]
    query_mock.filter.return_value.first.return_value = role
    
    # Also need to mock stores used in dashboard route
    with patch("app.dashboard.router.get_upstream_store") as mock_u_store, \
         patch("app.dashboard.router.get_model_group_store") as mock_mg_store:
        
        mock_u_store.return_value.list_upstreams.return_value = []
        mock_mg_store.return_value.list_model_groups.return_value = []
        
        client = TestClient(app)
        response = client.get("/", headers={"Authorization": "Bearer valid-token"})
        
        assert response.status_code == 200
        assert "Talos AI Gateway" in response.text
