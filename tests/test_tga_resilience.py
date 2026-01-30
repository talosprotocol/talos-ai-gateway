import pytest
import hashlib
import json
from unittest.mock import AsyncMock, MagicMock
from app.domain.tga.runtime import TgaRuntime, ExecutionPlan, ExecutionResult
from app.domain.tga.state_store import TgaStateStore, ExecutionState, ExecutionStateEnum, ZERO_DIGEST
from app.domain.mcp.tool_guard import ToolGuard, GuardPolicy, ToolClass

@pytest.fixture
def mock_store():
    store = MagicMock(spec=TgaStateStore)
    store.acquire_trace_lock = AsyncMock()
    store.release_trace_lock = AsyncMock()
    store.load_state = AsyncMock(return_value=None)
    store.append_log_entry = AsyncMock()
    return store

@pytest.fixture
def mock_guard():
    guard = MagicMock(spec=ToolGuard)
    guard.validate_call = AsyncMock(return_value=GuardPolicy(
        tool_server="test-server",
        tool_name="test-tool",
        tool_class=ToolClass.WRITE,
        requires_idempotency_key=True,
        read_replay_safe=False
    ))
    return guard

@pytest.mark.asyncio
async def test_tga_runtime_happy_path(mock_store, mock_guard):
    runtime = TgaRuntime(store=mock_store, tool_guard=mock_guard)
    
    plan = ExecutionPlan(
        trace_id="trace-123",
        plan_id="plan-456",
        tool_server="test-server",
        tool_name="test-tool",
        tool_args={"key": "value"},
        action_request={"user": "alice"},
        supervisor_decision_fn=AsyncMock(return_value={"approved": True}),
        tool_dispatch_fn=AsyncMock(return_value={"status": "success"})
    )
    
    # Mock compute_digest and generate_id to be deterministic for this test if needed
    runtime._compute_digest = MagicMock(return_value="hash")
    runtime._generate_id = MagicMock(return_value="id")
    runtime._make_entry = MagicMock(return_value=MagicMock(entry_digest="digest"))
    
    result = await runtime.execute_plan(plan)
    
    assert result.trace_id == "trace-123"
    assert result.final_state == ExecutionStateEnum.COMPLETED
    assert mock_store.append_log_entry.call_count >= 4 # Genesis, Decision, Executing, Completed

@pytest.mark.asyncio
async def test_tga_runtime_recovery_logic(mock_store, mock_guard):
    # Simulate a crash during EXECUTING state
    recovered_state = ExecutionState(
        trace_id="trace-123",
        schema_id="v1",
        schema_version="1.0",
        plan_id="plan-456",
        current_state=ExecutionStateEnum.EXECUTING,
        last_sequence_number=3,
        last_entry_digest="digest-3",
        state_digest="state-hash"
    )
    mock_store.load_state = AsyncMock(return_value=recovered_state)
    
    # Mock log entries for _resume_execution to find
    exec_entry = MagicMock(
        to_state=ExecutionStateEnum.EXECUTING,
        tool_call_id="tc-123",
        idempotency_key="key-123"
    )
    mock_store.list_log_entries = AsyncMock(return_value=[exec_entry])
    
    # Mock internal recovery methods
    runtime = TgaRuntime(store=mock_store, tool_guard=mock_guard)
    runtime._recover_impl = AsyncMock(return_value=MagicMock(
        recovered_state=ExecutionStateEnum.EXECUTING,
        tool_call_payload={"call": "..."}
    ))
    runtime._dispatch_and_complete = AsyncMock(return_value=ExecutionResult(
        trace_id="trace-123", final_state=ExecutionStateEnum.COMPLETED
    ))
    
    plan = ExecutionPlan(
        trace_id="trace-123",
        plan_id="plan-456",
        tool_server="test-server",
        tool_name="test-tool",
        tool_args={},
        action_request={}
    )
    
    result = await runtime.execute_plan(plan)
    
    assert result.final_state == ExecutionStateEnum.COMPLETED
    runtime._recover_impl.assert_called_once()
