"""PolicyEngine per Phase 7 LOCKED SPEC.

Deterministic RBAC resolution with scope matching.
"""
import logging
from typing import Dict, List, Optional

from app.domain.rbac.models import (
    Role,
    Binding,
    BindingDocument,
    Scope,
    ScopeType,
    ScopeMatchResult,
    AuthzDecision,
    AuthzReasonCode,
)

logger = logging.getLogger(__name__)


class PolicyEngine:
    """
    Deterministic RBAC PolicyEngine per Phase 7 LOCKED SPEC.
    
    Resolution algorithm:
    1. Load roles and principal bindings
    2. For each binding, check role exists and has permission
    3. Match scope using normative semantics
    4. Choose winner by specificity then binding_id tie-break
    """
    
    def __init__(
        self, 
        roles: Optional[Dict[str, Role]] = None,
        binding_docs: Optional[Dict[str, BindingDocument]] = None
    ):
        """Initialize with in-memory stores (for testing)."""
        self._roles = roles or {}
        self._binding_docs = binding_docs or {}
    
    async def load_roles(self) -> Dict[str, Role]:
        """Load all roles."""
        return self._roles
    
    async def load_bindings(self, principal_id: str) -> List[Binding]:
        """Load bindings for a principal."""
        doc = self._binding_docs.get(principal_id)
        if doc:
            return doc.bindings
        return []
    
    async def resolve(
        self, 
        principal_id: str, 
        permission: str, 
        request_scope: Scope
    ) -> AuthzDecision:
        """
        Resolve authorization decision.
        
        Per LOCKED SPEC:
        - Bindings evaluated in deterministic order (sorted by binding_id)
        - Winner chosen by specificity then binding_id tie-break
        - Stable reason codes for all outcomes
        """
        try:
            bindings = await self.load_bindings(principal_id)
            roles = await self.load_roles()
            
            if not bindings:
                return AuthzDecision(
                    allowed=False,
                    reason_code=AuthzReasonCode.BINDING_NOT_FOUND,
                    principal_id=principal_id,
                    permission=permission,
                    request_scope=request_scope
                )
            
            # Sort bindings by binding_id for deterministic evaluation
            sorted_bindings = sorted(bindings, key=lambda b: b.binding_id)
            
            # Find all qualifying bindings
            candidates: List[tuple[Binding, Role, int]] = []
            
            for binding in sorted_bindings:
                # Check role exists
                role = roles.get(binding.role_id)
                if not role:
                    logger.warning(f"Role {binding.role_id} not found for binding {binding.binding_id}")
                    continue
                
                # Check role has permission
                if not role.has_permission(permission):
                    continue
                
                # Check scope matches
                match_result = self._match_scope(binding.scope, request_scope)
                if not match_result.matched:
                    continue
                
                candidates.append((binding, role, match_result.specificity))
            
            if not candidates:
                # Determine most specific reason
                # Check if any binding had role not found
                has_roles = any(roles.get(b.role_id) for b in sorted_bindings)
                if not has_roles:
                    return AuthzDecision(
                        allowed=False,
                        reason_code=AuthzReasonCode.ROLE_NOT_FOUND,
                        principal_id=principal_id,
                        permission=permission,
                        request_scope=request_scope
                    )
                
                # Check if permission not in any role
                has_permission = any(
                    roles.get(b.role_id) and roles.get(b.role_id).has_permission(permission)
                    for b in sorted_bindings
                )
                if not has_permission:
                    return AuthzDecision(
                        allowed=False,
                        reason_code=AuthzReasonCode.PERMISSION_DENIED,
                        principal_id=principal_id,
                        permission=permission,
                        request_scope=request_scope
                    )
                
                # Otherwise scope mismatch
                return AuthzDecision(
                    allowed=False,
                    reason_code=AuthzReasonCode.SCOPE_MISMATCH,
                    principal_id=principal_id,
                    permission=permission,
                    request_scope=request_scope
                )
            
            # Choose winner: highest specificity, then lexicographic binding_id
            candidates.sort(key=lambda x: (-x[2], x[0].binding_id))
            winner_binding, winner_role, _ = candidates[0]
            
            return AuthzDecision(
                allowed=True,
                reason_code=AuthzReasonCode.PERMISSION_ALLOWED,
                principal_id=principal_id,
                permission=permission,
                request_scope=request_scope,
                matched_role_ids=[c[1].role_id for c in candidates],
                matched_binding_ids=[c[0].binding_id for c in candidates],
                effective_role_id=winner_role.role_id,
                effective_binding_id=winner_binding.binding_id
            )
            
        except Exception as e:
            logger.exception(f"Policy error for {principal_id}: {e}")
            return AuthzDecision(
                allowed=False,
                reason_code=AuthzReasonCode.POLICY_ERROR,
                principal_id=principal_id,
                permission=permission,
                request_scope=request_scope
            )
    
    def _match_scope(self, binding_scope: Scope, request_scope: Scope) -> ScopeMatchResult:
        """
        Match binding scope against request scope.
        
        Per LOCKED SPEC:
        - global matches any scope (specificity 0)
        - scope_type must equal (no cross-type matching)
        - Exact match: +2 specificity per attribute
        - Wildcard "*": +1 specificity per attribute
        """
        # Global matches anything
        if binding_scope.scope_type == ScopeType.GLOBAL:
            return ScopeMatchResult(matched=True, specificity=0)
        
        # scope_type must match
        if binding_scope.scope_type != request_scope.scope_type:
            return ScopeMatchResult(
                matched=False, 
                specificity=0,
                reason="scope_type_mismatch"
            )
        
        # Check all binding attributes match request
        specificity = 0
        for key, binding_value in binding_scope.attributes.items():
            request_value = request_scope.attributes.get(key)
            
            if request_value is None:
                return ScopeMatchResult(
                    matched=False,
                    specificity=0,
                    reason="missing_attribute"
                )
            
            if binding_value == "*":
                specificity += 1  # Wildcard match
            elif binding_value == request_value:
                specificity += 2  # Exact match
            else:
                return ScopeMatchResult(
                    matched=False,
                    specificity=0,
                    reason="attribute_mismatch"
                )
        
        return ScopeMatchResult(matched=True, specificity=specificity)


# Singleton for gateway
_policy_engine: Optional[PolicyEngine] = None


def get_policy_engine() -> PolicyEngine:
    """Get or create PolicyEngine singleton."""
    global _policy_engine
    if _policy_engine is None:
        _policy_engine = PolicyEngine()
    return _policy_engine
