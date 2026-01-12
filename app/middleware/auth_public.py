
from fastapi import Request, Depends, Header, HTTPException
from typing import Optional
from app.dependencies import get_key_store, get_attestation_verifier, get_principal_store, get_surface_registry, get_audit_logger
from app.adapters.postgres.key_store import KeyStore
from app.middleware.attestation_http import AttestationVerifier, AttestationError
from app.domain.interfaces import PrincipalStore
from app.domain.registry import SurfaceRegistry, SurfaceItem
from app.errors import raise_talos_error
from app.domain.audit import AuditLogger

class AuthContext:
    """Authentication context for requests."""
    def __init__(self, key_id: str, team_id: str, org_id: str, scopes: list, 
                 allowed_model_groups: list, allowed_mcp_servers: list,
                 principal_id: Optional[str] = None):
        self.key_id = key_id
        self.team_id = team_id
        self.org_id = org_id
        self.scopes = scopes
        self.allowed_model_groups = allowed_model_groups
        self.allowed_mcp_servers = allowed_mcp_servers
        self.principal_id = principal_id

    def has_scope(self, scope: str) -> bool:
        return scope in self.scopes

    def can_access_model_group(self, group_id: str) -> bool:
        return "*" in self.allowed_model_groups or group_id in self.allowed_model_groups

    def can_access_mcp_server(self, server_id: str) -> bool:
        return "*" in self.allowed_mcp_servers or server_id in self.allowed_mcp_servers


