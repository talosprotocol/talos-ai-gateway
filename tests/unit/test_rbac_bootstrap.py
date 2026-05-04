import asyncio

import pytest
from fastapi import HTTPException

from app.api.admin.router import create_binding, create_role, delete_binding, delete_role
from app.domain.rbac.bootstrap import ensure_bootstrap_rbac
from app.domain.rbac.models import Binding, BindingEntry, Role, Scope, ScopeType
from app.middleware.auth_admin import RbacContext


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


class NoopAuditStore:
    def append_event(self, event):
        pass


def run_async(coro):
    return asyncio.run(coro)


def admin_context():
    return RbacContext(
        principal_id="dev-admin",
        effective_permissions={"platform.admin"},
        bindings=[],
    )


def test_bootstrap_restores_missing_admin_role_when_binding_exists():
    store = FakeRbacStore(
        roles=[],
        bindings=[
            {
                "principal_id": "dev-admin",
                "bindings": [
                    {
                        "binding_id": "bind_admin",
                        "role_id": "role-admin",
                        "scope": {"scope_type": "global", "attributes": {}},
                    }
                ],
            }
        ],
    )

    changed = ensure_bootstrap_rbac(store)

    assert changed is True
    assert store.get_role("role-admin")["permissions"] == ["*:*"]
    assert store.get_role("role-admin")["built_in"] is True
    assert store.get_binding("dev-admin")["bindings"][0]["role_id"] == "role-admin"


def test_bootstrap_merges_missing_admin_binding_without_dropping_existing_entries():
    store = FakeRbacStore(
        roles=[{"role_id": "custom-role", "name": "Custom", "permissions": ["x.y"]}],
        bindings=[
            {
                "principal_id": "dev-admin",
                "bindings": [
                    {
                        "binding_id": "custom",
                        "role_id": "custom-role",
                        "scope": {"scope_type": "global", "attributes": {}},
                    }
                ],
            }
        ],
    )

    ensure_bootstrap_rbac(store)

    role_ids = {
        entry["role_id"] for entry in store.get_binding("dev-admin")["bindings"]
    }
    assert role_ids == {"custom-role", "role-admin"}


def test_delete_protected_role_is_rejected():
    store = FakeRbacStore(
        roles=[
            {
                "role_id": "role-admin",
                "name": "Admin",
                "permissions": ["*:*"],
                "built_in": True,
            }
        ]
    )

    with pytest.raises(HTTPException) as excinfo:
        run_async(
            delete_role(
                "role-admin",
                principal=admin_context(),
                store=store,
                audit_store=NoopAuditStore(),
            )
        )

    assert excinfo.value.status_code == 403
    assert store.get_role("role-admin") is not None


def test_delete_referenced_role_is_rejected():
    store = FakeRbacStore(
        roles=[
            {
                "role_id": "custom-role",
                "name": "Custom",
                "permissions": ["platform.admin"],
                "built_in": False,
            }
        ],
        bindings=[
            {
                "principal_id": "custom-admin",
                "bindings": [
                    {
                        "binding_id": "custom",
                        "role_id": "custom-role",
                        "scope": {"scope_type": "global", "attributes": {}},
                    }
                ],
            }
        ],
    )

    with pytest.raises(HTTPException) as excinfo:
        run_async(
            delete_role(
                "custom-role",
                principal=admin_context(),
                store=store,
                audit_store=NoopAuditStore(),
            )
        )

    assert excinfo.value.status_code == 409
    assert store.get_role("custom-role") is not None


def test_protected_role_and_binding_mutations_are_rejected():
    store = FakeRbacStore()

    with pytest.raises(HTTPException) as role_excinfo:
        run_async(
            create_role(
                Role(
                    role_id="role-admin",
                    name="Mutated",
                    permissions=[],
                    built_in=False,
                ),
                principal=admin_context(),
                store=store,
                audit_store=NoopAuditStore(),
            )
        )
    with pytest.raises(HTTPException) as binding_excinfo:
        run_async(
            create_binding(
                Binding(
                    principal_id="dev-admin",
                    bindings=[
                        BindingEntry(
                            binding_id="bad",
                            role_id="other",
                            scope=Scope(scope_type=ScopeType.GLOBAL),
                        )
                    ],
                ),
                principal=admin_context(),
                store=store,
                audit_store=NoopAuditStore(),
            )
        )
    with pytest.raises(HTTPException) as delete_binding_excinfo:
        run_async(
            delete_binding(
                "dev-admin",
                principal=admin_context(),
                store=store,
                audit_store=NoopAuditStore(),
            )
        )

    assert role_excinfo.value.status_code == 403
    assert binding_excinfo.value.status_code == 403
    assert delete_binding_excinfo.value.status_code == 403
