from __future__ import annotations
from enum import Enum
from typing import List, Dict, Optional, Any
from pydantic import BaseModel, Field

class ScopeType(str, Enum):
    GLOBAL = "global"
    TEAM = "team"
    REPO = "repo"
    PATH = "path"
    SECRET = "secret"
    TRACE = "trace"

class Scope(BaseModel):
    scope_type: ScopeType
    attributes: Dict[str, str] = Field(default_factory=dict)

class Role(BaseModel):
    role_id: str
    name: str
    permissions: List[str]
    built_in: bool = False
    description: Optional[str] = None
    
class BindingEntry(BaseModel):
    binding_id: str
    role_id: str
    scope: Scope

class Binding(BaseModel):
    principal_id: str
    team_id: Optional[str] = None
    bindings: List[BindingEntry] = Field(default_factory=list)

BindingDocument = Binding

class AuthzReasonCode(str, Enum):
    ALLOWED = "RBAC_PERMISSION_ALLOWED"
    DENIED = "RBAC_PERMISSION_DENIED"
    BINDING_NOT_FOUND = "RBAC_BINDING_NOT_FOUND"
    SCOPE_MISMATCH = "RBAC_SCOPE_MISMATCH"

class AuthzDecision(BaseModel):
    allowed: bool
    reason_code: str
    principal_id: str
    permission: str
    request_scope: Scope
    matched_role_ids: List[str] = Field(default_factory=list)
    matched_binding_ids: List[str] = Field(default_factory=list)
    effective_role_id: Optional[str] = None
    effective_binding_id: Optional[str] = None

    def to_audit_dict(self) -> Dict[str, Any]:
        return {
            "authz_decision": "ALLOW" if self.allowed else "DENY",
            "authz_reason_code": self.reason_code,
            "principal_id": self.principal_id,
            "permission": self.permission,
            "scope_type": self.request_scope.scope_type,
            "matched_role_ids": self.matched_role_ids,
            "matched_binding_ids": self.matched_binding_ids,
            "effective_role_id": self.effective_role_id,
            "effective_binding_id": self.effective_binding_id
        }

class SurfaceRoute(BaseModel):
    method: str
    path_template: str
    permission: str
    scope_template: Scope
    public: bool = False
