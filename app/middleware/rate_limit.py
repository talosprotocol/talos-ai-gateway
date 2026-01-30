import time
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp

from app.dependencies import get_rate_limiter

class RateLimitMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: ASGIApp):
        super().__init__(app)
        # Default limits from Env (Locked Spec)
        import os
        self.default_rps = int(os.getenv("RATE_LIMIT_DEFAULT_RPS", "5"))
        self.default_burst = int(os.getenv("RATE_LIMIT_DEFAULT_BURST", "10"))

    async def dispatch(self, request: Request, call_next):
        import os
        if os.getenv("RATE_LIMIT_ENABLED", "true").lower() == "false":
            return await call_next(request)

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
        
        
        # 2. Determine Limits (Look up Surface Registry overrides)
        surface = getattr(request.state, "surface", None)
        rps = self.default_rps
        burst = self.default_burst
        
        if surface and hasattr(surface, "rate_limit_rps") and surface.rate_limit_rps:
            rps = surface.rate_limit_rps
        if surface and hasattr(surface, "rate_limit_burst") and surface.rate_limit_burst:
            burst = surface.rate_limit_burst
        
        principal = getattr(request.state, "principal", None)
        
        if principal:
            key = f"auth:{principal}"
            # Logic to fetch per-user override could be here
        else:
            # Fallback to IP (Hashed per Locked Spec)
            import hashlib
            ip = request.client.host if request.client else "unknown"
            ip_hash = hashlib.sha256(ip.encode()).hexdigest()
            key = f"ip:{ip_hash}"

        # 3. Check Limit
        limiter = await get_rate_limiter()
        try:
            allowed, headers = await limiter.check_throughput(key, float(rps), burst)
        except RuntimeError as e:
            # Handle runtime failures (e.g. Redis down)
            # The lower level already checks MODE for fail-open logic.
            # If we are here, we MUST fail closed (503).
            import os
            mode = os.getenv("MODE", "dev").lower()
            
            # Phase 11 Spec: RATE_LIMITER_UNAVAILABLE (503, dev only)
            error_code = "RATE_LIMITER_UNAVAILABLE"  # Dev only per Phase 11 spec
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
