"""Tests for TGA Runtime Recovery with Payload (Phase 9.3)."""
print("DEBUG: Starting test script execution...")
# import pytest
from unittest.mock import MagicMock, AsyncMock
from app.domain.tga.runtime import (
    TgaRuntime,
    ExecutionPlan,
    ExecutionResult,
    ExecutionStateEnum
)
from app.domain.tga.state_store import TgaStateStore, ExecutionLogEntry, ZERO_DIGEST
from app.domain.mcp.classifier import ToolClassification
from datetime import datetime

class TestTgaRecovery:
    """Test TgaRuntime recovery logic."""

    # @pytest.mark.asyncio
    async def test_recovery_redispatch_with_payload(self):
        """Recovery should use stored payload to re-dispatch tool."""
        store = TgaStateStore()
        runtime = TgaRuntime(store)
        
        trace_id = "trace-recovery-001"
        plan_id = "plan-recovery-001"
        
        # 1. Manually populate store with "crashed" state (EXECUTING)
        # Sequence:
        # 1. Action Request
        # 2. Supervisor Auth
        # 3. Tool Call (EXECUTING) -> Crash here
        
        # Helper to bypass runtime logic for setup
        await store.acquire_trace_lock(trace_id)
        
        # Gen entry 1
        e1 = ExecutionLogEntry(
            schema_id="v1", schema_version="v1", trace_id=trace_id, sequence_number=1,
            prev_entry_digest=ZERO_DIGEST, entry_digest="", ts="now",
            from_state=ExecutionStateEnum.PENDING, to_state=ExecutionStateEnum.PENDING,
            artifact_type="action_request", artifact_id="ar-1", artifact_digest="d1",
            artifact_payload={"intent": "test"}
        )
        e1.entry_digest = e1.compute_digest()
        await store.append_log_entry(e1)
        
        # Gen entry 2
        e2 = ExecutionLogEntry(
            schema_id="v1", schema_version="v1", trace_id=trace_id, sequence_number=2,
            prev_entry_digest=e1.entry_digest, entry_digest="", ts="now",
            from_state=ExecutionStateEnum.PENDING, to_state=ExecutionStateEnum.AUTHORIZED,
            artifact_type="supervisor_decision", artifact_id="sd-1", artifact_digest="d2",
            artifact_payload={"approved": True}
        )
        e2.entry_digest = e2.compute_digest()
        await store.append_log_entry(e2)
        
        # Gen entry 3 (Tool Call)
        tool_payload = {
            "call": {"name": "server:tool", "arguments": {"x": 1}},
            "idempotency_key": "idem-1"
        }
        e3 = ExecutionLogEntry(
            schema_id="v1", schema_version="v1", trace_id=trace_id, sequence_number=3,
            prev_entry_digest=e2.entry_digest, entry_digest="", ts="now",
            from_state=ExecutionStateEnum.AUTHORIZED, to_state=ExecutionStateEnum.EXECUTING,
            artifact_type="tool_call", artifact_id="tc-1", artifact_digest="d3",
            tool_call_id="tc-1", idempotency_key="idem-1",
            artifact_payload=tool_payload
        )
        e3.entry_digest = e3.compute_digest()
        await store.append_log_entry(e3)
        
        await store.release_trace_lock(trace_id)
        
        # 2. Verify Recovery
        # Create a plan that matches
        plan = ExecutionPlan(
            trace_id=trace_id,
            plan_id=plan_id,
            action_request={"intent": "test"},
            tool_dispatch_fn=AsyncMock(return_value={"outcome": {"status": "SUCCESS"}})
        )
        
        # This should trigger _resume_execution -> recover -> re-dispatch
        result = await runtime.execute_plan(plan)
        
        assert result.final_state == ExecutionStateEnum.COMPLETED
        
        # Verify tool_dispatch_fn was called with original payload
        plan.tool_dispatch_fn.assert_called_once()
        call_arg = plan.tool_dispatch_fn.call_args[0][0]
        assert call_arg == tool_payload
        
        # Verify log has 4 entries now
        entries = await store.list_log_entries(trace_id)
        assert len(entries) == 4
        assert entries[-1].to_state == ExecutionStateEnum.COMPLETED

if __name__ == "__main__":
    import asyncio
    try:
        t = TestTgaRecovery()
        asyncio.run(t.test_recovery_redispatch_with_payload())
        print("TEST PASSED: test_recovery_redispatch_with_payload")
    except Exception as e:
        print(f"TEST FAILED: {e}")
        import traceback
        traceback.print_exc()
        exit(1)

