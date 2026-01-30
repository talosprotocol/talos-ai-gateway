from fastapi import APIRouter, Depends, HTTPException, Request, Response
from typing import Optional, List
from app.domain.a2a.models import (
    SessionCreateRequest, SessionAcceptRequest, SessionRotateRequest, FrameSendRequest,
    GroupCreateRequest, GroupMemberAddRequest
)
from app.domain.a2a.session_manager import A2ASessionManager
from app.domain.a2a.frame_store import A2AFrameStore
from app.domain.a2a.group_manager import A2AGroupManager
from app.domain.audit import AuditLogger
from app.dependencies import (
    get_a2a_session_manager, get_a2a_frame_store, get_a2a_group_manager,
    get_audit_logger
)

router = APIRouter()

def get_actor_id(request: Request) -> str:
    # Use request.state.principal set by AuthMiddleware
    if not hasattr(request.state, "principal") or not request.state.principal:
        raise HTTPException(status_code=401, detail="Unauthorized")
    return request.state.principal.get("principal_id") or request.state.principal.get("id")

@router.post("/sessions", status_code=201)
def create_session(
    req: SessionCreateRequest,
    request: Request,
    sm: A2ASessionManager = Depends(get_a2a_session_manager),
    actor_id: str = Depends(get_actor_id)
):
    if actor_id == req.responder_id:
        raise HTTPException(status_code=400, detail="Initiator cannot be responder")
    
    session = sm.create_session(actor_id, req)
    request.state.audit_meta["session_id"] = session.session_id
    return session

@router.get("/sessions/{id}")
def get_session(
    id: str,
    sm: A2ASessionManager = Depends(get_a2a_session_manager),
    actor_id: str = Depends(get_actor_id)
):
    session = sm.get_session(id)
    if not session:
        raise HTTPException(status_code=404, detail="A2A_SESSION_NOT_FOUND")
    return session

@router.post("/sessions/{id}/accept")
def accept_session(
    id: str,
    req: SessionAcceptRequest,
    request: Request,
    sm: A2ASessionManager = Depends(get_a2a_session_manager),
    actor_id: str = Depends(get_actor_id)
):
    try:
        session = sm.accept_session(id, actor_id, req)
        request.state.audit_meta["session_id"] = id
        return session
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.post("/sessions/{id}/rotate")
def rotate_session(
    id: str,
    req: SessionRotateRequest,
    request: Request,
    sm: A2ASessionManager = Depends(get_a2a_session_manager),
    actor_id: str = Depends(get_actor_id)
):
    try:
        session = sm.rotate_session(id, actor_id, req)
        request.state.audit_meta["session_id"] = id
        return session
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.delete("/sessions/{id}")
def close_session(
    id: str,
    request: Request,
    sm: A2ASessionManager = Depends(get_a2a_session_manager),
    actor_id: str = Depends(get_actor_id)
):
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
):
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
):
    # Recipient isolation is enforced within list_frames by passing actor_id
    frames, next_cursor = fs.list_frames(id, actor_id, cursor, limit)
    
    request.state.audit_meta["session_id"] = id
    request.state.audit_meta["frame_count"] = len(frames)
    return {"items": frames, "next_cursor": next_cursor}

@router.post("/groups", status_code=201)
def create_group(
    req: GroupCreateRequest,
    request: Request,
    gm: A2AGroupManager = Depends(get_a2a_group_manager),
    actor_id: str = Depends(get_actor_id)
):
    group = gm.create_group(actor_id, req)
    request.state.audit_meta["group_id"] = group.group_id
    return group

@router.get("/groups/{id}")
def get_group(
    id: str,
    gm: A2AGroupManager = Depends(get_a2a_group_manager),
    actor_id: str = Depends(get_actor_id)
):
    group = gm.get_group(id)
    if not group:
         raise HTTPException(status_code=404, detail="A2A_GROUP_NOT_FOUND")
    return group

@router.post("/groups/{id}/members")
def add_group_member(
    id: str,
    req: GroupMemberAddRequest,
    request: Request,
    gm: A2AGroupManager = Depends(get_a2a_group_manager),
    actor_id: str = Depends(get_actor_id)
):
    try:
        group = gm.add_member(id, actor_id, req)
        request.state.audit_meta["group_id"] = id
        request.state.audit_meta["target_id"] = req.member_id
        return group
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.delete("/groups/{id}/members/{pid}")
def remove_group_member(
    id: str,
    pid: str,
    request: Request,
    gm: A2AGroupManager = Depends(get_a2a_group_manager),
    actor_id: str = Depends(get_actor_id)
):
    try:
        group = gm.remove_member(id, actor_id, pid)
        request.state.audit_meta["group_id"] = id
        request.state.audit_meta["target_id"] = pid
        return group
    except PermissionError as e:
         raise HTTPException(status_code=403, detail=str(e))
    except ValueError as e:
         raise HTTPException(status_code=400, detail=str(e))

@router.delete("/groups/{id}")
def close_group(
    id: str,
    request: Request,
    gm: A2AGroupManager = Depends(get_a2a_group_manager),
    actor_id: str = Depends(get_actor_id)
):
    try:
        res = gm.close_group(id, actor_id)
        request.state.audit_meta["group_id"] = id
        return res
    except PermissionError as e:
         raise HTTPException(status_code=403, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
