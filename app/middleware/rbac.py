from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse
import logging
import re
import os
from typing import Optional, Dict
from ..domain.rbac.policy_engine import PolicyEngine
from ..domain.rbac.models import Scope, ScopeType, SurfaceRoute

logger = logging.getLogger(__name__)

class RBACMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, policy_engine: Optional[PolicyEngine] = None):
        super().__init__(app)
        self.is_production = os.getenv("TALOS_ENV") == "production"

    def _match_route(self, registry: list[SurfaceRoute], method: str, path: str) -> Optional[tuple[SurfaceRoute, dict]]:
        """
        Match request against surface registry.
        Returns (Route, extracted_path_params) or None.
        """
        # Simple regex matching for now. In real implementation, use a routing trie.
        for route in registry:
            if route.method != method:
                continue
            
            # Convert /v1/secrets/{secret_id} to regex
            # Escape parts, replace {var} with (?P<var>[^/]+)
            pattern = "^" + re.sub(r'\{([^}]+)\}', r'(?P<\1>[^/]+)', route.path_template) + "$"
            match = re.match(pattern, path)
            
            if match:
                return route, match.groupdict()
        return None

    def _derive_scope(self, route: SurfaceRoute, path_params: dict) -> Scope:
        """Derive request scope from route template and extracted params."""
        tpl = route.scope_template
        attributes = {}
        
        for k, v in tpl.attributes.items():
            # If value is a template variable {var}, extract from path_params
            if v.startswith("{") and v.endswith("}"):
                var_name = v[1:-1]
                val = path_params.get(var_name)
                if val:
                    attributes[k] = val
                else:
                    # Should not happen if regex matched, unless var logic differs
                    attributes[k] = "unknown"
            else:
                # Literal value
                attributes[k] = v
                
        return Scope(scope_type=tpl.scope_type, attributes=attributes)

    async def dispatch(self, request: Request, call_next):
        # 1. Skip if public/health/metrics (Allowlist approach for operational routes)
        if request.url.path in ["/health", "/metrics", "/docs", "/openapi.json"]:
            return await call_next(request)

        # Retrieve state
        if not hasattr(request.app.state, "surface_registry") or not hasattr(request.app.state, "policy_engine"):
            # If not initialized (e.g. unit tests without lifespan), fail safe or warn
            if not self.is_production:
                logger.warning("RBAC not initialized in app.state, skipping enforcement in DEV.")
                # We can return 500 or just pass
                # For verification script, we NEED it to work.
                # If using TestClient with lifespan, it should be there.
                pass
            
            # If we are here and state is missing, we can't enforce.
            # Fail closed in Prod?
            if self.is_production:
                return JSONResponse(status_code=500, content={"message": "Security System Error"})
            
            # Fallback for now if state missing (shouldn't happen with correct setup)
            return await call_next(request)

        registry = request.app.state.surface_registry
        policy_engine = request.app.state.policy_engine

        # 2. Match Route
        match = self._match_route(registry, request.method, request.url.path)
        
        if not match:
            if self.is_production:
                logger.warning(f"RBAC Deny: Unmapped route {request.method} {request.url.path}")
             
            return JSONResponse(
                status_code=403,
                content={"code": "RBAC_SURFACE_UNMAPPED_DENIED", "message": "Access Denied: Route not in Surface Registry"}
            )
            
        route, path_params = match
        
        # 3. Public Route Check
        if route.public:
            return await call_next(request)

        # 4. Identity Extraction (Mocked for now, assumes AuthenticationMiddleware ran before)
        # request.state.user should be populated
        principal_id = getattr(request.state, "user_id", "anonymous")
        
        if principal_id == "anonymous":
             return JSONResponse(
                status_code=401, 
                content={"code": "UNAUTHORIZED", "message": "Authentication required"}
            )

        # 5. Scope Derivation
        request_scope = self._derive_scope(route, path_params)
        
        # 6. Resolve Policy
        decision = await policy_engine.resolve(principal_id, route.permission, request_scope)
        
        # 7. Audit Logging (Mock stub)
        logger.info(f"RBAC Decision: {decision.allowed} Code: {decision.reason_code} Principal: {principal_id}")
        
        if decision.allowed:
            return await call_next(request)
        else:
            return JSONResponse(
                status_code=403,
                content={
                    "code": decision.reason_code,
                    "message": "Access Denied",
                    "request_id": "req_123" # Mock
                }
            )
