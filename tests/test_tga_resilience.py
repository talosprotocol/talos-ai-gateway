import pytest
from unittest.mock import AsyncMock, MagicMock
from app.domain.tga.runtime import TgaRuntime, ExecutionPlan, ExecutionResult
from app.domain.tga.state_store import TgaStateStore, ExecutionState, ExecutionStateEnum
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
    
    trace_uuid = "0191b7d5-1111-7111-8111-111111111111"
    plan_uuid = "0191b7d5-2222-7222-8222-222222222222"
    
    plan = ExecutionPlan(
        trace_id=trace_uuid,
        plan_id=plan_uuid,
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
    
    recovered_state = ExecutionState(
        trace_id=trace_uuid,
        schema_id="v1",
        schema_version="1.0",
        plan_id=plan_uuid,
        current_state=ExecutionStateEnum.EXECUTING,
        last_sequence_number=3,
        last_entry_digest="digest-3",
        state_digest="state-hash"
    )
    mock_store.load_state = AsyncMock(side_effect=[None, recovered_state])
    mock_store.list_log_entries = AsyncMock(return_value=[MagicMock(
        principal_id="0191b7d5-0000-7000-8000-000000000000",
        entry_digest="digest-3"
    )])
    
    result = await runtime.execute_plan(plan)
    
    assert result.trace_id == trace_uuid
    assert result.final_state == ExecutionStateEnum.COMPLETED
    assert mock_store.append_log_entry.call_count >= 4 # Genesis, Decision, Executing, Completed

@pytest.mark.asyncio
async def test_tga_runtime_recovery_logic(mock_store, mock_guard):
    trace_uuid = "0191b7d5-1111-7111-8111-111111111111"
    plan_uuid = "0191b7d5-2222-7222-8222-222222222222"
    
    # Simulate a crash during EXECUTING state
    recovered_state = ExecutionState(
        trace_id=trace_uuid,
        schema_id="v1",
        schema_version="1.0",
        plan_id=plan_uuid,
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
        idempotency_key="0191b7d5-3333-7333-8333-333333333333",
        principal_id="0191b7d5-0000-7000-8000-000000000000"
    )
    mock_store.list_log_entries = AsyncMock(return_value=[exec_entry])
    
    runtime = TgaRuntime(store=mock_store, tool_guard=mock_guard)
    
    dispatch_mock = AsyncMock(return_value={"outcome": {"status": "SUCCESS"}})
    plan = ExecutionPlan(
        trace_id=trace_uuid,
        plan_id=plan_uuid,
        tool_server="test-server",
        tool_name="test-tool",
        tool_args={},
        action_request={},
        tool_dispatch_fn=dispatch_mock
    )
    
    result = await runtime.execute_plan(plan)
    
    assert result.final_state == ExecutionStateEnum.COMPLETED
    dispatch_mock.assert_called_once()
