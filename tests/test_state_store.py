"""Tests for TGA State Store (Phase 9.3.2)."""
import pytest
from app.domain.tga.state_store import (
    TgaStateStore,
    ExecutionLogEntry,
    ExecutionStateEnum,
    StateStoreError,
    ZERO_DIGEST,
)


def make_entry(
    trace_id: str,
    seq: int,
    from_state: ExecutionStateEnum,
    to_state: ExecutionStateEnum,
    prev_digest: str,
    artifact_type: str = "action_request"
) -> ExecutionLogEntry:
    """Helper to create test log entries."""
    entry = ExecutionLogEntry(
        schema_id="talos.tga.execution_log_entry",
        schema_version="v1",
        trace_id=trace_id,
        sequence_number=seq,
        prev_entry_digest=prev_digest,
        entry_digest="",  # Will be computed
        ts="2026-01-15T19:00:00.000Z",
        from_state=from_state,
        to_state=to_state,
        artifact_type=artifact_type,
        artifact_id="01936a8b-4c2d-7000-8000-000000000001",
        artifact_digest="a" * 64,
    )
    entry.entry_digest = entry.compute_digest()
    return entry


class TestTgaStateStore:
    """Test TgaStateStore behavior."""

    @pytest.mark.asyncio
    async def test_append_genesis_entry(self):
        """Genesis entry should append successfully."""
        store = TgaStateStore()
        entry = make_entry(
            trace_id="01936a8b-4c2d-7000-8000-000000000001",
            seq=1,
            from_state=ExecutionStateEnum.PENDING,
            to_state=ExecutionStateEnum.PENDING,
            prev_digest=ZERO_DIGEST
        )
        
        await store.append_log_entry(entry)
        
        entries = await store.list_log_entries(entry.trace_id)
        assert len(entries) == 1
        assert entries[0].sequence_number == 1

    @pytest.mark.asyncio
    async def test_append_valid_transition(self):
        """Valid PENDING->AUTHORIZED transition should succeed."""
        store = TgaStateStore()
        trace_id = "01936a8b-4c2d-7000-8000-000000000002"
        
        # Genesis
        e1 = make_entry(trace_id, 1, ExecutionStateEnum.PENDING, ExecutionStateEnum.PENDING, ZERO_DIGEST)
        await store.append_log_entry(e1)
        
        # PENDING -> AUTHORIZED
        e2 = make_entry(trace_id, 2, ExecutionStateEnum.PENDING, ExecutionStateEnum.AUTHORIZED, e1.entry_digest, "supervisor_decision")
        await store.append_log_entry(e2)
        
        state = await store.load_state(trace_id)
        assert state is not None
        assert state.current_state == ExecutionStateEnum.AUTHORIZED
        assert state.last_sequence_number == 2

    @pytest.mark.asyncio
    async def test_reject_invalid_transition(self):
        """Invalid PENDING->COMPLETED should be rejected."""
        store = TgaStateStore()
        trace_id = "01936a8b-4c2d-7000-8000-000000000003"
        
        # Genesis
        e1 = make_entry(trace_id, 1, ExecutionStateEnum.PENDING, ExecutionStateEnum.PENDING, ZERO_DIGEST)
        await store.append_log_entry(e1)
        
        # Invalid: PENDING -> COMPLETED
        e2 = make_entry(trace_id, 2, ExecutionStateEnum.PENDING, ExecutionStateEnum.COMPLETED, e1.entry_digest, "tool_effect")
        
        with pytest.raises(StateStoreError) as exc:
            await store.append_log_entry(e2)
        assert exc.value.code == "STATE_INVALID_TRANSITION"

    @pytest.mark.asyncio
    async def test_reject_sequence_gap(self):
        """Sequence gap should be rejected."""
        store = TgaStateStore()
        trace_id = "01936a8b-4c2d-7000-8000-000000000004"
        
        # Genesis
        e1 = make_entry(trace_id, 1, ExecutionStateEnum.PENDING, ExecutionStateEnum.PENDING, ZERO_DIGEST)
        await store.append_log_entry(e1)
        
        # Skip sequence 2
        e3 = make_entry(trace_id, 3, ExecutionStateEnum.PENDING, ExecutionStateEnum.AUTHORIZED, e1.entry_digest, "supervisor_decision")
        
        with pytest.raises(StateStoreError) as exc:
            await store.append_log_entry(e3)
        assert exc.value.code == "STATE_SEQUENCE_GAP"

    @pytest.mark.asyncio
    async def test_reject_broken_hash_chain(self):
        """Broken hash chain should be rejected."""
        store = TgaStateStore()
        trace_id = "01936a8b-4c2d-7000-8000-000000000005"
        
        # Genesis
        e1 = make_entry(trace_id, 1, ExecutionStateEnum.PENDING, ExecutionStateEnum.PENDING, ZERO_DIGEST)
        await store.append_log_entry(e1)
        
        # Wrong prev_entry_digest
        e2 = make_entry(trace_id, 2, ExecutionStateEnum.PENDING, ExecutionStateEnum.AUTHORIZED, "wrong" + "0" * 59, "supervisor_decision")
        
        with pytest.raises(StateStoreError) as exc:
            await store.append_log_entry(e2)
        assert exc.value.code == "STATE_CHECKSUM_MISMATCH"

    @pytest.mark.asyncio
    async def test_full_execution_flow(self):
        """Complete PENDING->AUTHORIZED->EXECUTING->COMPLETED flow."""
        store = TgaStateStore()
        trace_id = "01936a8b-4c2d-7000-8000-000000000006"
        
        # 1. Genesis (PENDING)
        e1 = make_entry(trace_id, 1, ExecutionStateEnum.PENDING, ExecutionStateEnum.PENDING, ZERO_DIGEST, "action_request")
        await store.append_log_entry(e1)
        
        # 2. PENDING -> AUTHORIZED
        e2 = make_entry(trace_id, 2, ExecutionStateEnum.PENDING, ExecutionStateEnum.AUTHORIZED, e1.entry_digest, "supervisor_decision")
        await store.append_log_entry(e2)
        
        # 3. AUTHORIZED -> EXECUTING
        e3 = make_entry(trace_id, 3, ExecutionStateEnum.AUTHORIZED, ExecutionStateEnum.EXECUTING, e2.entry_digest, "tool_call")
        await store.append_log_entry(e3)
        
        # 4. EXECUTING -> COMPLETED
        e4 = make_entry(trace_id, 4, ExecutionStateEnum.EXECUTING, ExecutionStateEnum.COMPLETED, e3.entry_digest, "tool_effect")
        await store.append_log_entry(e4)
        
        state = await store.load_state(trace_id)
        assert state is not None
        assert state.current_state == ExecutionStateEnum.COMPLETED
        assert state.last_sequence_number == 4
        
        entries = await store.list_log_entries(trace_id)
        assert len(entries) == 4
