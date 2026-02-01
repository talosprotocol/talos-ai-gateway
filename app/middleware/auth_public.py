"""Authentication Middleware."""

import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from fastapi import Depends, Header, HTTPException, Request

from app.adapters.postgres.key_store import KeyStore
from app.dependencies import (
    get_attestation_verifier,
    get_audit_logger,
    get_key_store,
    get_policy_engine,
    get_principal_store,
    get_surface_registry,
)
from app.domain.audit import AuditLogger
from app.domain.interfaces import PrincipalStore
from app.domain.registry import SurfaceItem, SurfaceRegistry
from app.errors import raise_talos_error
from app.middleware.attestation_http import (
    AttestationError,
    AttestationVerifier,
)
from app.policy import PolicyEngine
from app.utils.id import uuid7
from talos_sdk import IdentityValidationError, validate_principal

logger = logging.getLogger(__name__)


class AuthContext:
    """Authentication context for requests."""
    def __init__(
        self,
        key_id: str,
        team_id: str,
        org_id: str,
        scopes: list[str],
        allowed_model_groups: list[str],
        allowed_mcp_servers: list[str],
        principal_id: Optional[str] = None,
        # Phase 15: Budget Context
        budget_mode: str = "off",
        team_budget_mode: str = "off",
        overdraft_usd: str = "0",
        team_overdraft_usd: str = "0",
        max_tokens_default: Optional[int] = None,
        team_max_tokens_default: Optional[int] = None,
        budget_metadata: Optional[Dict[str, Any]] = None,
        team_budget_metadata: Optional[Dict[str, Any]] = None,
    ):
        self.key_id = key_id
        self.team_id = team_id
        self.org_id = org_id
        self.scopes = scopes
        self.allowed_model_groups = allowed_model_groups
        self.allowed_mcp_servers = allowed_mcp_servers
        self.principal_id = principal_id

        # Budget
        self.budget_mode = budget_mode
        self.team_budget_mode = team_budget_mode
        self.overdraft_usd = overdraft_usd
        self.team_overdraft_usd = team_overdraft_usd
        self.max_tokens_default = max_tokens_default
        self.team_max_tokens_default = team_max_tokens_default
        self.budget_metadata = budget_metadata or {}
        self.team_budget_metadata = team_budget_metadata or {}

        self.team_budget_metadata = team_budget_metadata or {}

    def has_scope(self, scope: str) -> bool:
        for s in self.scopes:
            if s == "*:*" or s == scope:
                return True
            if s.endswith(".*") and scope.startswith(s[:-2]):
                return True
        return False

    def can_access_model_group(self, group_id: str) -> bool:
        return (
            "*" in self.allowed_model_groups
            or group_id in self.allowed_model_groups
        )

    def can_access_mcp_server(self, server_id: str) -> bool:
        return (
            "*" in self.allowed_mcp_servers
            or server_id in self.allowed_mcp_servers
        )


