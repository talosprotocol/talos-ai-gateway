import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from unittest.mock import MagicMock, patch
from app.middleware.auth_admin import get_rbac_context
from app.main import app
import asyncio

# To run async functions in tests
def run_async(coro):
    return asyncio.run(coro)


class FakeRbacStore:
    def __init__(self, roles=None, bindings=None):
        self.roles = {role["role_id"]: role for role in roles or []}
        self.bindings = {
            binding["principal_id"]: binding for binding in bindings or []
        }

    def list_roles(self):
        return list(self.roles.values())

    def get_role(self, role_id):
        return self.roles.get(role_id)

    def upsert_role(self, role):
        self.roles[role["role_id"]] = role

    def delete_role(self, role_id):
        self.roles.pop(role_id, None)

    def list_bindings(self):
        return list(self.bindings.values())

    def get_binding(self, principal_id):
        return self.bindings.get(principal_id)

    def upsert_binding(self, binding):
        self.bindings[binding["principal_id"]] = binding

    def delete_binding(self, principal_id):
        self.bindings.pop(principal_id, None)


def rbac_store_for(principal_id, role_id, permissions):
    return FakeRbacStore(
        roles=[
            {
                "role_id": role_id,
                "name": role_id,
                "permissions": permissions,
                "built_in": False,
            }
        ],
        bindings=[
            {
                "principal_id": principal_id,
                "bindings": [
                    {
                        "binding_id": f"bind-{principal_id}",
                        "role_id": role_id,
                        "scope": {"scope_type": "global", "attributes": {}},
                    }
                ],
            }
        ],
    )


def test_get_rbac_context_no_auth():
    """Verify that missing Authorization header raises 401."""
    with pytest.raises(HTTPException) as excinfo:
        run_async(get_rbac_context(authorization=None, rbac_store=FakeRbacStore()))
    assert excinfo.value.status_code == 401
    assert excinfo.value.detail["error"]["code"] == "AUTH_MISSING"

def test_get_rbac_context_invalid_header():
    """Verify that non-Bearer Authorization header raises 401."""
    with pytest.raises(HTTPException) as excinfo:
        run_async(
            get_rbac_context(
                authorization="Basic dXNlcjpwYXNz",
                rbac_store=FakeRbacStore(),
            )
        )
    assert excinfo.value.status_code == 401
    assert excinfo.value.detail["error"]["code"] == "AUTH_MISSING"

@patch("app.middleware.auth_admin.get_admin_validator")
def test_get_rbac_context_invalid_token(mock_get_validator):
    """Verify that invalid token raises 401."""
    validator = MagicMock()
    validator.validate_token.side_effect = Exception("Invalid token")
    mock_get_validator.return_value = validator
    
    with pytest.raises(HTTPException) as excinfo:
        run_async(
            get_rbac_context(
                authorization="Bearer invalid-token",
                rbac_store=FakeRbacStore(),
            )
        )
    assert excinfo.value.status_code == 401
    assert excinfo.value.detail["error"]["code"] == "AUTH_INVALID"

@patch("app.middleware.auth_admin.get_admin_validator")
def test_get_rbac_context_no_permissions(mock_get_validator):
    """Verify that valid token but no RBAC permissions raises 403."""
    validator = MagicMock()
    validator.validate_token.return_value = {"sub": "user-without-perms"}
    mock_get_validator.return_value = validator
    
    with pytest.raises(HTTPException) as excinfo:
        run_async(
            get_rbac_context(
                authorization="Bearer valid-token",
                rbac_store=FakeRbacStore(),
            )
        )
    assert excinfo.value.status_code == 403
    assert excinfo.value.detail["error"]["code"] == "RBAC_DENIED"

@patch("app.middleware.auth_admin.get_admin_validator")
def test_get_rbac_context_success(mock_get_validator):
    """Verify that valid token and permissions returns RbacContext."""
    validator = MagicMock()
    validator.validate_token.return_value = {"sub": "admin-user"}
    mock_get_validator.return_value = validator
    
    context = run_async(
        get_rbac_context(
            authorization="Bearer valid-token",
            rbac_store=rbac_store_for(
                "admin-user",
                "admin-role",
                ["llm.read", "llm.admin"],
            ),
        )
    )
    
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

    context = run_async(
        get_rbac_context(
            authorization="Bearer valid-token",
            rbac_store=rbac_store_for(
                "admin-user",
                "admin-role",
                ["llm.read", "llm.admin"],
            ),
        )
    )

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

    with pytest.raises(HTTPException) as excinfo:
        run_async(
            get_rbac_context(
                authorization="Bearer valid-token",
                rbac_store=rbac_store_for("admin-user", "viewer-role", ["llm.read"]),
            )
        )

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

    with pytest.raises(HTTPException) as excinfo:
        run_async(
            get_rbac_context(
                authorization="Bearer valid-token",
                rbac_store=rbac_store_for("admin-user", "admin-role", ["*"]),
            )
        )

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

    upstream_store = MagicMock()
    upstream_store.list_upstreams.return_value = []
    model_group_store = MagicMock()
    model_group_store.list_model_groups.return_value = []

    from app.dependencies import get_model_group_store, get_rbac_store, get_upstream_store

    app.dependency_overrides[get_rbac_store] = lambda: rbac_store_for(
        "admin-user",
        "admin-role",
        ["llm.read"],
    )
    app.dependency_overrides[get_upstream_store] = lambda: upstream_store
    app.dependency_overrides[get_model_group_store] = lambda: model_group_store

    # Also need to mock stores used in dashboard route
    with patch("app.dashboard.router.get_upstream_store") as mock_u_store, \
         patch("app.dashboard.router.get_model_group_store") as mock_mg_store:
        
        mock_u_store.return_value.list_upstreams.return_value = []
        mock_mg_store.return_value.list_model_groups.return_value = []
        
        client = TestClient(app)
        response = client.get("/", headers={"Authorization": "Bearer valid-token"})
        
        assert response.status_code == 200
        assert "Talos AI Gateway" in response.text

    app.dependency_overrides.clear()
