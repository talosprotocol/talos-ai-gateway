"""RBAC models per Phase 7 LOCKED SPEC.

Normative types: Role, Binding, Scope, AuthzDecision.
"""
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional


class AuthzReasonCode(str, Enum):
    """Stable RBAC reason codes."""
    PERMISSION_ALLOWED = "RBAC_PERMISSION_ALLOWED"
    PERMISSION_DENIED = "RBAC_PERMISSION_DENIED"
    SCOPE_MISMATCH = "RBAC_SCOPE_MISMATCH"
    BINDING_NOT_FOUND = "RBAC_BINDING_NOT_FOUND"
    ROLE_NOT_FOUND = "RBAC_ROLE_NOT_FOUND"
    POLICY_ERROR = "RBAC_POLICY_ERROR"
    SURFACE_UNMAPPED = "RBAC_SURFACE_UNMAPPED_DENIED"


class ScopeType(str, Enum):
    """Normative scope types per LOCKED SPEC."""
    GLOBAL = "global"
    TEAM = "team"
    REPO = "repo"
    PATH = "path"
    SECRET = "secret"
    TRACE = "trace"


@dataclass
class Scope:
    """Normative scope with type and attributes."""
    scope_type: ScopeType
    attributes: Dict[str, str] = field(default_factory=dict)
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Scope":
        return cls(
            scope_type=ScopeType(data["scope_type"]),
            attributes=data.get("attributes", {})
        )
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "scope_type": self.scope_type.value,
            "attributes": self.attributes
        }


@dataclass
class Role:
    """RBAC role with permissions."""
    role_id: str
    permissions: List[str]
    name: Optional[str] = None
    built_in: bool = False
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Role":
        return cls(
            role_id=data["role_id"],
            permissions=data["permissions"],
            name=data.get("name"),
            built_in=data.get("built_in", False)
        )
    
    def has_permission(self, permission: str) -> bool:
        """Check if role grants a permission (supports wildcards)."""
        for p in self.permissions:
            if p == "*:*":
                return True
            if p == permission:
                return True
            # Check namespace wildcard (e.g., secrets:* matches secrets:read)
            if p.endswith(":*"):
                ns = p[:-2]
                if permission.startswith(f"{ns}:"):
                    return True
        return False


@dataclass
class Binding:
    """Role binding with scope."""
    binding_id: str
    role_id: str
    scope: Scope
    created_at: Optional[str] = None
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Binding":
        return cls(
            binding_id=data["binding_id"],
            role_id=data["role_id"],
            scope=Scope.from_dict(data["scope"]),
            created_at=data.get("created_at")
        )


@dataclass
class BindingDocument:
    """Per-principal binding document."""
    principal_id: str
    bindings: List[Binding]
    team_id: Optional[str] = None
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "BindingDocument":
        return cls(
            principal_id=data["principal_id"],
            team_id=data.get("team_id"),
            bindings=[Binding.from_dict(b) for b in data.get("bindings", [])]
        )


@dataclass
class ScopeMatchResult:
    """Result of scope matching."""
    matched: bool
    specificity: int = 0
    reason: Optional[str] = None


@dataclass
class AuthzDecision:
    """Normative authorization decision."""
    allowed: bool
    reason_code: AuthzReasonCode
    principal_id: str
    permission: str
    request_scope: Scope
    matched_role_ids: List[str] = field(default_factory=list)
    matched_binding_ids: List[str] = field(default_factory=list)
    effective_role_id: Optional[str] = None
    effective_binding_id: Optional[str] = None
    ts: str = field(default_factory=lambda: datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z")
    
    def to_audit_dict(self) -> Dict[str, Any]:
        """Convert to normalized audit fields."""
        result = {
            "authz_decision": "ALLOW" if self.allowed else "DENY",
            "authz_reason_code": self.reason_code.value,
            "permission": self.permission,
            "scope_type": self.request_scope.scope_type.value,
            "scope_attributes": self.request_scope.attributes
        }
        if self.allowed:
            result["matched_role_ids"] = self.matched_role_ids
            result["matched_binding_ids"] = self.matched_binding_ids
        return result
