"""RBAC Middleware for Admin API."""
from fastapi import Header, HTTPException, Depends
from typing import Optional, Set
from dataclasses import dataclass

# MVP: Mock RBAC data - will be replaced with Postgres
MOCK_ROLES = {
    "PlatformAdmin": {"permissions": ["*"]},
    "PlatformViewer": {"permissions": ["org.read", "team.read", "mcp.read", "llm.read", "audit.read"]},
    "OrgAdmin": {"permissions": ["org.read", "org.write", "team.read", "team.write", "mcp.admin", "llm.admin", "keys.write"]},
    "TeamAdmin": {"permissions": ["team.read", "mcp.admin", "keys.write", "policies.write"]},
    "TeamViewer": {"permissions": ["team.read", "mcp.read", "llm.read"]},
}

MOCK_BINDINGS = {
    # principal_id -> list of bindings
    "admin@talos.io": [{"role_id": "PlatformAdmin", "scope": {"type": "platform"}}],
    "viewer@talos.io": [{"role_id": "PlatformViewer", "scope": {"type": "platform"}}],
}


@dataclass
class RbacContext:
    """RBAC context for admin requests."""
    principal_id: str
    effective_permissions: Set[str]
    bindings: list

    def has_permission(self, permission: str, scope: dict = None) -> bool:
        """Check if principal has permission at scope."""
        if "*" in self.effective_permissions:
            return True
        return permission in self.effective_permissions


async def get_rbac_context(x_talos_principal: Optional[str] = Header(None)) -> RbacContext:
    """Extract RBAC context from request headers.
    
    In production, this would validate OIDC token or service account token.
    For MVP, we use a header-based mock.
    """
    if not x_talos_principal:
        raise HTTPException(status_code=401, detail={"error": {"code": "AUTH_INVALID", "message": "Missing X-Talos-Principal header"}})
    
    bindings = MOCK_BINDINGS.get(x_talos_principal, [])
    if not bindings:
        raise HTTPException(status_code=401, detail={"error": {"code": "AUTH_INVALID", "message": "Unknown principal"}})
    
    # Compute effective permissions from bindings
    effective_permissions: Set[str] = set()
    for binding in bindings:
        role = MOCK_ROLES.get(binding["role_id"], {})
        effective_permissions.update(role.get("permissions", []))
    
    return RbacContext(
        principal_id=x_talos_principal,
        effective_permissions=effective_permissions,
        bindings=bindings
    )


def require_permission(permission: str):
    """Dependency that requires a specific RBAC permission."""
    async def checker(rbac: RbacContext = Depends(get_rbac_context)):
        if not rbac.has_permission(permission):
            raise HTTPException(status_code=403, detail={"error": {"code": "RBAC_DENIED", "message": f"Missing permission: {permission}"}})
        return rbac
    return checker
