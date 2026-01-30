import logging
import hashlib
from datetime import datetime, timezone
from typing import Optional
from sqlalchemy.orm import Session
from sqlalchemy import text

from app.domain.a2a.models import (
    GroupCreateRequest, GroupMemberAddRequest
)
from app.adapters.postgres.models import A2AGroup, A2AGroupEvent
from app.domain.a2a.canonical import canonical_json_bytes
from app.domain.a2a.utils import uuid7

logger = logging.getLogger(__name__)

class A2AGroupManager:
    def __init__(self, write_db: Session, read_db: Optional[Session] = None):
        self.db = write_db
        self.read_db = read_db or write_db

    def _advisory_lock(self, group_id: str) -> None:
        """Acquire transaction-scoped advisory lock for single-writer concurrency.
        
        Uses pg_try_advisory_xact_lock for deterministic failure instead of blocking.
        Raises ValueError with A2A_LOCK_CONTENTION if lock cannot be acquired.
        """
        # Deterministic int64 hash for lock ID (use different prefix for groups)
        lock_id = int(hashlib.sha256(b"group:" + group_id.encode()).hexdigest()[:15], 16)
        result = self.db.execute(
            text("SELECT pg_try_advisory_xact_lock(:id) AS acquired"),
            {"id": lock_id}
        ).fetchone()
        if not result or not result.acquired:
            raise ValueError("A2A_LOCK_CONTENTION")

    def _append_event(self, group_id: str, event_type: str, actor_id: str, event_data: dict, target_id: Optional[str] = None, prev_digest: Optional[str] = None) -> A2AGroupEvent:
        last_event = self.db.query(A2AGroupEvent).filter(
            A2AGroupEvent.group_id == group_id
        ).order_by(A2AGroupEvent.seq.desc()).first()
        
        seq = (last_event.seq + 1) if last_event else 0
        actual_prev_digest = last_event.digest if last_event else None
        
        if prev_digest and prev_digest != actual_prev_digest:
            raise ValueError("Optimistic locking failure")

        event_id = uuid7()
        ts = datetime.now(timezone.utc)
        
        event_payload = {
            "schema_id": "talos.a2a.group_event",
            "schema_version": "v1",
            "event_id": event_id,
            "group_id": group_id,
            "actor_id": actor_id,
            "event_type": event_type,
            "ts": ts.isoformat() + "Z"
        }
        if target_id:
            event_payload["target_id"] = target_id
            
        full_event = event_payload.copy() # merge data if needed
        # event_data processing?
        
        if prev_digest:
            full_event["previous_digest"] = prev_digest
            
        digest_bytes = canonical_json_bytes(full_event)
        digest = hashlib.sha256(digest_bytes).hexdigest()
        full_event["event_digest"] = digest
        
        event_obj = A2AGroupEvent(
            group_id=group_id,
            seq=seq,
            prev_digest=actual_prev_digest,
            digest=digest,
            event_json=full_event,
            ts=ts,
            actor_id=actor_id,
            target_id=target_id
        )
        self.db.add(event_obj)
        return event_obj

    def create_group(self, owner_id: str, req: GroupCreateRequest) -> A2AGroup:
        group_id = uuid7()
        
        group = A2AGroup(
            group_id=group_id,
            owner_id=owner_id,
            state="active",
            created_at=datetime.now(timezone.utc)
        )
        self.db.add(group)
        self.db.flush()
        
        self._append_event(group_id, "group_created", owner_id, {"name": req.name})
        # Implicitly add owner as member? Usually yes.
        self._append_event(group_id, "member_added", owner_id, {}, target_id=owner_id)
        
        return group

    def get_group(self, group_id: str) -> Optional[A2AGroup]:
        return self.db.query(A2AGroup).filter(A2AGroup.group_id == group_id).first()

    def add_member(self, group_id: str, actor_id: str, req: GroupMemberAddRequest) -> A2AGroup:
        self._advisory_lock(group_id)
        group = self.get_group(group_id)
        if not group:
             raise ValueError("A2A_GROUP_NOT_FOUND")
        if group.state != "active":
             raise ValueError("A2A_GROUP_STATE_INVALID")
             
        # Check permission (only owner?)
        if actor_id != group.owner_id:
             raise PermissionError("A2A_MEMBER_NOT_ALLOWED")
             
        self._append_event(group_id, "member_added", actor_id, {}, target_id=req.member_id)
        return group
        
    def remove_member(self, group_id: str, actor_id: str, member_id: str) -> A2AGroup:
        self._advisory_lock(group_id)
        group = self.get_group(group_id)
        if not group:
             raise ValueError("A2A_GROUP_NOT_FOUND")
        if group.state != "active":
             raise ValueError("A2A_GROUP_STATE_INVALID")
             
        if actor_id != group.owner_id and actor_id != member_id:
             raise PermissionError("A2A_MEMBER_NOT_ALLOWED")
             
        self._append_event(group_id, "member_removed", actor_id, {}, target_id=member_id)
        return group

    def close_group(self, group_id: str, actor_id: str) -> Optional[A2AGroup]:
        self._advisory_lock(group_id)
        group = self.get_group(group_id)
        if not group:
            raise ValueError("A2A_GROUP_NOT_FOUND")
        
        if actor_id != group.owner_id:
             raise PermissionError("A2A_MEMBER_NOT_ALLOWED")
             
        group.state = "closed"  # type: ignore
        self._append_event(group_id, "group_closed", actor_id, {})
        return group