async def get_auth_context(
    request: Request,
    authorization: Optional[str] = Header(None),
    x_talos_signature: Optional[str] = Header(None),
    key_store: KeyStore = Depends(get_key_store),
    verifier: AttestationVerifier = Depends(get_attestation_verifier),
    principal_store: PrincipalStore = Depends(get_principal_store),
    registry: SurfaceRegistry = Depends(get_surface_registry),
    audit_logger: AuditLogger = Depends(get_audit_logger)
) -> AuthContext:
    """Extract and validate virtual key from Authorization header."""
    
    # Context vars for logging
    surface = None
    key_id = "unknown"
    team_id = "unknown"
    principal_id = "unknown"
    
    try:
        # 0. Resolve Surface
        # Use fallback if route not available (e.g. testing or middleware ordering)
        if hasattr(request, "route") and request.route:
            route_path = request.route.path
        else:
            # Fallback to current path (works for static paths, but templates might fail)
            route_path = request.url.path

        surface = registry.match_request(request.method, route_path)
        if not surface:
            # Should be caught by Startup Gate, but redundancy is safe.
            # "Default deny in prod"
            raise_talos_error("RBAC_DENIED", 403, "Unknown surface")
        
        if not authorization:
            raise_talos_error("AUTH_INVALID", 401, "Missing Authorization header")
        
        if not authorization.startswith("Bearer "):
            raise_talos_error("AUTH_INVALID", 401, "Invalid Authorization format")
        
        # 1. Validate Bearer Token
        key = authorization[7:]
        key_hash = key_store.hash_key(key)
        
        key_data = key_store.lookup_by_hash(key_hash)
        if not key_data:
            raise_talos_error("AUTH_INVALID", 401, "Invalid key")
        
        if key_data.revoked:
            raise_talos_error("AUTH_REVOKED", 401, "Key has been revoked")

        key_id = key_data.id
        team_id = key_data.team_id

        # 2. Enforce Scopes
        # Check if key has ALL required scopes? Or ANY? 
        # Usually "required_scopes" means you need ONE OF (if alternate) or ALL?
        # Spec says "required_scopes": ["llm.invoke"].
        # Let's enforce that key MUST have the required scope.
        # Note: scopes can be wildcards in key? "llm.*"
        # Helper to match scope. To keep simple for now: exact match or crude wildcard.
        has_permission = False
        for req in surface.required_scopes:
            # Check against key_data.scopes
            if req in key_data.scopes:
                has_permission = True
                break
            # Handle simple wildcards like "llm.*" matching "llm.invoke"
            # If key has "llm.*", does it grant "llm.invoke"? Yes.
            for granted in key_data.scopes:
                 if granted.endswith(".*") and req.startswith(granted[:-2]):
                      has_permission = True
                      break
            if has_permission: break
        
        if not has_permission:
             raise_talos_error("RBAC_DENIED", 403, f"Missing required scopes: {surface.required_scopes}")

        principal_id = key_id # Default to key_id if no attestation binding yet? 
        # Actually principal_id usually refers to the human/system Identity, not the Key.
        # If not signed, principal is the "Virtual Key holder".
        
        # 3. HTTP Attestation Enforce
        if surface.attestation_required:
            if not x_talos_signature:
                 raise_talos_error("AUTH_INVALID", 401, "Attestation required for this surface")
            
            try:
                raw_body = await request.body()
                
                # Opcode from Registry!
                opcode = surface.id
                
                # Path+Query
                scope = request.scope
                raw_path = scope.get('raw_path', b'/').decode('ascii')
                qs = scope.get('query_string', b'').decode('ascii')
                path_query = raw_path + (f"?{qs}" if qs else "")
                
                signer_key_id = await verifier.verify_request(
                    dict(request.headers),
                    raw_body,
                    request.method,
                    path_query,
                    opcode
                )
                
                # Identity Binding
                principal_obj = principal_store.get_principal(signer_key_id)
                if not principal_obj:
                     raise_talos_error("AUTH_INVALID", 401, "Signer lost")

                if principal_obj['team_id'] != key_data.team_id:
                    # Log potential security event
                    client_ip = request.client.host if request.client else None
                    req_id = getattr(request.state, "request_id", "req-unknown")
                    
                    audit_logger.log_event(
                        surface=surface,
                        principal={
                            "principal_id": principal_obj['id'],
                            "team_id": principal_obj['team_id'],
                            "auth_mode": "signed",
                            "signer_key_id": signer_key_id
                        },
                        http_info={
                            "method": request.method,
                            "path": request.url.path,
                            "status_code": 403,
                            "client_ip": client_ip
                        },
                        outcome="denied",
                        request_id=req_id,
                        metadata={"error": "Identity Binding Mismatch", "bearer_team": team_id, "signer_team": principal_obj['team_id']}
                    )
                    request.state.authz_decision = "DENY"
                    raise_talos_error("RBAC_DENIED", 403, "Identity binding mismatch: Bearer Team != Signer Team")
                
                principal_id = principal_obj['id']

            except AttestationError as e:
                request.state.authz_decision = "DENY"
                raise_talos_error(e.code, 401, str(e))
        
        # If success, we don't log success HERE. The Router or Post-Middleware should log success.
        # But we return the context.
        ctx = AuthContext(
            key_id=key_data.id,
            team_id=key_data.team_id,
            org_id=key_data.org_id,
            scopes=key_data.scopes,
            allowed_model_groups=key_data.allowed_model_groups,
            allowed_mcp_servers=key_data.allowed_mcp_servers,
            principal_id=principal_id
        )
        
        # Attach to state for AuditMiddleware
        request.state.auth = ctx
        request.state.surface = surface
        
        return ctx

    except Exception as e:
        # Catch standardized Talos errors or others
        # Log failure
        # 1. Resolve effective surface for logging
        if not surface:
            # Fallback for unknown surfaces
            surface = SurfaceItem(
                id="gateway.access_control",
                type="system",
                required_scopes=[],
                attestation_required=False,
                audit_action="gateway.access_control.deny",
                data_classification="public",
                audit_meta_allowlist=["error", "code", "message"]
            )
        
        # 2. Extract details
        err_code = "INTERNAL_ERROR"
        err_msg = str(e)
        status_code = 500
        
        if isinstance(e, HTTPException):
            status_code = e.status_code
            if isinstance(e.detail, dict) and "error" in e.detail:
                err_code = e.detail["error"].get("code", "UNKNOWN")
                err_msg = e.detail["error"].get("message", str(e))
        
        # Determine Outcome
        log_outcome = "denied" if status_code in [401, 403] else "failure"
        if log_outcome == "denied":
            request.state.authz_decision = "DENY"

        # 3. Build Principal Dict
        # Determine if attested/signed
        auth_mode = "bearer"
        signer_key_id = None
        if principal_id != "unknown" and principal_id != key_id:
            auth_mode = "signed"
            signer_key_id = principal_id # In this fallback logic, principal_id was signer_key_id if verified

        principal_data = {
            "principal_id": principal_id,
            "team_id": team_id,
            "auth_mode": auth_mode,
            "signer_key_id": signer_key_id
        }

        # 4. Log
        client_ip = request.client.host if request.client else None
        req_id = getattr(request.state, "request_id", "req-unknown")
        
        audit_logger.log_event(
            surface=surface,
            principal=principal_data,
            http_info={
                "method": request.method,
                "path": request.url.path,
                "status_code": status_code,
                "client_ip": client_ip
            },
            outcome=log_outcome,
            request_id=req_id,
            metadata={
                "error": err_msg,
                "code": err_code,
                "message": err_msg
            }
        )
        # Checkpoint: Mark as emitted so subsequent middleware doesn't double-log
        request.state.audit_emitted = True
        raise e

async def get_auth_context_or_none(
    request: Request,
    authorization: Optional[str] = Header(None),
    key_store: KeyStore = Depends(get_key_store),
    verifier: AttestationVerifier = Depends(get_attestation_verifier),
    principal_store: PrincipalStore = Depends(get_principal_store),
    registry: SurfaceRegistry = Depends(get_surface_registry),
    audit_logger: AuditLogger = Depends(get_audit_logger)
) -> Optional[AuthContext]:
    """Optional version."""
    if not authorization:
        return None
    try:
        return await get_auth_context(request, authorization, None, key_store, verifier, principal_store, registry, audit_logger)
    except HTTPException:
        return None


def require_scope(scope: str):
    """Dependency that requires a specific scope."""
    async def checker(auth: AuthContext = Depends(get_auth_context)):
        if not auth.has_scope(scope):
            raise HTTPException(status_code=403, detail={"error": {"code": "POLICY_DENIED", "message": f"Missing scope: {scope}"}})
        return auth
    return checker
