import time
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp

from app.dependencies import get_rate_limiter

class RateLimitMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: ASGIApp):
        super().__init__(app)
        # Default limits - should be moved to config
        self.auth_limit = "100/60" # 100 req/min for authenticated
        self.anon_limit = "10/60"  # 10 req/min for anonymous

    async def dispatch(self, request: Request, call_next):
        # 1. Skip health checks or metrics if needed (optional)
        if request.url.path.startswith("/health"):
            return await call_next(request)

        # 2. Determine Key
        # Check if authenticated (set by RBAC/Auth middleware running BEFORE this? 
        # Actually usually Auth runs BEFORE. If using FastAPI dependency for Auth, it runs AFTER middleware!
        # This is a common FastAPI "gotcha". Middleware runs before route dependencies.
        # If we rely on request.state.principal, we must ensure Auth Middleware runs before this.
        # OR we perform a lightweight check here, or we use dependency injection rate limiting in routes instead.
        
        # The Implementation Plan said: "Middleware: RateLimitMiddleware".
        # But if we rely on "request.state.principal", we need an Auth Middleware that sets it.
        # Talos uses `AttestationVerifier` which is typically used in dependencies (`get_current_user`).
        # Wait, Phase 7 implemented `RBACMiddleware`. Let's assume RBAC/Auth Middleware runs before.
        # If RBAC middleware does authentication, then `request.state.principal` might be available.
        
        principal = getattr(request.state, "principal", None)
        
        if principal:
            key = f"auth:{principal}"
            limit = self.auth_limit
        else:
            # Fallback to IP
            ip = request.client.host if request.client else "unknown"
            key = f"ip:{ip}"
            limit = self.anon_limit

        # 3. Check Limit
        limiter = await get_rate_limiter()
        try:
            allowed, headers = await limiter.check(key, limit)
        except RuntimeError as e:
            # Handle runtime failures (e.g. Redis down)
            # The lower level already checks MODE for fail-open logic.
            # If we are here, we MUST fail closed (503).
            import os
            mode = os.getenv("MODE", "dev").lower()
            
            error_code = "SERVER_OVERLOADED" if mode == "prod" else "RATE_LIMITER_UNAVAILABLE"
            detail = "rate_limiter_unavailable" # Same detail for both per prod spec requirements
            
            return JSONResponse(
                status_code=503,
                content={
                    "error": error_code,
                    "detail": detail
                },
                headers={"Retry-After": "30"} # Suggest retry
            )
        
        if not allowed:
            return JSONResponse(
                status_code=429,
                content={
                    "error": "RATE_LIMITED",
                    "message": "Too many requests"
                },
                headers=headers
            )
            
        # 4. Process Request
        response = await call_next(request)
        
        # 5. Add Headers (optional, but good practice)
        for k, v in headers.items():
            response.headers[k] = v
            
        return response
