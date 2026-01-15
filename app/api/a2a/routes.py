from fastapi import APIRouter, Depends, Request, Body, Header, Query
from typing import Dict, Any

from app.middleware.auth_public import AuthContext, get_auth_context_or_none
from app.middleware.attestation import get_attestation_auth
from app.dependencies import (
    get_routing_service, get_audit_store, get_rate_limit_store, get_usage_store, 
    get_mcp_client, get_task_store, get_key_store, get_capability_validator
)
from app.domain.interfaces import (
    AuditStore, RateLimitStore, UsageStore, TaskStore
)
from app.adapters.mcp.client import McpClient
from app.domain.routing import RoutingService
from app.domain.a2a.dispatcher import A2ADispatcher
from app.adapters.postgres.key_store import KeyStore

router = APIRouter()

async def get_integrated_auth(
    auth_bearer: AuthContext | None = Depends(get_auth_context_or_none),
    auth_attest: AuthContext | None = Depends(get_attestation_auth),
    token: str | None = Query(default=None),
    key_store: KeyStore = Depends(get_key_store)
) -> AuthContext:
    if auth_attest:
        return auth_attest
    if auth_bearer:
        return auth_bearer
        
    # Dev mode query token fallback (only if DEV_MODE=true)
    if app_settings.dev_mode and token:
         key_hash = key_store.hash_key(token)
         key_data = key_store.lookup_by_hash(key_hash)
         if key_data and not key_data.revoked:
              return AuthContext(
                  key_id=key_data.id,
                  team_id=key_data.team_id,
                  org_id=key_data.org_id,
                  scopes=key_data.scopes,
                  allowed_model_groups=key_data.allowed_model_groups,
                  allowed_mcp_servers=key_data.allowed_mcp_servers
              )

    raise HTTPException(
        status_code=401, 
        detail={"error": {"code": -32000, "message": "Unauthorized", "data": {"details": "Missing Bearer token or Attestation headers"}}}
    )

@router.post("/", response_model=None)
async def handle_jsonrpc(
    request: Request,
    payload: Dict[str, Any] = Body(...),
    auth: AuthContext = Depends(get_integrated_auth),
    routing_service: RoutingService = Depends(get_routing_service),
    audit_store: AuditStore = Depends(get_audit_store),
    rl_store: RateLimitStore = Depends(get_rate_limit_store),
    usage_store: UsageStore = Depends(get_usage_store),
    task_store: TaskStore = Depends(get_task_store),
    mcp_client: McpClient = Depends(get_mcp_client),
    cap_validator: Any = Depends(get_capability_validator),
    x_talos_capability: Optional[str] = Header(None)
):
    """
    JSON-RPC 2.0 Endpoint for Agent-to-Agent interaction.
    """
    dispatcher = A2ADispatcher(
        auth=auth,
        routing_service=routing_service,
        audit_store=audit_store,
        rl_store=rl_store,
        usage_store=usage_store,
        task_store=task_store,
        mcp_client=mcp_client,
        capability_validator=cap_validator
    )
    
    response = await dispatcher.dispatch(payload, capability=x_talos_capability)
    return response

# SSE Endpoint (Phase A4)
from fastapi.responses import StreamingResponse
from fastapi import Query, HTTPException
from app.settings import settings as app_settings
from app.domain.a2a.streaming import stream_task_events
from app.adapters.redis.client import get_redis_client
# Actually, calling get_auth_context with a modified request is hard in a sub-dependency.
# But we can call `get_auth_context` directly if we import it.
import uuid

async def get_sse_auth(
    authorization: str | None = Header(default=None),
    token: str | None = Query(default=None),
    key_store: KeyStore = Depends(get_key_store)
) -> AuthContext:
    """Unified SSE Auth resolver."""
    # This is redundant with get_integrated_auth, but kept for compatibility if needed.
    # Preferably, just use get_integrated_auth.
    return await get_integrated_auth(
        auth_bearer=await get_auth_context_or_none(authorization, key_store),
        token=token,
        key_store=key_store
    )

@router.get("/tasks/{task_id}/events")
async def stream_events(
    request: Request,
    task_id: str,
    after_cursor: str | None = Query(default=None),
    auth: AuthContext = Depends(get_integrated_auth),
    task_store: TaskStore = Depends(get_task_store)
):
    request_id = str(uuid.uuid4()) # Generate request ID for the stream session
    
    if "a2a.stream" not in auth.scopes:
         raise HTTPException(
             status_code=403, 
             detail={
                 "error": {
                     "talos_code": "RBAC_DENIED", 
                     "message": "Missing 'a2a.stream' scope", 
                     "request_id": request_id
                 }
             }
         )
         
    redis_client = await get_redis_client()
    if not redis_client:
         raise HTTPException(
             status_code=503, 
             detail={
                 "error": {
                     "talos_code": "SERVICE_UNAVAILABLE", 
                     "message": "Streaming unavailable", 
                     "request_id": request_id
                 }
             }
         )
         
    return StreamingResponse(
        stream_task_events(
            task_id=task_id, 
            team_id=auth.team_id, 
            task_store=task_store, 
            redis_client=redis_client,
            request_id=request_id,
            after_cursor=after_cursor
        ),
        media_type="text/event-stream"
    )
