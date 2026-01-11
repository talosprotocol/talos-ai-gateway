
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response
from app.domain.audit import get_audit_logger
import uuid

class TalosAuditMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        # Ensure request ID
        req_id = request.headers.get("X-Request-ID", str(uuid.uuid4()))
        request.state.request_id = req_id
        
        # Init state for Metadata updates from routes
        request.state.audit_meta = {}
        request.state.auth = None
        request.state.surface = None
        
        try:
            response = await call_next(request)
        except Exception as e:
            # Check if auth/surface captured before crash?
            # If application crashed, response is 500
            # Usually FastAPI handles exception and returns Response
            # So we might reach 'response = ...'
            raise e
        
        # Post-Processing
        if getattr(request.state, "auth", None) and getattr(request.state, "surface", None):
            self._log_success_or_failure(request, response)
            
        return response

    def _log_success_or_failure(self, request: Request, response: Response):
        surface = request.state.surface
        auth = request.state.auth
        meta = getattr(request.state, "audit_meta", {})
        
        # Determine Status
        status = "success"
        if response.status_code >= 400:
            status = "failure"
        
        # Principal
        principal_data = {
            "principal_id": auth.principal_id or auth.key_id,
            "team_id": auth.team_id,
            "org_id": auth.org_id,
            "auth_mode": "bearer_attested" if auth.principal_id else "bearer",
            "signer_key_id": auth.principal_id if auth.principal_id else None
        }
        
        audit_logger = get_audit_logger()
        audit_logger.log_event(
            surface=surface,
            principal=principal_data,
            http_info={
                "method": request.method,
                "path": request.url.path,
                "status_code": response.status_code,
                "client_ip_hash": None # TODO hash IP if present
            },
            status=status,
            request_id=request.state.request_id,
            metadata=meta,
            resource=None # Routes can set this in meta or special state if needed
        )
