from unittest.mock import ANY, AsyncMock, MagicMock, patch

import pytest

from app.adapters.upstreams_ai.client import UpstreamRateLimitError, UpstreamTransportError
from app.api.a2a.jsonrpc import JsonRpcException
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


@pytest.mark.asyncio
async def test_handle_send_uses_configured_default_model_group_for_wildcard_access():
    auth = MagicMock()
    auth.scopes = ["a2a.send", "llm.invoke"]
    auth.allowed_model_groups = ["*"]
    auth.team_id = "team-1"
    auth.key_id = "key-1"
    auth.org_id = "org-1"

    routing = MagicMock()
    routing.default_model_group_id.return_value = "llama3"
    routing.select_upstream.return_value = {
        "upstream": {"endpoint": "http://mock", "id": "u1"},
        "model_name": "llama3.2:1b",
    }

    rate_limit_result = MagicMock()
    rate_limit_result.allowed = True
    rl_store = MagicMock()
    rl_store.check_limit = AsyncMock(return_value=rate_limit_result)

    task_store = MagicMock()
    task_store.update_task_status.side_effect = [2, 3]

    dispatcher = A2ADispatcher(
        auth=auth,
        routing_service=routing,
        audit_store=MagicMock(),
        rl_store=rl_store,
        usage_store=MagicMock(),
        task_store=task_store,
        mcp_client=MagicMock(),
    )
    dispatcher._publish_event = AsyncMock()

    with patch(
        "app.domain.a2a.dispatcher.invoke_openai_compatible",
        new=AsyncMock(
            return_value={
                "choices": [{"message": {"content": "Hello from configured default"}}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1},
            }
        ),
    ):
        result = await dispatcher.handle_send(
            {
                "profile": {
                    "profile_id": "a2a-compat",
                    "profile_version": "0.1",
                    "spec_source": "a2a-protocol",
                },
                "input": [{"role": "user", "content": [{"text": "hi"}]}],
            },
            request_id="req-1",
        )

    routing.default_model_group_id.assert_called_once_with()
    routing.select_upstream.assert_called_once_with("llama3", ANY)
    assert result["status"] == "completed"


@pytest.mark.asyncio
async def test_handle_send_maps_upstream_rate_limit_to_specific_talos_code():
    auth = MagicMock()
    auth.scopes = ["a2a.send", "llm.invoke"]
    auth.allowed_model_groups = ["*"]
    auth.team_id = "team-1"
    auth.key_id = "key-1"
    auth.org_id = "org-1"

    routing = MagicMock()
    routing.default_model_group_id.return_value = "llama3"
    routing.select_upstream.return_value = {
        "upstream": {"endpoint": "http://mock", "id": "u1"},
        "model_name": "llama3.2:1b",
    }

    rate_limit_result = MagicMock()
    rate_limit_result.allowed = True
    rl_store = MagicMock()
    rl_store.check_limit = AsyncMock(return_value=rate_limit_result)

    task_store = MagicMock()
    task_store.update_task_status.side_effect = [2, 3]

    dispatcher = A2ADispatcher(
        auth=auth,
        routing_service=routing,
        audit_store=MagicMock(),
        rl_store=rl_store,
        usage_store=MagicMock(),
        task_store=task_store,
        mcp_client=MagicMock(),
    )
    dispatcher._publish_event = AsyncMock()

    with patch(
        "app.domain.a2a.dispatcher.invoke_openai_compatible",
        new=AsyncMock(
            side_effect=UpstreamRateLimitError(
                "Upstream rate limited",
                request_id="req-upstream-429",
                status_code=429,
                retry_after_seconds=2.5,
            )
        ),
    ), patch("app.domain.a2a.dispatcher._simulated_llm_enabled", return_value=False), patch("app.domain.a2a.dispatcher._dev_mode_enabled", return_value=False):
        with pytest.raises(JsonRpcException) as exc:
            await dispatcher.handle_send(
                {
                    "profile": {
                        "profile_id": "a2a-compat",
                        "profile_version": "0.1",
                        "spec_source": "a2a-protocol",
                    },
                    "input": [{"role": "user", "content": [{"text": "hi"}]}],
                },
                request_id="req-2",
            )

    assert exc.value.data["talos_code"] == "UPSTREAM_RATE_LIMITED"
    assert exc.value.message == "Upstream rate limited"
    assert exc.value.data["upstream_request_id"] == "req-upstream-429"
    assert exc.value.data["upstream_status_code"] == 429
    assert exc.value.data["retry_after_ms"] == 2500


@pytest.mark.asyncio
async def test_handle_send_maps_upstream_transport_failures_to_specific_talos_code():
    auth = MagicMock()
    auth.scopes = ["a2a.send", "llm.invoke"]
    auth.allowed_model_groups = ["*"]
    auth.team_id = "team-1"
    auth.key_id = "key-1"
    auth.org_id = "org-1"

    routing = MagicMock()
    routing.default_model_group_id.return_value = "llama3"
    routing.select_upstream.return_value = {
        "upstream": {"endpoint": "http://mock", "id": "u1"},
        "model_name": "llama3.2:1b",
    }

    rate_limit_result = MagicMock()
    rate_limit_result.allowed = True
    rl_store = MagicMock()
    rl_store.check_limit = AsyncMock(return_value=rate_limit_result)

    task_store = MagicMock()
    task_store.update_task_status.side_effect = [2, 3]

    dispatcher = A2ADispatcher(
        auth=auth,
        routing_service=routing,
        audit_store=MagicMock(),
        rl_store=rl_store,
        usage_store=MagicMock(),
        task_store=task_store,
        mcp_client=MagicMock(),
    )
    dispatcher._publish_event = AsyncMock()

    with patch(
        "app.domain.a2a.dispatcher.invoke_openai_compatible",
        new=AsyncMock(
            side_effect=UpstreamTransportError(
                "Upstream request timed out",
                request_id="req-upstream-timeout",
            )
        ),
    ), patch("app.domain.a2a.dispatcher._simulated_llm_enabled", return_value=False), patch("app.domain.a2a.dispatcher._dev_mode_enabled", return_value=False):
        with pytest.raises(JsonRpcException) as exc:
            await dispatcher.handle_send(
                {
                    "profile": {
                        "profile_id": "a2a-compat",
                        "profile_version": "0.1",
                        "spec_source": "a2a-protocol",
                    },
                    "input": [{"role": "user", "content": [{"text": "hi"}]}],
                },
                request_id="req-3",
            )

    assert exc.value.data["talos_code"] == "UPSTREAM_TRANSPORT_ERROR"
    assert exc.value.message == "Upstream transport error"
    assert exc.value.data["upstream_request_id"] == "req-upstream-timeout"
