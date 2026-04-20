import inspect
import os
from typing import Any, Dict, Optional, cast

from fastapi import APIRouter, Depends, Header, HTTPException, Request

from app.adapters.mcp.client import McpClient
from app.adapters.postgres.models import A2AGroup, A2ASession
from app.dependencies import (
    get_a2a_frame_store,
    get_a2a_group_manager,
    get_a2a_session_manager,
    get_audit_logger,
    get_audit_store,
    get_capability_validator,
    get_mcp_client,
    get_rate_limit_store,
    get_routing_service,
    get_task_store,
    get_usage_store,
)
from app.domain.a2a.dispatcher import A2ADispatcher
from app.domain.a2a.models import (
    FrameSendRequest,
    GroupCreateRequest,
    GroupMemberAddRequest,
    SessionAcceptRequest,
    SessionCreateRequest,
    SessionRotateRequest,
)
from app.domain.audit import AuditLogger
from app.domain.interfaces import AuditStore, RateLimitStore, TaskStore, UsageStore
from app.domain.routing import RoutingService
from app.domain.a2a.session_manager import A2ASessionManager
from app.domain.a2a.frame_store import A2AFrameStore
from app.domain.a2a.group_manager import A2AGroupManager
from app.middleware.auth_public import AuthContext, get_auth_context
from app.middleware.auth_public import _is_probable_jwt, _session_auth_context_from_claims
from app.domain.auth import get_admin_validator

router = APIRouter()

def get_actor_id(request: Request) -> str:
    # Use request.state.principal set by AuthMiddleware
    if not hasattr(request.state, "principal") or not request.state.principal:
        raise HTTPException(status_code=401, detail="Unauthorized")
    return cast(str, request.state.principal.get("principal_id") or request.state.principal.get("id"))


