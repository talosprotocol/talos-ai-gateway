"""Unit tests for PostgresTgaStateStore."""
import pytest
from unittest.mock import MagicMock, AsyncMock, call
from app.adapters.postgres.tga_store import PostgresTgaStateStore
from app.domain.tga.state_store import ExecutionLogEntry, ExecutionStateEnum

def test_append_log_entry_sql():
    """Verify append_log_entry generates correct INSERT with payload."""
    mock_session = MagicMock()
    store = PostgresTgaStateStore(mock_session)
    
    # Mock load_state response for sequence validation
    # store.load_state is async
    store.load_state = AsyncMock(return_value=None)
    
    # store.list_log_entries is async
    store.list_log_entries = AsyncMock(return_value=[])
    
    entry = ExecutionLogEntry(
        schema_id="v1", schema_version="v1", trace_id="t1", sequence_number=1,
        prev_entry_digest="0"*64, entry_digest="", ts="now",
        from_state=ExecutionStateEnum.PENDING, to_state=ExecutionStateEnum.PENDING,
        artifact_type="action_request", artifact_id="ar1", artifact_digest="d1",
        artifact_payload={"foo": "bar"}
    )
    entry.entry_digest = entry.compute_digest()
    
    # Run
    import asyncio
    asyncio.run(store.append_log_entry(entry))
    
    # Verify execute calls
    # We expect 2 executes: INSERT logs, UPDATE traces
    # Check INSERT logs
    # args match roughly insert(tga_logs).values(...)
    # Since we use text(), execute is called with text object and params.
    
    calls = mock_session.execute.call_args_list
    
    # Find INSERT
    insert_call = next((c for c in calls if "INSERT INTO tga_logs" in str(c[0][0])), None)
    assert insert_call is not None
    
    params = insert_call[0][1]
    assert params["trace_id"] == "t1"
    assert params["artifact_payload"] == '{"foo": "bar"}' # JSON serialized
    
    print("TEST PASSED: test_append_log_entry_sql")

if __name__ == "__main__":
    try:
        test_append_log_entry_sql()
    except Exception as e:
        print(f"TEST FAILED: {e}")
        import traceback
        traceback.print_exc()
        exit(1)
