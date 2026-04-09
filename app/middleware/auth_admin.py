"""RBAC Middleware for Admin API."""
from fastapi import Header, HTTPException, Depends
from typing import Optional, Set
from dataclasses import dataclass

from app.dependencies import get_db
from app.adapters.postgres.models import RoleBinding, Role
from sqlalchemy.orm import Session
from app.domain.auth import get_admin_validator
import os

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
        if "*" in self.effective_permissions:
            return True
        return permission in self.effective_permissions


async def get_rbac_context(
    authorization: Optional[str] = Header(None),
    db: Session = Depends(get_db)
) -> RbacContext:
    """Extract RBAC context from JWT. No fallbacks allowed."""
    principal_id = None
    
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
    effective_permissions: Set[str] = set()
    binding_data = []

    try:
        bindings = db.query(RoleBinding).filter(RoleBinding.principal_id == principal_id).all()
        for b in bindings:
            role = db.query(Role).filter(Role.id == b.role_id).first()
            if role:
                effective_permissions.update(role.permissions or [])
            binding_data.append({
                "role_id": b.role_id,
                "scope_type": b.scope_type,
                "scope_org_id": b.scope_org_id,
                "scope_team_id": b.scope_team_id
            })
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