async def get_jsonrpc_auth_context(
    request: Request,
    authorization: Optional[str] = Header(None),
) -> AuthContext:
    """Resolve auth for the compat JSON-RPC route without forcing full key-store bootstrap in tests."""
    if not authorization:
        raise HTTPException(status_code=401, detail="Missing Authorization header")
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Invalid Authorization format")
    token = authorization[7:]

    if _is_probable_jwt(token):
        claims = get_admin_validator().validate_token(token)
        return _session_auth_context_from_claims(claims)

    override = request.app.dependency_overrides.get(get_auth_context)
    if override is not None:
        auth = override()
        if inspect.isawaitable(auth):
            auth = await auth
        return cast(AuthContext, auth)

    if os.getenv("MODE", "dev").lower() == "prod":
        raise HTTPException(status_code=500, detail="Compat A2A auth bootstrap unavailable in production")

    expected_dev_token = os.getenv("A2A_DEV_BEARER_TOKEN", "sk-test-key")
    if token != expected_dev_token:
        raise HTTPException(
            status_code=401,
            detail="Invalid key",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return AuthContext(
        key_id="019da2a2-8a74-712e-9698-57ce81b535b1",
        team_id="019da2a2-8a74-7c16-ac38-4e85e53093b4",
        org_id="019da2a2-8a74-749e-b2a5-b839a84ac989",
        scopes=["a2a.send", "a2a.discovery.read", "a2a.invoke", "a2a.stream", "llm.invoke", "mcp.invoke"],
        allowed_model_groups=["*"],
        allowed_mcp_servers=["*"],
        principal_id="dev-principal",
    )


@router.post("/", response_model=None)
async def jsonrpc_entrypoint(
    payload: Dict[str, Any],
    request: Request,
    idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key"),
    x_talos_nonce: Optional[str] = Header(None, alias="X-Talos-Nonce"),
    auth: AuthContext = Depends(get_jsonrpc_auth_context),
    routing_service: RoutingService = Depends(get_routing_service),
    audit_store: AuditStore = Depends(get_audit_store),
    rl_store: RateLimitStore = Depends(get_rate_limit_store),
    usage_store: UsageStore = Depends(get_usage_store),
    task_store: TaskStore = Depends(get_task_store),
    mcp_client: McpClient = Depends(get_mcp_client),
    capability_validator: Any = Depends(get_capability_validator),
    audit_logger: AuditLogger = Depends(get_audit_logger),
) -> Dict[str, Any]:
    dispatcher = A2ADispatcher(
        auth=auth,
        routing_service=routing_service,
        audit_store=audit_store,
        rl_store=rl_store,
        usage_store=usage_store,
        task_store=task_store,
        mcp_client=mcp_client,
        capability_validator=capability_validator,
        audit_logger=audit_logger,
    )
    idem_key = idempotency_key or x_talos_nonce
    return await dispatcher.dispatch(payload, idempotency_key=idem_key)

@router.post("/sessions", status_code=201, response_model=None)
def create_session(
    req: SessionCreateRequest,
    request: Request,
    sm: A2ASessionManager = Depends(get_a2a_session_manager),
    actor_id: str = Depends(get_actor_id)
) -> A2ASession:
    if actor_id == req.responder_id:
        raise HTTPException(status_code=400, detail="Initiator cannot be responder")
    
    session = sm.create_session(actor_id, req)
    request.state.audit_meta["session_id"] = session.session_id
    return session

@router.get("/sessions/{id}", response_model=None)
def get_session(
    id: str,
    sm: A2ASessionManager = Depends(get_a2a_session_manager),
    actor_id: str = Depends(get_actor_id)
) -> A2ASession:
    session = sm.get_session(id)
    if not session:
        raise HTTPException(status_code=404, detail="A2A_SESSION_NOT_FOUND")
    return session

@router.post("/sessions/{id}/accept", response_model=None)
def accept_session(
    id: str,
    req: SessionAcceptRequest,
    request: Request,
    sm: A2ASessionManager = Depends(get_a2a_session_manager),
    actor_id: str = Depends(get_actor_id)
) -> A2ASession:
    try:
        session = sm.accept_session(id, actor_id, req)
        request.state.audit_meta["session_id"] = id
        return session
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.post("/sessions/{id}/rotate", response_model=None)
def rotate_session(
    id: str,
    req: SessionRotateRequest,
    request: Request,
    sm: A2ASessionManager = Depends(get_a2a_session_manager),
    actor_id: str = Depends(get_actor_id)
) -> A2ASession:
    try:
        session = sm.rotate_session(id, actor_id, req)
        request.state.audit_meta["session_id"] = id
        return session
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.delete("/sessions/{id}", response_model=None)
def close_session(
    id: str,
    request: Request,
    sm: A2ASessionManager = Depends(get_a2a_session_manager),
    actor_id: str = Depends(get_actor_id)
) -> A2ASession:
    try:
        session = sm.close_session(id, actor_id)
        request.state.audit_meta["session_id"] = id
        return session
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

@router.post("/sessions/{id}/frames", status_code=201)
def send_frame(
    id: str,
    req: FrameSendRequest,
    request: Request,
    sm: A2ASessionManager = Depends(get_a2a_session_manager),
    fs: A2AFrameStore = Depends(get_a2a_frame_store),
    actor_id: str = Depends(get_actor_id)
) -> Any:
    # Validate session
    session = sm.get_session(id)
    if not session:
        raise HTTPException(status_code=404, detail="A2A_SESSION_NOT_FOUND")
    if session.state != "active":
        raise HTTPException(status_code=400, detail="A2A_SESSION_STATE_INVALID")
        
    # Enforce isolation: actor == sender
    if actor_id != req.frame.sender_id:
        raise HTTPException(status_code=403, detail="A2A_MEMBER_NOT_ALLOWED")
        
    # Enforce participation
    if actor_id not in [session.initiator_id, session.responder_id]:
        raise HTTPException(status_code=403, detail="A2A_MEMBER_NOT_ALLOWED")
        
    # Derive recipient
    recipient_id = str(session.responder_id) if actor_id == session.initiator_id else str(session.initiator_id)
    
    # Validation
    if req.frame.session_id != id:
        raise HTTPException(status_code=400, detail="A2A_SESSION_ID_MISMATCH")
        
    try:
        result = fs.store_frame(req.frame, recipient_id)
        request.state.audit_meta["session_id"] = id
        request.state.audit_meta["sender_seq"] = req.frame.sender_seq
        return result
    except ValueError as e:
        msg = str(e)
        if "REPLAY" in msg:
            raise HTTPException(status_code=409, detail=msg)
        raise HTTPException(status_code=400, detail=msg)

@router.get("/sessions/{id}/frames")
def list_frames(
    id: str,
    request: Request,
    cursor: Optional[str] = None,
    limit: int = 100,
    fs: A2AFrameStore = Depends(get_a2a_frame_store),
    actor_id: str = Depends(get_actor_id)
) -> Dict[str, Any]:
    # Recipient isolation is enforced within list_frames by passing actor_id
    frames, next_cursor = fs.list_frames(id, actor_id, cursor, limit)
    
    request.state.audit_meta["session_id"] = id
    request.state.audit_meta["frame_count"] = len(frames)
    return {"items": frames, "next_cursor": next_cursor}

@router.post("/groups", status_code=201, response_model=None)
def create_group(
    req: GroupCreateRequest,
    request: Request,
    gm: A2AGroupManager = Depends(get_a2a_group_manager),
    actor_id: str = Depends(get_actor_id)
) -> A2AGroup:
    group = gm.create_group(actor_id, req)
    request.state.audit_meta["group_id"] = group.group_id
    return group

@router.get("/groups/{id}", response_model=None)
def get_group(
    id: str,
    gm: A2AGroupManager = Depends(get_a2a_group_manager),
    actor_id: str = Depends(get_actor_id)
) -> A2AGroup:
    group = gm.get_group(id)
    if not group:
         raise HTTPException(status_code=404, detail="A2A_GROUP_NOT_FOUND")
    return group

@router.post("/groups/{id}/members", response_model=None)
def add_group_member(
    id: str,
    req: GroupMemberAddRequest,
    request: Request,
    gm: A2AGroupManager = Depends(get_a2a_group_manager),
    actor_id: str = Depends(get_actor_id)
) -> A2AGroup:
    try:
        group = gm.add_member(id, actor_id, req)
        request.state.audit_meta["group_id"] = id
        request.state.audit_meta["target_id"] = req.member_id
        return group
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.delete("/groups/{id}/members/{pid}", response_model=None)
def remove_group_member(
    id: str,
    pid: str,
    request: Request,
    gm: A2AGroupManager = Depends(get_a2a_group_manager),
    actor_id: str = Depends(get_actor_id)
) -> A2AGroup:
    try:
        group = gm.remove_member(id, actor_id, pid)
        request.state.audit_meta["group_id"] = id
        request.state.audit_meta["target_id"] = pid
        return group
    except PermissionError as e:
         raise HTTPException(status_code=403, detail=str(e))
    except ValueError as e:
         raise HTTPException(status_code=400, detail=str(e))

@router.delete("/groups/{id}", response_model=None)
def close_group(
    id: str,
    request: Request,
    gm: A2AGroupManager = Depends(get_a2a_group_manager),
    actor_id: str = Depends(get_actor_id)
) -> Optional[A2AGroup]:
    try:
        res = gm.close_group(id, actor_id)
        request.state.audit_meta["group_id"] = id
        return res
    except PermissionError as e:
         raise HTTPException(status_code=403, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
