from typing import List, Optional
import json
from sqlalchemy import text, select, func
from sqlalchemy.orm import Session
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.domain.tga.state_store import (
    TgaStateStore,
    ExecutionLogEntry,
    ExecutionState,
    ExecutionCheckpoint,
    ExecutionStateEnum,
    StateStoreError
)
from app.adapters.postgres.models import TgaTrace, TgaLog

class PostgresTgaStateStore(TgaStateStore):
    """
    PostgreSQL persistence for TGA State Store.
    
    Uses row-level locking (SELECT FOR UPDATE) on tga_traces table
    to ensure single-writer serialization per trace.
    """
    
    def __init__(self, session: Session):
        super().__init__()
        self.session = session
    
    async def acquire_trace_lock(self, trace_id: str) -> None:
        """
        Acquire lock for trace.
        Ensures trace row exists, then locks it.
        """
        try:
            # 1. Upsert trace row
            stmt = pg_insert(TgaTrace).values(
                trace_id=trace_id,
                created_at=func.now(),
                updated_at=func.now()
            ).on_conflict_do_nothing()
            self.session.execute(stmt)
            
            # 2. Lock row
            self.session.execute(
                select(TgaTrace.trace_id)
                .where(TgaTrace.trace_id == (trace_id)) # type: ignore
                .with_for_update()
            )
        except Exception as e:
            raise StateStoreError(f"Failed to acquire lock: {e}", "STATE_LOCK_ACQUIRE_FAILED")

    async def release_trace_lock(self, trace_id: str) -> None:
        """Release lock by committing transaction."""
        try:
            self.session.commit()
        except Exception as e:
            self.session.rollback()
            raise StateStoreError(f"Failed to commit transaction: {e}", "DB_COMMIT_ERROR")

    async def load_state(self, trace_id: str) -> Optional[ExecutionState]:
        """Load current execution state for a trace."""
        trace = self.session.query(TgaTrace).filter(TgaTrace.trace_id == (trace_id)).first() # type: ignore
        if not trace or not trace.current_state:
            return None
        
        return ExecutionState(
            schema_id=trace.schema_id,
            schema_version=trace.schema_version,
            trace_id=trace.trace_id,
            plan_id=trace.plan_id,
            current_state=ExecutionStateEnum(trace.current_state),
            last_sequence_number=trace.last_sequence_number,
            last_entry_digest=trace.last_entry_digest,
            state_digest=trace.state_digest
        )

    async def append_log_entry(self, entry: ExecutionLogEntry) -> None:
        """
        Append a log entry with validation and atomic trace state update.
        """
        trace_id = entry.trace_id
        
        # Load current state for validation
        current_state = await self.load_state(trace_id)
        
        # 1. Sequence validation
        expected_seq = (current_state.last_sequence_number + 1) if current_state else 1
        if entry.sequence_number != expected_seq:
             raise StateStoreError(
                f"Sequence gap: expected {expected_seq}, got {entry.sequence_number}",
                "STATE_SEQUENCE_GAP"
            )

        # 2. Hash chain validation
        if current_state:
            if entry.prev_entry_digest != current_state.last_entry_digest:
                 raise StateStoreError(
                    f"Hash chain broken at sequence {entry.sequence_number}",
                    "STATE_CHECKSUM_MISMATCH"
                )
        else:
             from app.domain.tga.state_store import ZERO_DIGEST
             if entry.prev_entry_digest != ZERO_DIGEST:
                 raise StateStoreError(
                    "Genesis entry must have zero prev_entry_digest",
                    "STATE_CHECKSUM_MISMATCH"
                )
        
        # 3. Entry digest validation
        computed = entry.compute_digest()
        if entry.entry_digest != computed:
             raise StateStoreError(
                f"Entry digest mismatch: expected {computed}",
                "STATE_CHECKSUM_MISMATCH"
            )
            
        # 4. Insert Log Entry
        log_entry = TgaLog(
            trace_id=entry.trace_id,
            sequence_number=entry.sequence_number,
            entry_digest=entry.entry_digest,
            prev_entry_digest=entry.prev_entry_digest,
            ts=entry.ts,
            from_state=entry.from_state.value if isinstance(entry.from_state, ExecutionStateEnum) else entry.from_state,
            to_state=entry.to_state.value if isinstance(entry.to_state, ExecutionStateEnum) else entry.to_state,
            artifact_type=entry.artifact_type,
            artifact_id=entry.artifact_id,
            artifact_digest=entry.artifact_digest,
            tool_call_id=entry.tool_call_id,
            idempotency_key=entry.idempotency_key,
            artifact_payload=entry.artifact_payload,
            schema_id=entry.schema_id,
            schema_version=entry.schema_version
        )
        self.session.add(log_entry)
        
        # 5. Update Trace State
        trace = self.session.query(TgaTrace).filter(TgaTrace.trace_id == trace_id).first()
        if not trace:
            # Should be created by acquire_trace_lock, but fallback
            trace = TgaTrace(trace_id=trace_id)
            self.session.add(trace)

        if entry.sequence_number == 1:
            trace.plan_id = entry.artifact_id
        
        trace.schema_id = "talos.tga.execution_state"
        trace.schema_version = "v1"
        trace.current_state = entry.to_state.value if isinstance(entry.to_state, ExecutionStateEnum) else entry.to_state
        trace.last_sequence_number = entry.sequence_number
        trace.last_entry_digest = entry.entry_digest
        
        # Compute derived state digest
        derived_state = ExecutionState(
            schema_id=trace.schema_id,
            schema_version=trace.schema_version,
            trace_id=trace.trace_id,
            plan_id=trace.plan_id,
            current_state=ExecutionStateEnum(trace.current_state),
            last_sequence_number=trace.last_sequence_number,
            last_entry_digest=trace.last_entry_digest,
            state_digest=""
        )
        trace.state_digest = derived_state.compute_digest()
        
        # Flush to catch constraints
        self.session.flush()

    async def list_log_entries(
        self, 
        trace_id: str, 
        after_seq: int = 0
    ) -> List[ExecutionLogEntry]:
        """List log entries."""
        logs = (
            self.session.query(TgaLog)
            .filter(TgaLog.trace_id == trace_id)
            .filter(TgaLog.sequence_number > after_seq)
            .order_by(TgaLog.sequence_number.asc())
            .all()
        )
        
        return [
            ExecutionLogEntry(
                schema_id=l.schema_id,
                schema_version=l.schema_version,
                trace_id=l.trace_id,
                sequence_number=l.sequence_number,
                prev_entry_digest=l.prev_entry_digest,
                entry_digest=l.entry_digest,
                ts=l.ts,
                from_state=ExecutionStateEnum(l.from_state),
                to_state=ExecutionStateEnum(l.to_state),
                artifact_type=l.artifact_type,
                artifact_id=l.artifact_id,
                artifact_digest=l.artifact_digest,
                tool_call_id=l.tool_call_id,
                idempotency_key=l.idempotency_key,
                artifact_payload=l.artifact_payload
            )
            for l in logs
        ]
