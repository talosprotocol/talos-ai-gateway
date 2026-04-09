"""TGA State Store implementation using the standalone TGA Protocol."""
import asyncio
import hashlib
import json
import logging
import base64
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional
import uuid

from talos_governance_agent.ports.state_store import TgaStateStore as ITgaStateStore
from talos_governance_agent.domain.models import (
    ExecutionLogEntry,
    ExecutionState,
    ExecutionCheckpoint,
    ExecutionStateEnum,
    ArtifactType,
)

logger = logging.getLogger(__name__)

class StateStoreError(Exception):
    """Base exception for state store errors."""
    def __init__(self, message: str, code: str):
        super().__init__(message)
        self.code = code

# Constants
ZERO_DIGEST = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"

# Allowed transitions (Moore machine)
ALLOWED_TRANSITIONS = {
    (ExecutionStateEnum.PENDING, ExecutionStateEnum.AUTHORIZED),
    (ExecutionStateEnum.PENDING, ExecutionStateEnum.DENIED),
    (ExecutionStateEnum.AUTHORIZED, ExecutionStateEnum.EXECUTING),
    (ExecutionStateEnum.EXECUTING, ExecutionStateEnum.COMPLETED),
    (ExecutionStateEnum.EXECUTING, ExecutionStateEnum.FAILED),
}

class TgaStateStore(ITgaStateStore):
    """
    In-memory implementation of TGA state store for Gateway use.
    """
    
    def __init__(self):
        self._log_entries: Dict[str, List[ExecutionLogEntry]] = {}
        self._checkpoints: Dict[str, List[ExecutionCheckpoint]] = {}
        self._locks: Dict[str, asyncio.Lock] = {}
        self._states: Dict[str, ExecutionState] = {}
        self._sessions: Dict[str, Dict[str, Any]] = {}
    
    async def acquire_trace_lock(self, trace_id: str) -> None:
        if trace_id not in self._locks:
            self._locks[trace_id] = asyncio.Lock()
        
        if self._locks[trace_id].locked():
            raise StateStoreError(f"Lock held: {trace_id}", "STATE_LOCK_ACQUIRE_FAILED")
        await self._locks[trace_id].acquire()
    
    async def release_trace_lock(self, trace_id: str) -> None:
        if trace_id in self._locks and self._locks[trace_id].locked():
            self._locks[trace_id].release()
    
    async def load_state(self, trace_id: str) -> Optional[ExecutionState]:
        return self._states.get(trace_id)
    
    async def append_log_entry(self, entry: ExecutionLogEntry) -> None:
        trace_id = entry.trace_id
        if trace_id not in self._log_entries:
            self._log_entries[trace_id] = []
        
        entries = self._log_entries[trace_id]
        expected_seq = len(entries) + 1
        if entry.sequence_number != expected_seq:
            raise StateStoreError(f"Seq gap: {expected_seq} != {entry.sequence_number}", "STATE_SEQUENCE_GAP")
        
        if entries:
            if entry.prev_entry_digest != entries[-1].entry_digest:
                raise StateStoreError("Hash chain broken", "STATE_CHECKSUM_MISMATCH")
        elif entry.prev_entry_digest != ZERO_DIGEST:
            raise StateStoreError("Genesis digest invalid", "STATE_CHECKSUM_MISMATCH")
        
        # Transition validation
        if not (entry.from_state == ExecutionStateEnum.PENDING and entry.to_state == ExecutionStateEnum.PENDING):
            if (entry.from_state, entry.to_state) not in ALLOWED_TRANSITIONS:
                raise StateStoreError(f"Invalid transition {entry.from_state}->{entry.to_state}", "STATE_INVALID_TRANSITION")
        
        computed = entry.compute_digest()
        if entry.entry_digest != computed:
            raise StateStoreError(f"Digest mismatch: {computed} != {entry.entry_digest}", "STATE_CHECKSUM_MISMATCH")
        
        entries.append(entry)
        await self._update_state(trace_id, entry)
    
    async def _update_state(self, trace_id: str, entry: ExecutionLogEntry) -> None:
        state = self._states.get(trace_id)
        if state is None:
            state = ExecutionState(
                trace_id=trace_id,
                plan_id=entry.artifact_id, # First artifact
                current_state=entry.to_state,
                last_sequence_number=entry.sequence_number,
                last_entry_digest=entry.entry_digest,
                state_digest=ZERO_DIGEST
            )
        else:
            state.current_state = entry.to_state
            state.last_sequence_number = entry.sequence_number
            state.last_entry_digest = entry.entry_digest
        
        state.state_digest = state.compute_digest()
        self._states[trace_id] = state
    
    async def list_log_entries(self, trace_id: str, after_seq: int = 0) -> List[ExecutionLogEntry]:
        return [e for e in self._log_entries.get(trace_id, []) if e.sequence_number > after_seq]
    
    async def write_checkpoint(self, checkpoint: ExecutionCheckpoint) -> None:
        trace_id = checkpoint.trace_id
        if trace_id not in self._checkpoints:
            self._checkpoints[trace_id] = []
        self._checkpoints[trace_id].append(checkpoint)
    
    async def load_latest_checkpoint(self, trace_id: str) -> Optional[ExecutionCheckpoint]:
        checkpoints = self._checkpoints.get(trace_id, [])
        return checkpoints[-1] if checkpoints else None

    async def put_session(self, session: Dict[str, Any]) -> None:
        self._sessions[session["session_id"]] = session

    async def get_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        return self._sessions.get(session_id)

    async def touch_session(self, session_id: str, now: str) -> None:
        if session_id in self._sessions:
            self._sessions[session_id]["last_seen_at"] = now

    async def delete_expired_sessions(self, now: str) -> int:
        count = 0
        to_del = []
        for sid, sess in self._sessions.items():
            if sess["expires_at"] < now:
                to_del.append(sid)
        for sid in to_del:
            del self._sessions[sid]
            count += 1
        return count

# Singleton instance
_store_instance: Optional[TgaStateStore] = None

def get_state_store() -> TgaStateStore:
    global _store_instance
    if _store_instance is None:
        _store_instance = TgaStateStore()
    return _store_instance
