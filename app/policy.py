from typing import Protocol, List, Optional, Dict, Any
from dataclasses import dataclass

# Typedefs matching contracts
PrincipalId = str
RoleId = str
Action = str

@dataclass
class AuthorizationResult:
    allowed: bool
    reason: str
    role_id: Optional[str] = None

class PolicyEngine(Protocol):
    def authorize(self, principal: Dict[str, Any], action: str, resource: Dict[str, Any]) -> AuthorizationResult:
        ...

class DeterministicPolicyEngine:
    """
    Implements RBAC Policy Resolution logic (v1).
    Phase 7.1 Locked Logic.
    """

    def __init__(self, roles_db: Dict[str, Dict[str, Any]], bindings_db: Dict[str, List[Dict[str, Any]]]):
        """
        :param roles_db: Map of role_id -> Role Schema Object
        :param bindings_db: Map of principal_id -> List[Binding Schema Object]
        """
        self.roles_db = roles_db
        self.bindings_db = bindings_db

    def authorize(self, principal: Dict[str, Any], action: str, resource: Dict[str, Any]) -> AuthorizationResult:
        principal_id = principal.get("id")
        if not principal_id:
            return AuthorizationResult(False, "Principal has no ID")

        # 1. Fetch Bindings for Principal
        bindings = self.bindings_db.get(principal_id, [])
        if not bindings:
            return AuthorizationResult(False, "No bindings found for principal")

        # 2. Filter Bindings by Scope
        applicable_bindings = [b for b in bindings if self._is_scope_match(b.get("scope", {}), resource)]
        
        if not applicable_bindings:
            return AuthorizationResult(False, "No bindings match resource scope")

        # 3. Check Permissions in Roles
        for binding in applicable_bindings:
            role_id = binding.get("role_id")
            role = self.roles_db.get(role_id)
            if not role:
                continue

            permissions = role.get("permissions", [])
            if self._has_permission(permissions, action):
                return AuthorizationResult(True, "Access Granted", role_id=role_id)

        return AuthorizationResult(False, "No matching permission found in applicable roles")

    def _is_scope_match(self, scope: Dict[str, Any], resource: Dict[str, Any]) -> bool:
        """
        Determines if a binding scope applies to a resource.
        Hierarchy: Platform > Org > Team.
        """
        scope_type = scope.get("type")

        if scope_type == "platform":
            return True
        
        if scope_type == "org":
            return scope.get("org_id") == resource.get("id") or scope.get("org_id") == resource.get("org_id")

        if scope_type == "team":
            # Team scope requires matching org AND team
            # Resource might be the team itself, or an object belonging to the team
            res_team = resource.get("team_id") or (resource.get("id") if resource.get("type") == "team" else None)
            res_org = resource.get("org_id")
            
            return (scope.get("org_id") == res_org) and (scope.get("team_id") == res_team)

        return False

    def _has_permission(self, permissions: List[str], action: str) -> bool:
        """
        Checks if action satisfies any permission string.
        Supports:
        - Exact match: "audit:read" == "audit:read"
        - Global Wildcard: "*:*" matches everything
        - Namespace Wildcard: "audit:*" matches "audit:read", "audit:write"
        """
        for perm in permissions:
            if perm == "*:*":
                return True
            if perm == action:
                return True
            
            # Namespace wildcard check (e.g. "audit:*")
            if perm.endswith(":*"):
                namespace = perm[:-2]
                if action.startswith(namespace + ":"):
                    return True
                    
        return False
