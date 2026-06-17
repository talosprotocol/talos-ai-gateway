"""Unit tests for PostgresTgaStateStore."""
from unittest.mock import MagicMock, AsyncMock
from app.adapters.postgres.tga_store import PostgresTgaStateStore
from app.domain.tga.state_store import ExecutionLogEntry, ExecutionStateEnum, ZERO_DIGEST

def test_append_log_entry_sql():
    """Verify append_log_entry generates correct INSERT with payload."""
    mock_session = MagicMock()
    store = PostgresTgaStateStore(mock_session)
    
    # Mock load_state response for sequence validation
    # store.load_state is async
    store.load_state = AsyncMock(return_value=None)
    
    # store.list_log_entries is async
    store.list_log_entries = AsyncMock(return_value=[])
    mock_session.query.return_value.filter.return_value.first.return_value = None
    
    trace_uuid = "0191b7d5-1111-7111-8111-111111111111"
    principal_uuid = "0191b7d5-0000-7000-8000-000000000000"
    artifact_uuid = "0191b7d5-2222-7222-8222-222222222222"
    
    entry = ExecutionLogEntry(
        schema_id="v1",
        schema_version="v1",
        trace_id=trace_uuid,
        principal_id=principal_uuid,
        sequence_number=1,
        prev_entry_digest=ZERO_DIGEST,
        entry_digest="pending-digest",
        ts="2026-06-17T22:14:30.000Z",
        from_state=ExecutionStateEnum.PENDING,
        to_state=ExecutionStateEnum.PENDING,
        artifact_type="action_request",
        artifact_id=artifact_uuid,
        artifact_digest="d1",
    )
    object.__setattr__(entry, "artifact_payload", {"foo": "bar"})
    entry.entry_digest = entry.compute_digest()
    
    # Run
    import asyncio
    asyncio.run(store.append_log_entry(entry))
    
    # Verify session calls
    # We expect mock_session.add to be called with TgaLog and TgaTrace.
    from app.adapters.postgres.models import TgaLog, TgaTrace
    
    added_objects = [args[0] for args, kwargs in mock_session.add.call_args_list]
    log_entry = next((o for o in added_objects if isinstance(o, TgaLog)), None)
    trace_entry = next((o for o in added_objects if isinstance(o, TgaTrace)), None)
    
    assert log_entry is not None
    assert log_entry.trace_id == trace_uuid
    assert log_entry.artifact_payload == {"foo": "bar"}
    
    assert trace_entry is not None
    assert trace_entry.trace_id == trace_uuid
    assert trace_entry.plan_id == artifact_uuid
    
    print("TEST PASSED: test_append_log_entry_sql")

if __name__ == "__main__":
    try:
        test_append_log_entry_sql()
    except Exception as e:
        print(f"TEST FAILED: {e}")
        import traceback
        traceback.print_exc()
        exit(1)
