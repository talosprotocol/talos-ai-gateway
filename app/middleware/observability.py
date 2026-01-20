from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp
from fastapi import Request
import os

class RegionHeaderMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: ASGIApp):
        super().__init__(app)
        self.region_id = os.getenv("TALOS_REGION_ID", "unknown")

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Talos-Region"] = self.region_id
        return response
