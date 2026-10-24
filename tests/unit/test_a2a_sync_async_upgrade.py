"""Tests for the sync-to-async upgrade in the ai-gateway A2ADispatcher.

Covers:
  1. Fast task resolves synchronously (within sync window)
  2. Slow task promotes to async when sync window expires
  3. Execution ceiling kills long tasks → FAILED / TASK_EXECUTION_TIMEOUT
  4. returnImmediately=true bypasses sync window entirely
  5. Client polls tasks.get after async upgrade and sees COMPLETED
  6. Existing rate-limit / upstream-error paths are unaffected (regression)
"""
from __future__ import annotations

import asyncio
from unittest.mock import ANY, AsyncMock, MagicMock, patch

import pytest

from app.api.a2a.jsonrpc import JsonRpcException
from app.domain.a2a.dispatcher import A2ADispatcher


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


def _make_auth(scopes=("a2a.send", "llm.invoke"), model_groups=("*",)):
    auth = MagicMock()
    auth.scopes = list(scopes)
    auth.allowed_model_groups = list(model_groups)
    auth.team_id = "team-test"
    auth.key_id = "key-test"
    auth.org_id = "org-test"
    auth.principal_id = "principal-test"
    return auth


def _make_routing(model_group="llama3", model_name="llama3.2:1b"):
    routing = MagicMock()
    routing.default_model_group_id.return_value = model_group
    routing.select_upstream.return_value = {
        "upstream": {"endpoint": "http://mock-llm", "id": "u1", "provider": "openai"},
        "model_name": model_name,
    }
    return routing


def _make_rl_store(allowed=True):
    rl_result = MagicMock()
    rl_result.allowed = allowed
    store = MagicMock()
    store.check_limit = AsyncMock(return_value=rl_result)
    return store


def _make_task_store(get_task_side_effect=None):
    store = MagicMock()
    # update_task_status returns incrementing version numbers
    store.update_task_status.side_effect = [2, 3, 4, 5, 6]
    if get_task_side_effect:
        store.get_task.side_effect = get_task_side_effect
    return store


def _make_dispatcher(task_store=None, rl_store=None, routing=None, auth=None):
    return A2ADispatcher(
        auth=auth or _make_auth(),
        routing_service=routing or _make_routing(),
        audit_store=MagicMock(),
        rl_store=rl_store or _make_rl_store(),
        usage_store=MagicMock(),
        task_store=task_store or _make_task_store(),
        mcp_client=MagicMock(),
    )


_FAST_LLM_RESPONSE = {
    "choices": [{"message": {"content": "Hello!"}}],
    "usage": {"prompt_tokens": 5, "completion_tokens": 5},
}

_BASE_PARAMS = {
    "profile": {
        "profile_id": "a2a-compat",
        "profile_version": "0.1",
        "spec_source": "a2a-protocol",
    },
    "input": [{"role": "user", "content": [{"text": "hi"}]}],
}


# ---------------------------------------------------------------------------
# Test 1: Fast task resolves synchronously within sync window
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fast_task_resolves_synchronously():
    """LLM completes quickly → sync response with no async flag."""
    dispatcher = _make_dispatcher()
    dispatcher._publish_event = AsyncMock()

    params = {
        **_BASE_PARAMS,
        "configuration": {"syncTimeoutSeconds": 5.0, "executionTimeoutSeconds": 30.0},
    }

    with patch(
        "app.domain.a2a.dispatcher.invoke_openai_compatible",
        new=AsyncMock(return_value=_FAST_LLM_RESPONSE),
    ), patch("app.domain.a2a.dispatcher._simulated_llm_enabled", return_value=False), \
       patch("app.domain.a2a.dispatcher._dev_mode_enabled", return_value=False):
        result = await dispatcher.handle_send(params, request_id="req-fast")

    assert result["status"] == "completed"
    assert result.get("async") is None, "async flag must not be set for sync completions"
    assert "task_id" in result


# ---------------------------------------------------------------------------
# Test 2: Slow task upgrades to async when sync window expires
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_slow_task_upgrades_to_async():
    """LLM takes longer than syncTimeoutSeconds → async/polling response."""
    dispatcher = _make_dispatcher()
    dispatcher._publish_event = AsyncMock()

    params = {
        **_BASE_PARAMS,
        # 50 ms sync window; LLM mock sleeps 300 ms
        "configuration": {"syncTimeoutSeconds": 0.05, "executionTimeoutSeconds": 10.0},
    }

    async def slow_llm(**kwargs):
        await asyncio.sleep(0.3)
        return _FAST_LLM_RESPONSE

    with patch(
        "app.domain.a2a.dispatcher.invoke_openai_compatible",
        new=AsyncMock(side_effect=slow_llm),
    ), patch("app.domain.a2a.dispatcher._simulated_llm_enabled", return_value=False), \
       patch("app.domain.a2a.dispatcher._dev_mode_enabled", return_value=False):
        result = await dispatcher.handle_send(params, request_id="req-slow")

    assert result.get("async") is True, "async flag must be True when sync window expires"
    assert result["status"] == "running"
    assert "pollIntervalMs" in result
    assert "executionTimeoutSeconds" in result
    assert "expiresAt" in result

    # Let the background work complete cleanly
    await asyncio.sleep(0.5)


