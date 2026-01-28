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

class PostgresTgaStateStore(TgaStateStore):
    """
    PostgreSQL persistence for TGA State Store.
    
    Uses row-level locking (SELECT FOR UPDATE) on tga_traces table
    to ensure single-writer serialization per trace.
    """
    
    def __init__(self, session: Session):
        super().__init__()
        self.session = session
        # We don't use the in-memory structures from base class
    
    async def acquire_trace_lock(self, trace_id: str) -> None:
        """
        Acquire lock for trace.
        Ensures trace row exists, then locks it.
        """
        # Ensure trace exists
        # We use a raw generic insert to avoid importing specific models here if possible,
        # or we could define a minimal table definition.
        # For pure SQL via text:
        
        # 1. Upsert trace row to ensure it exists
        try:
           self.session.execute(
               text("""
                   INSERT INTO tga_traces (trace_id, created_at, updated_at) 
                   VALUES (:trace_id, NOW(), NOW())
                   ON CONFLICT (trace_id) DO NOTHING
               """),
               {"trace_id": trace_id}
           )
           
           # 2. Lock the row
           # NOWAIT? Or wait? Default wait is fine.
           self.session.execute(
               text("""
                   SELECT trace_id FROM tga_traces 
                   WHERE trace_id = :trace_id 
                   FOR UPDATE
               """),
               {"trace_id": trace_id}
           )
           # We must NOT commit here, as the lock needs to be held for the duration of the transaction.
           # The caller (TgaRuntime) is responsible for transaction scope?
           # TgaRuntime currently manages logical locking. 
           # If TgaRuntime calls this, it expects a lock.
           # BUT, SQLAlchemy Session usage usually assumes a transaction is open.
           # Since TgaRuntime.execute_plan has a finally block that calls release_trace_lock...
           # In Postgres, lock is released on Commit/Rollback.
           # So release_trace_lock should commit.
           
        except Exception as e:
           raise StateStoreError(f"Failed to acquire lock: {e}", "DB_LOCK_ERROR")

    async def release_trace_lock(self, trace_id: str) -> None:
        """Release lock by committing transaction."""
        try:
            self.session.commit()
        except Exception as e:
            self.session.rollback()
            raise StateStoreError(f"Failed to commit transaction: {e}", "DB_COMMIT_ERROR")

    async def load_state(self, trace_id: str) -> Optional[ExecutionState]:
        """Load execution state from tga_traces table."""
        # We assume tga_traces has the state columns
        result = self.session.execute(
            text("""
                SELECT 
                    schema_id, schema_version, trace_id, plan_id, 
                    current_state, last_sequence_number, last_entry_digest, state_digest
                FROM tga_traces
                WHERE trace_id = :trace_id AND current_state IS NOT NULL
            """),
            {"trace_id": trace_id}
        ).fetchone()
        
        if not result:
            return None
            
        return ExecutionState(
            schema_id=result.schema_id,
            schema_version=result.schema_version,
            trace_id=result.trace_id,
            plan_id=result.plan_id,
            current_state=ExecutionStateEnum(result.current_state),
            last_sequence_number=result.last_sequence_number,
            last_entry_digest=result.last_entry_digest,
            state_digest=result.state_digest
        )

    async def append_log_entry(self, entry: ExecutionLogEntry) -> None:
        """Append log entry and update state atomically."""
        
        # 1. Base validation (sequence, hash chain) happens in base class logic?
        # The base class method `append_log_entry` does validation AND appends to memory.
        # We should replicate the validation logic or call a reusable validator.
        # Since we are overriding, we must validate ourselves or refactor base.
        # Let's run validation against DB state.
        
        # Determine expected sequence
        current_state = await self.load_state(entry.trace_id)
        entries = await self.list_log_entries(entry.trace_id)
        
        # Validate Sequence
        expected_seq = (current_state.last_sequence_number + 1) if current_state else 1
        if entry.sequence_number != expected_seq:
             raise StateStoreError(
                f"Sequence gap: expected {expected_seq}, got {entry.sequence_number}",
                "STATE_SEQUENCE_GAP"
            )

        # Validate Prev Hash
        if current_state:
            if entry.prev_entry_digest != current_state.last_entry_digest:
                 raise StateStoreError(
                    f"Hash chain broken at sequence {entry.sequence_number}",
                    "STATE_CHECKSUM_MISMATCH"
                )
        else:
             if entry.prev_entry_digest != ("0" * 64): # ZERO_DIGEST
                 raise StateStoreError(
                    "Genesis entry must have zero prev_entry_digest",
                    "STATE_CHECKSUM_MISMATCH"
                )
        
        # Computed digest check
        computed = entry.compute_digest()
        if entry.entry_digest != computed:
             raise StateStoreError(
                f"Entry digest mismatch: expected {computed}",
                "STATE_CHECKSUM_MISMATCH"
            )
            
        # 2. Insert Log Entry
        self.session.execute(
            text("""
                INSERT INTO tga_logs (
                    trace_id, sequence_number, entry_digest, prev_entry_digest,
                    ts, from_state, to_state, artifact_type, artifact_id, artifact_digest,
                    tool_call_id, idempotency_key, artifact_payload,
                    schema_id, schema_version
                ) VALUES (
                    :trace_id, :sequence_number, :entry_digest, :prev_entry_digest,
                    :ts, :from_state, :to_state, :artifact_type, :artifact_id, :artifact_digest,
                    :tool_call_id, :idempotency_key, :artifact_payload,
                    :schema_id, :schema_version
                )
            """),
            {
                "trace_id": entry.trace_id,
                "sequence_number": entry.sequence_number,
                "entry_digest": entry.entry_digest,
                "prev_entry_digest": entry.prev_entry_digest,
                "ts": entry.ts,
                # Enums to strings
                "from_state": entry.from_state.value if isinstance(entry.from_state, ExecutionStateEnum) else entry.from_state,
                "to_state": entry.to_state.value if isinstance(entry.to_state, ExecutionStateEnum) else entry.to_state,
                "artifact_type": entry.artifact_type,
                "artifact_id": entry.artifact_id,
                "artifact_digest": entry.artifact_digest,
                "tool_call_id": entry.tool_call_id,
                "idempotency_key": entry.idempotency_key,
                # JSON serialization for JSONB
                "artifact_payload": json.dumps(entry.artifact_payload) if entry.artifact_payload else None,
                "schema_id": entry.schema_id,
                "schema_version": entry.schema_version
            }
        )
        
        # 3. Update State in tga_traces
        # Compute new state digest
        if entry.sequence_number == 1:
            plan_id = entry.artifact_id
        else:
            # Should be guaranteed by sequence check, but for type safety:
            if current_state is None:
                raise StateStoreError("State missing for non-genesis entry", "STATE_INTERNAL_ERROR")
            plan_id = current_state.plan_id
            
        new_state = ExecutionState(
            schema_id="talos.tga.execution_state",
            schema_version="v1",
            trace_id=entry.trace_id,
            plan_id=plan_id,
            current_state=entry.to_state,
            last_sequence_number=entry.sequence_number,
            last_entry_digest=entry.entry_digest,
            state_digest=""
        )
        new_state.state_digest = new_state.compute_digest()
        
        self.session.execute(
            text("""
                UPDATE tga_traces SET
                    schema_id = :schema_id,
                    schema_version = :schema_version,
                    plan_id = :plan_id,
                    current_state = :current_state,
                    last_sequence_number = :last_sequence_number,
                    last_entry_digest = :last_entry_digest,
                    state_digest = :state_digest,
                    updated_at = NOW()
                WHERE trace_id = :trace_id
            """),
            {
                "schema_id": new_state.schema_id,
                "schema_version": new_state.schema_version,
                "plan_id": new_state.plan_id,
                "current_state": new_state.current_state.value,
                "last_sequence_number": new_state.last_sequence_number,
                "last_entry_digest": new_state.last_entry_digest,
                "state_digest": new_state.state_digest,
                "trace_id": entry.trace_id
            }
        )
        
        # Flush to catch constraints? 
        # No, wait for commit in release_trace_lock or explicit.
        # But for list_log_entries inside same txn to work, flush might be needed if using ORM.
        # We are using core execute, so it's in the txn.

    async def list_log_entries(
        self, 
        trace_id: str, 
        after_seq: int = 0
    ) -> List[ExecutionLogEntry]:
        """List log entries."""
        rows = self.session.execute(
            text("""
                SELECT 
                    schema_id, schema_version, trace_id, sequence_number, 
                    prev_entry_digest, entry_digest, ts, from_state, to_state,
                    artifact_type, artifact_id, artifact_digest, 
                    tool_call_id, idempotency_key, artifact_payload
                FROM tga_logs
                WHERE trace_id = :trace_id AND sequence_number > :after_seq
                ORDER BY sequence_number ASC
            """),
            {"trace_id": trace_id, "after_seq": after_seq}
        ).fetchall()
        
        entries = []
        for r in rows:
            entries.append(ExecutionLogEntry(
                schema_id=r.schema_id,
                schema_version=r.schema_version,
                trace_id=r.trace_id,
                sequence_number=r.sequence_number,
                prev_entry_digest=r.prev_entry_digest,
                entry_digest=r.entry_digest,
                ts=r.ts,
                from_state=ExecutionStateEnum(r.from_state),
                to_state=ExecutionStateEnum(r.to_state),
                artifact_type=r.artifact_type,
                artifact_id=r.artifact_id,
                artifact_digest=r.artifact_digest,
                tool_call_id=r.tool_call_id,
                idempotency_key=r.idempotency_key,
                artifact_payload=r.artifact_payload # SQLAlchemy should handle JSONB auto-decoding
            ))
        return entries
