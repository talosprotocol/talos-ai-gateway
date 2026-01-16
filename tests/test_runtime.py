"""Tests for TGA Runtime Loop (Phase 9.3.4)."""
import pytest
from app.domain.tga.runtime import (
    TgaRuntime,
    ExecutionPlan,
    ExecutionResult,
    RecoveryResult,
)
from app.domain.tga.state_store import (
    TgaStateStore,
    ExecutionStateEnum,
)


class TestTgaRuntime:
    """Test TgaRuntime behavior."""

    @pytest.mark.asyncio
    async def test_execute_plan_approved(self):
        """Approved plan should reach COMPLETED state."""
        store = TgaStateStore()
        runtime = TgaRuntime(store)
        
        plan = ExecutionPlan(
            trace_id="01936a8b-4c2d-7000-8000-000000000001",
            plan_id="01936a8b-4c2d-7000-8000-000000000010",
            action_request={"intent": "test", "action_request_id": "ar-001"}
        )
        
        result = await runtime.execute_plan(plan)
        
        assert result.final_state == ExecutionStateEnum.COMPLETED
        assert result.tool_effect is not None

    @pytest.mark.asyncio
    async def test_execute_plan_denied(self):
        """Denied plan should reach DENIED state."""
        store = TgaStateStore()
        runtime = TgaRuntime(store)
        
        async def deny_decision(ar):
            return {"approved": False, "decision_id": "sd-deny-001"}
        
        plan = ExecutionPlan(
            trace_id="01936a8b-4c2d-7000-8000-000000000002",
            plan_id="01936a8b-4c2d-7000-8000-000000000020",
            action_request={"intent": "risky", "action_request_id": "ar-002"},
            supervisor_decision_fn=deny_decision
        )
        
        result = await runtime.execute_plan(plan)
        
        assert result.final_state == ExecutionStateEnum.DENIED
        assert result.error == "Supervisor denied the action"

    @pytest.mark.asyncio
    async def test_execute_plan_creates_log_entries(self):
        """Execute should create 4 log entries for full flow."""
        store = TgaStateStore()
        runtime = TgaRuntime(store)
        
        plan = ExecutionPlan(
            trace_id="01936a8b-4c2d-7000-8000-000000000003",
            plan_id="01936a8b-4c2d-7000-8000-000000000030",
            action_request={"intent": "test", "action_request_id": "ar-003"}
        )
        
        await runtime.execute_plan(plan)
        
        entries = await store.list_log_entries(plan.trace_id)
        assert len(entries) == 4
        assert entries[0].artifact_type == "action_request"
        assert entries[1].artifact_type == "supervisor_decision"
        assert entries[2].artifact_type == "tool_call"
        assert entries[3].artifact_type == "tool_effect"

    @pytest.mark.asyncio
    async def test_recover_terminal_state(self):
        """Recovery from terminal state should not re-dispatch."""
        store = TgaStateStore()
        runtime = TgaRuntime(store)
        
        # Execute first
        plan = ExecutionPlan(
            trace_id="01936a8b-4c2d-7000-8000-000000000004",
            plan_id="01936a8b-4c2d-7000-8000-000000000040",
            action_request={"intent": "test", "action_request_id": "ar-004"}
        )
        await runtime.execute_plan(plan)
        
        # Now recover
        recovery = await runtime.recover(plan.trace_id)
        
        assert recovery.recovered_state == ExecutionStateEnum.COMPLETED
        assert recovery.re_dispatched is False

    @pytest.mark.asyncio
    async def test_resume_completed_execution(self):
        """Re-executing completed plan should return existing result."""
        store = TgaStateStore()
        runtime = TgaRuntime(store)
        
        plan = ExecutionPlan(
            trace_id="01936a8b-4c2d-7000-8000-000000000005",
            plan_id="01936a8b-4c2d-7000-8000-000000000050",
            action_request={"intent": "test", "action_request_id": "ar-005"}
        )
        
        # First execution
        result1 = await runtime.execute_plan(plan)
        assert result1.final_state == ExecutionStateEnum.COMPLETED
        
        # Second execution should detect existing state
        result2 = await runtime.execute_plan(plan)
        assert result2.final_state == ExecutionStateEnum.COMPLETED
