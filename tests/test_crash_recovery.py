"""Crash Recovery Integration Tests (Phase 9.3.5).

Tests crash simulation and recovery per LOCKED spec:
- Crash after tool_call persisted but before tool_effect
- Recovery re-dispatches and connector returns cached effect
- No double execution (idempotency enforced)
- Hash-chain validation end-to-end
"""
import pytest
from app.domain.tga.runtime import TgaRuntime, ExecutionPlan
from app.domain.tga.state_store import (
    TgaStateStore,
    ExecutionLogEntry,
    ExecutionStateEnum,
    ZERO_DIGEST,
)


class TestCrashRecoveryIntegration:
    """Integration tests for crash recovery scenarios."""

    @pytest.mark.asyncio
    async def test_crash_after_tool_call_recovery_re_dispatches(self):
        """
        Simulate crash after tool_call is persisted but before tool_effect.
        Recovery should indicate re-dispatch is needed.
        """
        store = TgaStateStore()
        runtime = TgaRuntime(store)
        trace_id = "01936a8b-4c2d-7000-8000-crash-test-001"
        
        # Manually create partial state (simulating crash mid-execution)
        # 1. Genesis entry
        e1 = _make_entry(trace_id, 1, ZERO_DIGEST, 
                        ExecutionStateEnum.PENDING, ExecutionStateEnum.PENDING,
                        "action_request", "ar-001")
        await store.append_log_entry(e1)
        
        # 2. PENDING -> AUTHORIZED
        e2 = _make_entry(trace_id, 2, e1.entry_digest,
                        ExecutionStateEnum.PENDING, ExecutionStateEnum.AUTHORIZED,
                        "supervisor_decision", "sd-001")
        await store.append_log_entry(e2)
        
        # 3. AUTHORIZED -> EXECUTING (but NO tool_effect - simulates crash)
        e3 = _make_entry(trace_id, 3, e2.entry_digest,
                        ExecutionStateEnum.AUTHORIZED, ExecutionStateEnum.EXECUTING,
                        "tool_call", "tc-001",
                        tool_call_id="tc-001", idempotency_key="idem-crash-001")
        await store.append_log_entry(e3)
        
        # State should be EXECUTING
        state = await store.load_state(trace_id)
        assert state.current_state == ExecutionStateEnum.EXECUTING
        
        # Recovery should detect incomplete and indicate re-dispatch
        recovery = await runtime.recover(trace_id)
        
        assert recovery.recovered_state == ExecutionStateEnum.EXECUTING
        assert recovery.re_dispatched is True
        assert recovery.recovered_from_seq == 3

    @pytest.mark.asyncio
    async def test_crash_after_tool_effect_no_re_dispatch(self):
        """
        If tool_effect is already persisted, recovery should NOT re-dispatch.
        """
        store = TgaStateStore()
        runtime = TgaRuntime(store)
        trace_id = "01936a8b-4c2d-7000-8000-crash-test-002"
        
        # Create complete execution (tool_effect exists)
        e1 = _make_entry(trace_id, 1, ZERO_DIGEST,
                        ExecutionStateEnum.PENDING, ExecutionStateEnum.PENDING,
                        "action_request", "ar-002")
        await store.append_log_entry(e1)
        
        e2 = _make_entry(trace_id, 2, e1.entry_digest,
                        ExecutionStateEnum.PENDING, ExecutionStateEnum.AUTHORIZED,
                        "supervisor_decision", "sd-002")
        await store.append_log_entry(e2)
        
        e3 = _make_entry(trace_id, 3, e2.entry_digest,
                        ExecutionStateEnum.AUTHORIZED, ExecutionStateEnum.EXECUTING,
                        "tool_call", "tc-002",
                        tool_call_id="tc-002", idempotency_key="idem-complete")
        await store.append_log_entry(e3)
        
        e4 = _make_entry(trace_id, 4, e3.entry_digest,
                        ExecutionStateEnum.EXECUTING, ExecutionStateEnum.COMPLETED,
                        "tool_effect", "te-002",
                        tool_call_id="tc-002", idempotency_key="idem-complete")
        await store.append_log_entry(e4)
        
        # Recovery should NOT re-dispatch
        recovery = await runtime.recover(trace_id)
        
        assert recovery.recovered_state == ExecutionStateEnum.COMPLETED
        assert recovery.re_dispatched is False

    @pytest.mark.asyncio
    async def test_hash_chain_validated_on_recovery(self):
        """
        Recovery must validate the entire hash chain.
        """
        store = TgaStateStore()
        runtime = TgaRuntime(store)
        trace_id = "01936a8b-4c2d-7000-8000-chain-test-001"
        
        # Execute plan to create valid chain
        plan = ExecutionPlan(
            trace_id=trace_id,
            plan_id="plan-chain-001",
            action_request={"intent": "test", "action_request_id": "ar-chain"}
        )
        await runtime.execute_plan(plan)
        
        # Recovery validates chain
        recovery = await runtime.recover(trace_id)
        
        assert recovery.recovered_state == ExecutionStateEnum.COMPLETED
        
        # Verify entries have valid chain
        entries = await store.list_log_entries(trace_id)
        for i, entry in enumerate(entries):
            if i == 0:
                assert entry.prev_entry_digest == ZERO_DIGEST
            else:
                assert entry.prev_entry_digest == entries[i-1].entry_digest

    @pytest.mark.asyncio
    async def test_idempotency_prevents_double_execution(self):
        """
        If tool_call is re-executed with same idempotency_key,
        connector should return cached effect (no actual execution).
        
        This test simulates the behavior - actual connector integration
        would use the IdempotencyCache.
        """
        store = TgaStateStore()
        runtime = TgaRuntime(store)
        
        execution_count = 0
        
        async def counting_dispatch(tool_call):
            nonlocal execution_count
            execution_count += 1
            return {"outcome": {"status": "SUCCESS"}, "tool_effect_id": "te-idem"}
        
        plan = ExecutionPlan(
            trace_id="01936a8b-4c2d-7000-8000-idem-test-001",
            plan_id="plan-idem-001",
            action_request={"intent": "test", "action_request_id": "ar-idem"},
            tool_dispatch_fn=counting_dispatch
        )
        
        # First execution
        result1 = await runtime.execute_plan(plan)
        assert result1.final_state == ExecutionStateEnum.COMPLETED
        assert execution_count == 1
        
        # Second execution should detect existing state and NOT re-dispatch
        result2 = await runtime.execute_plan(plan)
        assert result2.final_state == ExecutionStateEnum.COMPLETED
        # execution_count stays at 1 because plan is already complete
        assert execution_count == 1

    @pytest.mark.asyncio
    async def test_denied_execution_recovery(self):
        """Recovery from DENIED state should not re-dispatch."""
        store = TgaStateStore()
        runtime = TgaRuntime(store)
        trace_id = "01936a8b-4c2d-7000-8000-denied-test-001"
        
        # Create denied state
        e1 = _make_entry(trace_id, 1, ZERO_DIGEST,
                        ExecutionStateEnum.PENDING, ExecutionStateEnum.PENDING,
                        "action_request", "ar-denied")
        await store.append_log_entry(e1)
        
        e2 = _make_entry(trace_id, 2, e1.entry_digest,
                        ExecutionStateEnum.PENDING, ExecutionStateEnum.DENIED,
                        "supervisor_decision", "sd-denied")
        await store.append_log_entry(e2)
        
        # Recovery should recognize terminal state
        recovery = await runtime.recover(trace_id)
        
        assert recovery.recovered_state == ExecutionStateEnum.DENIED
        assert recovery.re_dispatched is False


def _make_entry(
    trace_id: str,
    seq: int,
    prev_digest: str,
    from_state: ExecutionStateEnum,
    to_state: ExecutionStateEnum,
    artifact_type: str,
    artifact_id: str,
    tool_call_id: str = None,
    idempotency_key: str = None
) -> ExecutionLogEntry:
    """Helper to create test log entries."""
    entry = ExecutionLogEntry(
        schema_id="talos.tga.execution_log_entry",
        schema_version="v1",
        trace_id=trace_id,
        sequence_number=seq,
        prev_entry_digest=prev_digest,
        entry_digest="",
        ts="2026-01-15T20:00:00.000Z",
        from_state=from_state,
        to_state=to_state,
        artifact_type=artifact_type,
        artifact_id=artifact_id,
        artifact_digest="a" * 64,
        tool_call_id=tool_call_id,
        idempotency_key=idempotency_key
    )
    entry.entry_digest = entry.compute_digest()
    return entry
