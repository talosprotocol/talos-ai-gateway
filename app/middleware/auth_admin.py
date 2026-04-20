"""RBAC Middleware for Admin API."""
from fastapi import Header, HTTPException, Depends
from typing import Optional, Set, Tuple, List
from dataclasses import dataclass

from app.dependencies import get_db
from app.adapters.postgres.models import RoleBinding, Role
from sqlalchemy.orm import Session
from app.domain.auth import get_admin_validator
import os

REGISTERED_ADMIN_PERMISSIONS = {
    "audit.read",
    "keys.read",
    "keys.write",
    "llm.admin",
    "llm.read",
    "mcp.admin",
    "mcp.read",
    "platform.admin",
    "resource.delete",
}

REGISTERED_PUBLIC_SCOPES = {
    "a2a.cancel",
    "a2a.discovery.read",
    "a2a.frame.receive",
    "a2a.frame.send",
    "a2a.get",
    "a2a.group.close",
    "a2a.group.create",
    "a2a.group.manage",
    "a2a.group.read",
    "a2a.invoke",
    "a2a.list",
    "a2a.push_config.read",
    "a2a.push_config.write",
    "a2a.send",
    "a2a.session.accept",
    "a2a.session.close",
    "a2a.session.create",
    "a2a.session.read",
    "a2a.session.rotate",
    "a2a.subscribe",
    "llm.invoke",
    "llm.read",
    "mcp.invoke",
    "mcp.read",
}

REGISTERED_SESSION_PERMISSIONS = REGISTERED_ADMIN_PERMISSIONS | REGISTERED_PUBLIC_SCOPES

@dataclass
class RbacContext:
    """RBAC context for admin requests."""
    principal_id: str
    effective_permissions: Set[str]
    bindings: list

    @property
    def id(self) -> str:
        """Alias for principal_id to support legacy code."""
        return self.principal_id

    def has_permission(self, permission: str, scope: dict = None) -> bool:
        """Check if principal has permission at scope."""
        return has_permission(self.effective_permissions, permission)


def has_permission(permissions: Set[str], permission: str) -> bool:
    """Check permission strings with exact and wildcard semantics."""
    for granted in permissions:
        if granted in ("*", "*:*"):
            return True
        if granted == permission:
            return True
        if granted.endswith(".*") and permission.startswith(f"{granted[:-2]}."):
            return True
        if granted.endswith(":*") and permission.startswith(f"{granted[:-2]}:"):
            return True
    return False


def resolve_principal_permissions(db: Session, principal_id: str) -> Tuple[Set[str], list]:
    """Resolve DB-granted RBAC permissions and binding metadata."""
    effective_permissions: Set[str] = set()
    binding_data = []

    bindings = db.query(RoleBinding).filter(RoleBinding.principal_id == principal_id).all()
    for binding in bindings:
        role = db.query(Role).filter(Role.id == binding.role_id).first()
        if role:
            effective_permissions.update(role.permissions or [])
        binding_data.append({
            "role_id": binding.role_id,
            "scope_type": binding.scope_type,
            "scope_org_id": binding.scope_org_id,
            "scope_team_id": binding.scope_team_id
        })

    return effective_permissions, binding_data


def validate_session_permissions(
    requested_permissions: List[str],
    granted_permissions: Set[str],
    registered_permissions: Set[str] = REGISTERED_ADMIN_PERMISSIONS,
) -> Set[str]:
    """Validate session-scoped RBAC permissions against DB-granted RBAC."""
    if not requested_permissions:
        raise HTTPException(
            status_code=403,
            detail={"error": {"code": "RBAC_DENIED", "message": "Session token has no RBAC permissions"}}
        )

    requested = set(requested_permissions)
    unregistered = sorted(permission for permission in requested if permission not in registered_permissions)
    if unregistered:
        raise HTTPException(
            status_code=403,
            detail={"error": {"code": "RBAC_DENIED", "message": f"Session token requested unregistered permissions: {unregistered}"}}
        )

    denied = sorted(
        permission
        for permission in requested
        if not has_permission(granted_permissions, permission)
    )
    if denied:
        raise HTTPException(
            status_code=403,
            detail={"error": {"code": "RBAC_DENIED", "message": f"Session token requested ungranted permissions: {denied}"}}
        )

    return requested


def validate_registered_session_permissions(requested_permissions: List[str]) -> Set[str]:
    """Reject unknown or wildcard session permissions before minting/accepting JWTs."""
    requested = set(requested_permissions)
    unregistered = sorted(permission for permission in requested if permission not in REGISTERED_SESSION_PERMISSIONS)
    if unregistered:
        raise HTTPException(
            status_code=403,
            detail={"error": {"code": "RBAC_DENIED", "message": f"Session token requested unregistered permissions: {unregistered}"}}
        )
    return requested


async def get_rbac_context(
    authorization: Optional[str] = Header(None),
    db: Session = Depends(get_db)
) -> RbacContext:
    """Extract RBAC context from JWT. No fallbacks allowed."""
    principal_id = None
    claims = {}
    
    # 1. JWT Validation (Mandatory)
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=401, 
            detail={"error": {"code": "AUTH_MISSING", "message": "Missing or invalid Authorization header"}}
        )

    token = authorization[7:]
    try:
        validator = get_admin_validator()
        claims = validator.validate_token(token)
        principal_id = claims.get("sub")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=401, 
            detail={"error": {"code": "AUTH_INVALID", "message": f"Token validation failed: {str(e)}"}}
        )
    
    if not principal_id:
        raise HTTPException(
            status_code=401, 
            detail={"error": {"code": "AUTH_INVALID", "message": "Principal (sub) not found in token"}}
        )

    # 2. RBAC Resolution from DB (Mandatory)
    try:
        effective_permissions, binding_data = resolve_principal_permissions(db, principal_id)
    except Exception as e:
        raise HTTPException(
            status_code=500, 
            detail={"error": {"code": "RBAC_ERROR", "message": f"Failed to resolve RBAC: {str(e)}"}}
        )
    
    # 3. Final check for permissions
    if not effective_permissions:
        raise HTTPException(
            status_code=403, 
            detail={"error": {"code": "RBAC_DENIED", "message": f"Principal {principal_id} has no permissions assigned"}}
        )

    session_permissions = claims.get("rbac_permissions")
    if session_permissions is not None:
        if not isinstance(session_permissions, list) or not all(isinstance(p, str) for p in session_permissions):
            raise HTTPException(
                status_code=401,
                detail={"error": {"code": "AUTH_INVALID", "message": "rbac_permissions must be a list of strings"}}
            )
        validate_registered_session_permissions(session_permissions)
        admin_session_permissions = [
            permission
            for permission in session_permissions
            if permission in REGISTERED_ADMIN_PERMISSIONS
        ]
        effective_permissions = validate_session_permissions(admin_session_permissions, effective_permissions)

    return RbacContext(
        principal_id=principal_id,
        effective_permissions=effective_permissions,
        bindings=binding_data
    )


def require_permission(permission: str):
    """Dependency that requires a specific RBAC permission."""
    async def checker(rbac: RbacContext = Depends(get_rbac_context)):
        if not rbac.has_permission(permission):
            raise HTTPException(status_code=403, detail={"error": {"code": "RBAC_DENIED", "message": f"Missing permission: {permission}"}})
        return rbac
    return checker