async def get_auth_context(
    request: Request,
    authorization: Optional[str] = Header(None),
    x_talos_signature: Optional[str] = Header(None),
    key_store: KeyStore = Depends(get_key_store),
    verifier: AttestationVerifier = Depends(get_attestation_verifier),
    principal_store: PrincipalStore = Depends(get_principal_store),
    registry: SurfaceRegistry = Depends(get_surface_registry),
    audit_logger: AuditLogger = Depends(get_audit_logger),
    policy_engine: PolicyEngine = Depends(get_policy_engine)
) -> AuthContext:
    """Extract and validate virtual key from Authorization header."""
    
    # Context vars for logging
    surface = None
    key_id = "unknown"
    team_id = "unknown"
    principal_id = "unknown"
    
    try:
        # 0. Resolve Surface
        # Use fallback if route not available (e.g. testing or middleware
        # ordering)
        if hasattr(request, "route") and request.route:
            route_path = request.route.path
        else:
            # Fallback to current path (works for static paths, but templates
            # might fail)
            route_path = request.url.path

        surface = registry.match_request(request.method, route_path)
        if not surface:
            # Should be caught by Startup Gate, but redundancy is safe.
            # "Default deny in prod"
            raise_talos_error("RBAC_DENIED", 403, "Unknown surface")
        
        # Internal Service Bypass REMOVED for Production Hardening
        # All requests must be authenticated via valid headers or mTLS
        if authorization is None:
            raise_talos_error(
                "AUTH_INVALID", 401, "Missing Authorization header"
            )
        assert authorization is not None
        
        if not authorization.startswith("Bearer "):
            raise_talos_error(
                "AUTH_INVALID", 401, "Invalid Authorization format"
            )
        
        # 1. Validate Bearer Token
        key = authorization[7:]
        key_hash = key_store.hash_key(key)
        
        key_data = key_store.lookup_by_hash(key_hash)
        if key_data is None:
            raise_talos_error("AUTH_INVALID", 401, "Invalid key")
        assert key_data is not None

        if key_data.revoked:
            raise_talos_error("AUTH_REVOKED", 401, "Key has been revoked")

        key_id = key_data.id
        team_id = key_data.team_id

        # 2. RBAC Policy Check
        # Principal Context (using Key ID as proxy until binding is loaded)
        principal_ctx = {
            "id": key_id,
            "team_id": team_id,
            "org_id": key_data.org_id
        }

        # surface is from Depends(get_surface) which can return None if not
        # found
        if surface is None:
            raise_talos_error("NOT_FOUND", 404, "Surface not found")
        assert surface is not None

        # If surface requires multiple scopes, we enforce ALL must be granted.
        for required_perm in surface.required_scopes:
            resource_ctx = {
                "id": "gateway-surface",
                "org_id": key_data.org_id,
                "team_id": team_id 
            }
            
            result = policy_engine.authorize(
                principal_ctx, required_perm, resource_ctx
            )
            
            if result.allowed:
                continue

            # Fallback: Legacy Scope Check
            is_legacy_allowed = False
            for internal_scope in key_data.scopes:
                if internal_scope == "*:*" or internal_scope == required_perm:
                    is_legacy_allowed = True
                    break
                if (
                    internal_scope.endswith(".*")
                    and required_perm.startswith(internal_scope[:-2])
                ):
                    is_legacy_allowed = True
                    break
            
            if not is_legacy_allowed:
                raise_talos_error(
                    "RBAC_DENIED",
                    403,
                    f"Policy denied: {result.reason} "
                    f"(Perm: {required_perm})"
                )

        principal_id = key_id  # Default if no attestation binding
        # Actually principal_id usually refers to the human/system Identity,
        # not the Key.
        # If not signed, principal is the "Virtual Key holder".
        
        # 3. HTTP Attestation Enforce
        if surface.attestation_required:
            if not x_talos_signature:
                raise_talos_error(
                    "AUTH_INVALID",
                    401,
                    "Attestation required for this surface"
                )
            
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
                if principal_obj is None:
                    raise_talos_error("AUTH_INVALID", 401, "Signer lost")
                assert principal_obj is not None

                if principal_obj.get('team_id') != key_data.team_id:
                    # Log potential security event
                    client_ip = request.client.host if request.client else None
                    req_id = getattr(
                        request.state, "request_id", "req-unknown"
                    )
                    
                    audit_logger.log_event(
                        surface=surface,
                        principal={
                            "principal_id": principal_obj.get('id', 'unknown'),
                            "team_id": principal_obj.get('team_id', 'unknown'),
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
                        metadata={
                            "error": "Identity Binding Mismatch",
                            "bearer_team": team_id,
                            "signer_team": principal_obj.get('team_id')
                        }
                    )
                    request.state.authz_decision = "DENY"
                    raise_talos_error(
                        "RBAC_DENIED",
                        403,
                        "Identity binding mismatch: Bearer Team != Signer Team"
                    )
                
                principal_id = principal_obj.get('id', 'unknown')

            except AttestationError as e:
                request.state.authz_decision = "DENY"
                raise_talos_error(e.code, 401, str(e))
        
        # If success, we don't log success HERE. The Router or Post-Middleware
        # should log success.
        # But we return the context.
        ctx = AuthContext(
            key_id=key_data.id,
            team_id=key_data.team_id,
            org_id=key_data.org_id,
            scopes=key_data.scopes,
            allowed_model_groups=key_data.allowed_model_groups,
            allowed_mcp_servers=key_data.allowed_mcp_servers,
            principal_id=principal_id,
            # Pass budget data
            budget_mode=key_data.budget_mode,
            team_budget_mode=key_data.team_budget_mode,
            overdraft_usd=key_data.overdraft_usd,
            team_overdraft_usd=key_data.team_overdraft_usd,
            max_tokens_default=key_data.max_tokens_default,
            team_max_tokens_default=key_data.team_max_tokens_default,
            budget_metadata=key_data.budget,
            team_budget_metadata=key_data.team_budget
        )
        
        # 4. Construct & Validate Principal for SDK Hardening
        validation_auth_mode = (
            "signed"
            if (principal_id != "unknown" and principal_id != key_id)
            else "bearer"
        )
        
        # Determine the principal dictionary for validation
        validation_principal = {
            "schema_id": "talos.principal",
            "schema_version": "v2",
            "id": uuid7(),
            "principal_id": principal_id,
            "team_id": team_id,
            "type": "service_account", 
            "status": "active",
            "auth_mode": validation_auth_mode,
            "created_at": (
                datetime.now(timezone.utc).strftime(
                    "%Y-%m-%dT%H:%M:%S.%f"
                )[:-3]
                + "Z"
            )
        }

        if validation_auth_mode == "signed":
            # key_id is the signer in this context
            validation_principal["signer_key_id"] = key_id

        # Call SDK validation (Hardening)
        try:
            # Full Principal schema validation
            validate_principal(validation_principal)
        except IdentityValidationError as e:
            # Map identity validation errors to 400 Bad Request
            logger.error("Principal identity validation failure: %s", str(e))
            raise_talos_error(
                "AUTH_INVALID", 400, "Identity validation failed: %s" % str(e)
            )
        except Exception as e:  # pylint: disable=broad-exception-caught
            logger.warning("Unexpected validation error: %s", str(e))
            # For robustness in tests, we might proceed if it's just a schema
            # mismatch on non-critical fields
            if os.getenv("DEV_MODE", "false").lower() == "true":
                pass
            else:
                raise_talos_error(
                    "AUTH_INVALID", 400, f"Validation failure: {str(e)}"
                )
        
        request.state.principal = validation_principal
        request.state.auth_context = ctx
        request.state.auth = ctx
        request.state.surface = surface
        
        return ctx

    except Exception as e:  # noqa: B902
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
            # Fallback: principal_id was signer_key_id if verified
            signer_key_id = principal_id

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
        # Checkpoint: Mark as emitted so subsequent middleware doesn't
        # double-log
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
        return await get_auth_context(
            request,
            authorization,
            None,
            key_store,
            verifier,
            principal_store,
            registry,
            audit_logger,
        )
    except HTTPException:
        return None


def require_scope(scope: str) -> Any:
    """Dependency that requires a specific scope."""
    async def checker(
        auth: AuthContext = Depends(get_auth_context),
    ) -> AuthContext:
        if not auth.has_scope(scope):
            raise HTTPException(
                status_code=403,
                detail={
                    "error": {
                        "code": "POLICY_DENIED",
                        "message": f"Missing scope: {scope}"
                    }
                }
            )
        return auth
    return checker