# ---------------------------------------------------------------------------
# Test 3: Execution ceiling kills task → FAILED / TASK_EXECUTION_TIMEOUT
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_execution_ceiling_marks_task_failed():
    """Handler never finishes → background task times out → store updated to failed.

    Strategy: patch _execute_task_work directly so it always sleeps, bypassing
    the dev-mode / simulated-LLM short-circuit that resolves without calling
    invoke_openai_compatible at all.
    """
    task_store = _make_task_store()
    dispatcher = _make_dispatcher(task_store=task_store)
    dispatcher._publish_event = AsyncMock()

    params = {
        **_BASE_PARAMS,
        # overflow sync window in 50 ms; kill in 200 ms
        "configuration": {"syncTimeoutSeconds": 0.05, "executionTimeoutSeconds": 0.2},
    }

    async def never_returns(*args, **kwargs):
        await asyncio.sleep(9999)
        return {}

    # Patch _execute_task_work on the instance so both the sync-window coroutine
    # and the background coroutine use the slow version regardless of env flags.
    dispatcher._execute_task_work = never_returns  # type: ignore[method-assign]

    result = await dispatcher.handle_send(params, request_id="req-timeout")

    assert result.get("async") is True

    # Wait for the execution ceiling to fire
    await asyncio.sleep(0.5)

    # Assert store was told to mark the task failed with the timeout code
    timeout_calls = [
        call for call in task_store.update_task_status.call_args_list
        if "error" in call.kwargs
        and isinstance(call.kwargs["error"], dict)
        and call.kwargs["error"].get("talos_code") == "TASK_EXECUTION_TIMEOUT"
    ]
    assert len(timeout_calls) >= 1, (
        f"Expected update_task_status to be called with TASK_EXECUTION_TIMEOUT, "
        f"calls were: {task_store.update_task_status.call_args_list}"
    )


# ---------------------------------------------------------------------------
# Test 4: returnImmediately=true bypasses sync window
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_return_immediately_bypasses_sync_window():
    """returnImmediately=true returns a running snapshot immediately, no wait."""
    dispatcher = _make_dispatcher()
    dispatcher._publish_event = AsyncMock()

    params = {
        **_BASE_PARAMS,
        "configuration": {
            "returnImmediately": True,
            "syncTimeoutSeconds": 0.01,  # would overflow immediately anyway
            "executionTimeoutSeconds": 10.0,
        },
    }

    async def fast_llm(**kwargs):
        return _FAST_LLM_RESPONSE

    with patch(
        "app.domain.a2a.dispatcher.invoke_openai_compatible",
        new=AsyncMock(side_effect=fast_llm),
    ), patch("app.domain.a2a.dispatcher._simulated_llm_enabled", return_value=False), \
       patch("app.domain.a2a.dispatcher._dev_mode_enabled", return_value=False):
        result = await dispatcher.handle_send(params, request_id="req-immediate")

    # Must return immediately with running snapshot + async hints
    assert result["status"] == "running"
    assert result.get("async") is True
    assert "task_id" in result
    assert "pollIntervalMs" in result
    assert "expiresAt" in result

    # Let background work settle
    await asyncio.sleep(0.2)


# ---------------------------------------------------------------------------
# Test 5: Client polls tasks.get and eventually sees completed
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_client_polls_get_after_async_upgrade():
    """After async upgrade, repeated tasks.get calls eventually return completed."""
    # We need a real in-memory store to test the full lifecycle.
    # Use the MemoryTaskStore if available, else a minimal dict-backed mock.
    try:
        from app.adapters.memory_store.stores import MemoryTaskStore, _TASK_STATE
        _TASK_STATE.clear()
        real_store = MemoryTaskStore()
        use_real_store = True
    except ImportError:
        use_real_store = False

    if not use_real_store:
        pytest.skip("MemoryTaskStore not available; skipping integration poll test")
        return

    dispatcher = A2ADispatcher(
        auth=_make_auth(),
        routing_service=_make_routing(),
        audit_store=MagicMock(),
        rl_store=_make_rl_store(),
        usage_store=MagicMock(),
        task_store=real_store,
        mcp_client=MagicMock(),
    )
    dispatcher._publish_event = AsyncMock()

    params = {
        **_BASE_PARAMS,
        # sync window = 50 ms; LLM finishes in 300 ms
        "configuration": {"syncTimeoutSeconds": 0.05, "executionTimeoutSeconds": 10.0},
    }

    async def delayed_llm(**kwargs):
        await asyncio.sleep(0.3)
        return _FAST_LLM_RESPONSE

    with patch(
        "app.domain.a2a.dispatcher.invoke_openai_compatible",
        new=AsyncMock(side_effect=delayed_llm),
    ), patch("app.domain.a2a.dispatcher._simulated_llm_enabled", return_value=False), \
       patch("app.domain.a2a.dispatcher._dev_mode_enabled", return_value=False):
        result = await dispatcher.handle_send(params, request_id="req-poll")

    assert result.get("async") is True
    task_id = result["task_id"]

    # Poll until completed or bail after 10 attempts
    final_status = None
    for _ in range(10):
        await asyncio.sleep(0.1)
        task_row = real_store.get_task(task_id, "team-test")
        if task_row and task_row["status"] == "completed":
            final_status = "completed"
            break

    assert final_status == "completed", (
        f"Expected task to be completed after polling, got: {final_status}"
    )


# ---------------------------------------------------------------------------
# Test 6: Existing rate-limit path is unaffected (regression)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rate_limited_request_still_fails():
    """Rate-limited requests still raise JsonRpcException with RATE_LIMITED code."""
    dispatcher = _make_dispatcher(rl_store=_make_rl_store(allowed=False))
    dispatcher._publish_event = AsyncMock()

    with pytest.raises(JsonRpcException) as exc_info:
        await dispatcher.handle_send(_BASE_PARAMS, request_id="req-rl")

    assert exc_info.value.data["talos_code"] == "RATE_LIMITED"
