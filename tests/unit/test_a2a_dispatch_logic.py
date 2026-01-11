import pytest
from unittest.mock import MagicMock
from app.domain.a2a.dispatcher import A2ADispatcher

def test_sanitize_request_meta():
    # Setup minimal mock dispatcher (dependencies not needed for this pure function if we access properly)
    # But A2A dispatcher __init__ requires args.
    # We can just test the method if we mock the instance or extract the method.
    # Since it's an instance method, let's mock the dependencies.
    
    dispatcher = A2ADispatcher(
        auth=MagicMock(), routing_service=MagicMock(), audit_store=MagicMock(),
        rl_store=MagicMock(), usage_store=MagicMock(), task_store=MagicMock(), mcp_client=MagicMock()
    )
    
    # Test Allowlist
    meta = {
        "method": "tasks.send",
        "model_group_id": "gpt-4",
        "profile_id": "default",
        "profile_version": "0.1",
        "extra_unknown": "value",
        "tool_name": "weather",
        "origin_surface": "a2a"
    }
    cleaned = dispatcher._sanitize_request_meta(meta)
    assert cleaned == {
        "method": "tasks.send",
        "model_group_id": "gpt-4",
        "profile_id": "default",
        "profile_version": "0.1",
        "tool_name": "weather", 
        "origin_surface": "a2a"
    }
    assert "extra_unknown" not in cleaned

    # Test Forbidden Keys (Deep Scan - though current impl is shallow key check)
    forbidden = ["messages", "prompt", "input", "tool_input", "headers", "authorization", "api_key", "secret", "cookie"]
    
    dirty_meta = {
        "method": "tasks.send",
        **{k: "secret" for k in forbidden}
    }
    
    cleaned_dirty = dispatcher._sanitize_request_meta(dirty_meta)
    assert cleaned_dirty == {"method": "tasks.send"}
    
    for k in forbidden:
        assert k not in cleaned_dirty
