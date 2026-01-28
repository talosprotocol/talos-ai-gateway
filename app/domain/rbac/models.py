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

class SurfaceRoute(BaseModel):
    method: str
    path_template: str
    permission: str
    scope_template: Scope
    public: bool = False
