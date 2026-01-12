
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response
from app.dependencies import get_audit_logger
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
        if getattr(request.state, "audit_emitted", False):
            return

        surface = request.state.surface
        auth = request.state.auth
        meta = getattr(request.state, "audit_meta", {})
        
        # Determine Outcome based on locked rules
        authz = getattr(request.state, "authz_decision", None)
        if authz == "DENY":
            outcome = "denied"
        elif response.status_code >= 400:
            outcome = "failure"
        else:
            outcome = "success"
        
        # Principal Logic (Normative Shapes)
        is_signed = auth.principal_id and auth.principal_id != auth.key_id
        
        principal_data = {
            "principal_id": auth.principal_id or auth.key_id,
            "team_id": auth.team_id,
            "auth_mode": "signed" if is_signed else "bearer",
            "signer_key_id": auth.principal_id if is_signed else None
        }
        
        client_ip = request.client.host if request.client else None
        audit_logger = get_audit_logger()
        is_trusted = client_ip in audit_logger.trusted_proxies if client_ip else False

        audit_logger = get_audit_logger()
        is_trusted = client_ip in audit_logger.trusted_proxies if client_ip else False

        audit_logger.log_event(
            surface=surface,
            principal=principal_data,
            http_info={
                "method": request.method,
                "path": request.url.path,
                "status_code": response.status_code,
                "client_ip": client_ip,
                "is_trusted": is_trusted
            },
            outcome=outcome,
            request_id=request.state.request_id,
            metadata=meta
        )
