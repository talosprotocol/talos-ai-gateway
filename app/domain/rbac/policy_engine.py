from typing import List, Dict, Optional, Tuple
import logging
from .models import Role, Binding, BindingEntry, Scope, ScopeType, AuthzDecision

logger = logging.getLogger(__name__)

class PolicyEngine:
    def __init__(self):
        self.roles: Dict[str, Role] = {}
        # Simple in-memory store for bindings (principal_id -> Binding)
        # In production this would be cached from a DB or external service
        self.bindings: Dict[str, Binding] = {}

    async def load_roles(self, roles: List[Role]):
        """Load roles into the engine."""
        for role in roles:
            self.roles[role.role_id] = role
        logger.info(f"Loaded {len(roles)} roles into PolicyEngine")

    async def load_bindings(self, bindings: List[Binding]):
        """Load bindings into the engine."""
        for binding in bindings:
            self.bindings[binding.principal_id] = binding
        logger.info(f"Loaded bindings for {len(bindings)} principals")

    def _match_scope(self, required: Scope, binding_scope: Scope) -> int:
        """
        Calculate specificity score for scope match.
        Returns -1 if no match, or specificity score >= 0.
        
        Specificity Rules:
        0. Global scope bindings match everything (Specificity 0)
        1. Scope Type MUST match (unless binding is global)
        2. Attributes:
           - Exact match: +2
           - Wildcard match (*): +1
           - Missing in binding but present in required: No Match
        """
        # 0. Global binding matches everything
        if binding_scope.scope_type == ScopeType.GLOBAL:
            return 0

        # 1. Type Mismatch
        if required.scope_type != binding_scope.scope_type:
            return -1

        score = 0
        
        # 2. Check attributes
        # Every attribute in the binding scope MUST match the required scope
        # Note: We iterate over binding attributes because the binding defines the constraint.
        # However, typically we check if the binding *covers* the requirement.
        # Actually, for RBAC, the binding extracts a subset of resources.
        # If binding says "repo: talos", and request is "repo: talos", match.
        
        # Let's follow the strict "Binding Covers Request" logic.
        # A specific binding scope (e.g. repo=A) matches a request for repo=A.
        
        # For each attribute in the binding scope:
        for k, v in binding_scope.attributes.items():
            req_val = required.attributes.get(k)
            
            # If binding has an attr that request doesn't have, it's a scope mismatch
            # (e.g. binding is limited to branch=main, but request has no branch)
            # conservatively deny.
            if req_val is None:
                return -1
                
            if v == "*":
                score += 1
            elif v == req_val:
                score += 2
            else:
                # Value mismatch
                return -1
                
        return score

    async def resolve(self, principal_id: str, permission: str, request_scope: Scope) -> AuthzDecision:
        binding_container = self.bindings.get(principal_id)
        
        if not binding_container:
            return AuthzDecision(
                allowed=False,
                reason_code="RBAC_BINDING_NOT_FOUND",
                principal_id=principal_id,
                permission=permission,
                request_scope=request_scope
            )

        matched_roles = []
        matched_bindings = []
        best_score = -1
        effective_role = None
        effective_binding = None

        # Iterate all bindings for this user
        for entry in binding_container.bindings:
            role = self.roles.get(entry.role_id)
            if not role:
                continue

            # 1. Check Permission
            if not self._has_permission(role.permissions, permission):
                continue

            # 2. Check Scope
            score = self._match_scope(request_scope, entry.scope)
            if score >= 0:
                matched_roles.append(role.role_id)
                matched_bindings.append(entry.binding_id)
                
                # Tie-breaking: Higher specificity, then lexicographically smaller binding_id
                is_better = False
                if score > best_score:
                    is_better = True
                elif score == best_score:
                    if effective_binding is None or entry.binding_id < effective_binding:
                        is_better = True
                
                if is_better:
                    best_score = score
                    effective_role = role.role_id
                    effective_binding = entry.binding_id
                    
        if effective_role:
             return AuthzDecision(
                allowed=True,
                reason_code="RBAC_PERMISSION_ALLOWED",
                principal_id=principal_id,
                permission=permission,
                request_scope=request_scope,
                matched_role_ids=matched_roles,
                matched_binding_ids=matched_bindings,
                effective_role_id=effective_role,
                effective_binding_id=effective_binding
            )

        return AuthzDecision(
            allowed=False,
            reason_code="RBAC_PERMISSION_DENIED",
            principal_id=principal_id,
            permission=permission,
            request_scope=request_scope
        )

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
