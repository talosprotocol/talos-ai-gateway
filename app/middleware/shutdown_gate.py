from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp
import logging

logger = logging.getLogger(__name__)

class ShutdownGateMiddleware(BaseHTTPMiddleware):
    _is_shutting_down = False

    def __init__(self, app: ASGIApp):
        super().__init__(app)

    @classmethod
    def set_shutting_down(cls, value: bool):
        cls._is_shutting_down = value
        if value:
            logger.info("Shutdown Gate enabled: Rejecting non-health traffic.")

    async def dispatch(self, request: Request, call_next):
        if self._is_shutting_down:
            # Allow health/live even during shutdown
            if request.url.path == "/health/live":
                 return await call_next(request)
            
            # Reject everything else
            return JSONResponse(
                status_code=503,
                content={
                    "error": "SERVER_SHUTTING_DOWN",
                    "detail": "Server is shutting down"
                }
            )
            
        return await call_next(request)
