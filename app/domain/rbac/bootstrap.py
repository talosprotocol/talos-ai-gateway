"""Bootstrap RBAC invariants for local admin access."""
from __future__ import annotations

from copy import deepcopy
import os
from typing import Any, Dict, Iterable

from app.domain.interfaces import RbacStore
from app.domain.rbac.models import Binding, BindingEntry, Role, Scope, ScopeType


BOOTSTRAP_RBAC_ROLES = (
    Role(
        role_id="role-admin",
        name="Admin",
        permissions=["*:*"],
        built_in=True,
    ),
    Role(
        role_id="role-public",
        name="Public",
        permissions=["system:health", "system:metrics"],
        built_in=True,
    ),
)

BOOTSTRAP_RBAC_BINDINGS = (
    Binding(
        principal_id="anonymous",
        bindings=[
            BindingEntry(
                binding_id="bind_anon",
                role_id="role-public",
                scope=Scope(scope_type=ScopeType.GLOBAL),
            )
        ],
    ),
    Binding(
        principal_id="dev-user",
        bindings=[
            BindingEntry(
                binding_id="bind_user",
                role_id="role-public",
                scope=Scope(scope_type=ScopeType.GLOBAL),
            )
        ],
    ),
    Binding(
        principal_id="dev-admin",
        bindings=[
            BindingEntry(
                binding_id="bind_admin",
                role_id="role-admin",
                scope=Scope(scope_type=ScopeType.GLOBAL),
            )
        ],
    ),
)

PROTECTED_RBAC_ROLE_IDS = frozenset(role.role_id for role in BOOTSTRAP_RBAC_ROLES)


def protected_rbac_principal_ids() -> frozenset[str]:
    """Return bootstrap principals that must not be orphaned at runtime."""

    return frozenset({"dev-admin", os.getenv("AUTH_ADMIN_PRINCIPAL", "dev-admin")})


def is_protected_rbac_role(role_id: str, role: Dict[str, Any] | None = None) -> bool:
    """Return whether an RBAC role is built in or part of bootstrap auth."""

    return role_id in PROTECTED_RBAC_ROLE_IDS or bool(role and role.get("built_in"))


def is_protected_rbac_principal(principal_id: str) -> bool:
    """Return whether deleting a binding would strand local admin auth."""

    return principal_id in protected_rbac_principal_ids()


def ensure_bootstrap_rbac(rbac_store: RbacStore) -> bool:
    """Restore required built-in roles and bootstrap bindings if they drift."""

    changed = False
    roles_by_id = {
        _role_id(role): role for role in rbac_store.list_roles() if _role_id(role)
    }

    for default_role in BOOTSTRAP_RBAC_ROLES:
        current = roles_by_id.get(default_role.role_id)
        default_data = default_role.model_dump()
        if _role_needs_restore(current, default_data):
            rbac_store.upsert_role(default_data)
            changed = True

    bindings_by_principal = {
        binding.get("principal_id"): binding
        for binding in rbac_store.list_bindings()
        if binding.get("principal_id")
    }
    for default_binding in BOOTSTRAP_RBAC_BINDINGS:
        current = bindings_by_principal.get(default_binding.principal_id)
        default_data = default_binding.model_dump()
        if current is None:
            rbac_store.upsert_binding(default_data)
            changed = True
            continue

        merged = _merge_missing_binding_entries(current, default_data["bindings"])
        if merged is not current:
            rbac_store.upsert_binding(merged)
            changed = True

    return changed


def role_is_referenced(bindings: Iterable[Dict[str, Any]], role_id: str) -> bool:
    """Return whether any persisted binding still references a role."""

    for binding in bindings:
        for entry in binding.get("bindings", []) or []:
            if entry.get("role_id") == role_id:
                return True
    return False


def _role_id(role: Dict[str, Any]) -> str | None:
    return role.get("role_id") or role.get("id")


def _role_needs_restore(
    current: Dict[str, Any] | None,
    default_role: Dict[str, Any],
) -> bool:
    if current is None:
        return True
    return (
        current.get("name") != default_role["name"]
        or current.get("permissions") != default_role["permissions"]
        or current.get("built_in") is not True
    )


def _merge_missing_binding_entries(
    current: Dict[str, Any],
    default_entries: list[Dict[str, Any]],
) -> Dict[str, Any]:
    current_entries = list(current.get("bindings") or [])
    current_role_ids = {entry.get("role_id") for entry in current_entries}
    missing_entries = [
        entry for entry in default_entries if entry.get("role_id") not in current_role_ids
    ]
    if not missing_entries:
        return current

    merged = deepcopy(current)
    merged["bindings"] = current_entries + missing_entries
    return merged
