"""RBAC Middleware for Admin API."""
from fastapi import Header, HTTPException, Depends
from typing import Optional, Set
from dataclasses import dataclass

from app.adapters.postgres.session import get_db
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
    x_talos_principal: Optional[str] = Header(None),
    db: Session = Depends(get_db)
) -> RbacContext:
    """Extract RBAC context from JWT or legacy header."""
    principal_id = None
    dev_mode = os.getenv("MODE", "dev").lower() == "dev" or os.getenv("DEV_MODE", "false").lower() == "true"
    
    # 1. JWT Validation
    if authorization and authorization.startswith("Bearer "):
        token = authorization[7:]
        try:
            validator = get_admin_validator()
            claims = validator.validate_token(token)
            principal_id = claims.get("sub")
        except Exception:
            if not dev_mode:
                raise
    
    # 2. Legacy Fallback (DEV_MODE only)
    if not principal_id and dev_mode:
        principal_id = x_talos_principal or "admin" # Default to admin in dev
        
    if not principal_id:
        raise HTTPException(
            status_code=401, 
            detail={"error": {"code": "AUTH_INVALID", "message": "Missing or invalid authentication"}}
        )

    # 3. RBAC Resolution from DB (Bypass in DEV_MODE if DB unavailable)
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
    except Exception:
        if not dev_mode:
            raise
        # In DEV_MODE, if DB fails, grant admin permissions
        effective_permissions = {"*"}
        binding_data = [{"role_id": "admin", "scope_type": "global"}]
    
    # 4. Final check for permissions
    if not effective_permissions and not dev_mode:
        raise HTTPException(
            status_code=403, 
            detail={"error": {"code": "RBAC_DENIED", "message": f"Principal {principal_id} has no permissions"}}
        )
    
    if not effective_permissions and dev_mode:
        effective_permissions = {"*"}

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
