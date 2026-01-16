"""RBAC Middleware per Phase 7 LOCKED SPEC.

Deny-by-default enforcement with surface registry lookup.
"""
import json
import logging
import re
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from app.domain.rbac.models import (
    Scope,
    ScopeType,
    AuthzDecision,
    AuthzReasonCode,
)
from app.domain.rbac.policy_engine import PolicyEngine, get_policy_engine

logger = logging.getLogger(__name__)


class SurfaceEntry:
    """A surface registry entry."""
    
    def __init__(self, data: Dict[str, Any]):
        self.method = data["method"]
        self.path_template = data["path_template"]
        self.permission = data["permission"]
        self.scope_template = data["scope_template"]
        self.anonymous_allowed = data.get("anonymous_allowed", False)
        
        # Compile path pattern
        pattern = re.sub(r"\{(\w+)\}", r"(?P<\1>[^/]+)", self.path_template)
        self._pattern = re.compile(f"^{pattern}$")
    
    def match(self, method: str, path: str) -> Optional[Dict[str, str]]:
        """Match request and return path params if matched."""
        if method.upper() != self.method:
            return None
        match = self._pattern.match(path)
        if match:
            return match.groupdict()
        return None
    
    def derive_scope(self, path_params: Dict[str, str]) -> Scope:
        """Derive request scope from path params and template."""
        scope_type = ScopeType(self.scope_template["scope_type"])
        attributes = {}
        
        for key, value_template in self.scope_template.get("attributes", {}).items():
            if value_template.startswith("{") and value_template.endswith("}"):
                param_name = value_template[1:-1]
                if param_name in path_params:
                    attributes[key] = path_params[param_name]
                else:
                    raise ValueError(f"Missing path param: {param_name}")
            else:
                attributes[key] = value_template
        
        return Scope(scope_type, attributes)


class SurfaceRegistry:
    """
    Surface registry for route-to-permission mapping.
    
    Per LOCKED SPEC:
    - All routes MUST have a registry entry
    - Unmapped routes denied with RBAC_SURFACE_UNMAPPED_DENIED
    """
    
    def __init__(self, entries: list[SurfaceEntry] = None):
        self._entries = entries or []
    
    @classmethod
    def load(cls, registry_path: Path) -> "SurfaceRegistry":
        """Load registry from JSON file."""
        with open(registry_path) as f:
            data = json.load(f)
        entries = [SurfaceEntry(e) for e in data.get("routes", [])]
        return cls(entries)
    
    def lookup(self, method: str, path: str) -> Optional[tuple[SurfaceEntry, Dict[str, str]]]:
        """Look up entry for request. Returns (entry, path_params) or None."""
        for entry in self._entries:
            params = entry.match(method, path)
            if params is not None:
                return (entry, params)
        return None


class RbacMiddleware(BaseHTTPMiddleware):
    """
    RBAC enforcement middleware per Phase 7 LOCKED SPEC.
    
    Behavior:
    1. Look up surface registry entry for route
    2. If no entry: deny with RBAC_SURFACE_UNMAPPED_DENIED (prod) or log (dev)
    3. Extract principal from request
    4. Derive scope from path params
    5. Call PolicyEngine.resolve()
    6. On deny: return 403 with stable error code
    7. Emit audit fields
    """
    
    def __init__(
        self, 
        app, 
        policy_engine: Optional[PolicyEngine] = None,
        surface_registry: Optional[SurfaceRegistry] = None,
        deny_unmapped: bool = True  # True in production
    ):
        super().__init__(app)
        self._engine = policy_engine or get_policy_engine()
        self._registry = surface_registry or SurfaceRegistry()
        self._deny_unmapped = deny_unmapped
    
    async def dispatch(self, request: Request, call_next: Callable):
        path = request.url.path
        method = request.method
        
        # Skip health/metrics
        if path in ("/health", "/metrics", "/"):
            return await call_next(request)
        
        # Look up surface entry
        lookup_result = self._registry.lookup(method, path)
        
        if lookup_result is None:
            if self._deny_unmapped:
                logger.warning(f"Unmapped surface: {method} {path}")
                return self._deny_response(
                    AuthzReasonCode.SURFACE_UNMAPPED,
                    "No surface registry entry for this route"
                )
            else:
                # Development mode: allow but log
                logger.warning(f"Unmapped surface (dev mode): {method} {path}")
                return await call_next(request)
        
        entry, path_params = lookup_result
        
        # Check anonymous
        principal_id = self._get_principal_id(request)
        if principal_id is None:
            if not entry.anonymous_allowed:
                return self._deny_response(
                    AuthzReasonCode.BINDING_NOT_FOUND,
                    "Authentication required"
                )
            return await call_next(request)
        
        # Derive scope
        try:
            request_scope = entry.derive_scope(path_params)
        except ValueError as e:
            logger.error(f"Scope derivation failed: {e}")
            return self._deny_response(
                AuthzReasonCode.POLICY_ERROR,
                f"Invalid scope: {e}"
            )
        
        # Resolve authorization
        decision = await self._engine.resolve(
            principal_id=principal_id,
            permission=entry.permission,
            request_scope=request_scope
        )
        
        # Store decision for audit
        request.state.authz_decision = decision.to_audit_dict()
        
        if not decision.allowed:
            return self._deny_response(
                decision.reason_code,
                decision.reason_code.value
            )
        
        return await call_next(request)
    
    def _get_principal_id(self, request: Request) -> Optional[str]:
        """Extract principal ID from request."""
        # Check state (set by auth middleware)
        if hasattr(request.state, "principal_id"):
            return request.state.principal_id
        
        # Fallback to header
        return request.headers.get("X-Principal-Id")
    
    def _deny_response(self, reason: AuthzReasonCode, message: str) -> JSONResponse:
        """Return 403 with stable error code."""
        return JSONResponse(
            status_code=403,
            content={
                "error": reason.value,
                "message": message
            }
        )


# Factory for creating middleware
def create_rbac_middleware(
    app,
    policy_engine: Optional[PolicyEngine] = None,
    registry_path: Optional[Path] = None,
    deny_unmapped: bool = True
) -> RbacMiddleware:
    """Create RBAC middleware with optional registry file."""
    registry = None
    if registry_path and registry_path.exists():
        registry = SurfaceRegistry.load(registry_path)
    return RbacMiddleware(
        app,
        policy_engine=policy_engine,
        surface_registry=registry,
        deny_unmapped=deny_unmapped
    )
