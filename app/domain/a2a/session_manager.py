import logging
import hashlib
from datetime import datetime, timedelta
from typing import Optional
from sqlalchemy.orm import Session
from sqlalchemy import text
from app.domain.a2a.models import (
    SessionCreateRequest, SessionAcceptRequest, SessionRotateRequest
)
from app.adapters.postgres.models import A2ASession, A2ASessionEvent
from app.domain.a2a.canonical import canonical_json_bytes
from app.domain.a2a.utils import uuid7

logger = logging.getLogger(__name__)

# Config constants (Phase 10.1 mandatory)
A2A_SESSION_DEFAULT_TTL = 86400  # 24 hours

class A2ASessionManager:
    def __init__(self, db: Session):
        self.db = db

    def _advisory_lock(self, session_id: str):
        # Deterministic int64 hash for lock ID
        lock_id = int(hashlib.sha256(session_id.encode()).hexdigest()[:15], 16)
        self.db.execute(text("SELECT pg_advisory_xact_lock(:id)"), {"id": lock_id})

    def _append_event(self, session_id: str, event_type: str, actor_id: str, event_data: dict, prev_digest: Optional[str] = None) -> A2ASessionEvent:
        # Determine sequence
        last_event = self.db.query(A2ASessionEvent).filter(
            A2ASessionEvent.session_id == session_id
        ).order_by(A2ASessionEvent.seq.desc()).first()
        
        seq = (last_event.seq + 1) if last_event else 0
        actual_prev_digest = last_event.digest if last_event else None
        
        if prev_digest and prev_digest != actual_prev_digest:
            raise ValueError("Optimistic locking failure: prev_digest mismatch")

        # Construct event
        event_id = uuid7()
        ts = datetime.utcnow()
        
        # Prepare for hashing
        event_payload = {
            "schema_id": "talos.a2a.session_event",
            "schema_version": "v1",
            "event_id": event_id,
            "session_id": session_id,
            "actor_id": actor_id,
            "event_type": event_type,
            "ts": ts.isoformat() + "Z", # naive utc to iso string
            # event_digest excluded
        }
        
        # event_json in DB should probably store the full event for reconstruction.
        full_event = event_payload.copy()
        
        if prev_digest:
            full_event["previous_digest"] = prev_digest
            
        digest_bytes = canonical_json_bytes(full_event)
        digest = hashlib.sha256(digest_bytes).hexdigest()
        
        full_event["event_digest"] = digest
        
        event_obj = A2ASessionEvent(
            session_id=session_id,
            seq=seq,
            prev_digest=actual_prev_digest,
            digest=digest,
            event_json=full_event,
            ts=ts,
            actor_id=actor_id
        )
        self.db.add(event_obj)
        return event_obj

    def create_session(self, initiator_id: str, req: SessionCreateRequest) -> A2ASession:
        session_id = uuid7()
        
        expires_at = req.expires_at or (datetime.utcnow() + timedelta(seconds=A2A_SESSION_DEFAULT_TTL))
        
        # Create Projection
        session = A2ASession(
            session_id=session_id,
            state="pending",
            initiator_id=initiator_id,
            responder_id=req.responder_id,
            ratchet_state_blob=req.ratchet_state_blob_b64u,
            ratchet_state_digest=req.ratchet_state_digest,
            expires_at=expires_at
        )
        
        self.db.add(session)
        self.db.flush()
        
        # Append session_opened event
        self._append_event(session_id, "session_opened", initiator_id, {})
        
        return session

    def get_session(self, session_id: str) -> Optional[A2ASession]:
        return self.db.query(A2ASession).filter(A2ASession.session_id == session_id).first()

    def accept_session(self, session_id: str, responder_id: str, req: SessionAcceptRequest) -> A2ASession:
        self._advisory_lock(session_id)
        session = self.get_session(session_id)
        if not session:
            raise ValueError("Session not found")
            
        if session.responder_id != responder_id:
             raise PermissionError("Not the designated responder") # Or 403
             
        if session.state != "pending":
            raise ValueError(f"Invalid state transition: {session.state} -> active")
            
        if session.expires_at and session.expires_at < datetime.utcnow():
            raise ValueError("Session expired")

        # Update core state
        session.state = "active"
        session.ratchet_state_blob = req.ratchet_state_blob_b64u
        session.ratchet_state_digest = req.ratchet_state_digest
        
        # Append event
        self._append_event(session_id, "session_accepted", responder_id, {})
        
        return session

    def rotate_session(self, session_id: str, actor_id: str, req: SessionRotateRequest) -> A2ASession:
        self._advisory_lock(session_id)
        session = self.get_session(session_id)
        if not session:
             raise ValueError("Session not found")
             
        if actor_id not in [session.initiator_id, session.responder_id]:
            raise PermissionError("Not a session participant")

        if session.state != "active":
             raise ValueError(f"Invalid state transition: {session.state} -> rotate")

        if session.expires_at and session.expires_at < datetime.utcnow():
            raise ValueError("Session expired")

        session.ratchet_state_blob = req.ratchet_state_blob_b64u
        session.ratchet_state_digest = req.ratchet_state_digest
        
        self._append_event(session_id, "session_rotated", actor_id, {})
        return session

    def close_session(self, session_id: str, actor_id: str) -> A2ASession:
        self._advisory_lock(session_id)
        session = self.get_session(session_id)
        if not session:
             raise ValueError("Session not found")

        if actor_id not in [session.initiator_id, session.responder_id]:
             # Maybe admin override?
             # detailed in spec: "close valid from pending or active -> closed"
             raise PermissionError("Not a session participant")

        if session.state == "closed":
            return session

        session.state = "closed"
        self._append_event(session_id, "session_closed", actor_id, {})
        return session
