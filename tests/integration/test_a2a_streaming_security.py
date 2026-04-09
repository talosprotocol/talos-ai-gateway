from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from app.adapters.memory_store.stores import MemoryTaskStore, _TASK_STATE
from app.api.a2a.jsonrpc import JsonRpcException
from app.dependencies import (
    get_audit_store,
    get_mcp_client,
    get_rate_limit_store,
    get_routing_service,
    get_task_store,
    get_usage_store,
)
from app.domain.a2a.streaming import stream_task_events
from app.main import app
from app.middleware.auth_public import AuthContext, get_auth_context
from app.settings import settings


client = TestClient(app)
AUTH_HEADERS = {"Authorization": "Bearer sk-test-key"}


@pytest.fixture(autouse=True)
def reset_state():
    original_mode = settings.a2a_protocol_mode
    app.dependency_overrides = {}
    _TASK_STATE.clear()
    yield
    settings.a2a_protocol_mode = original_mode
    app.dependency_overrides = {}
    _TASK_STATE.clear()


@pytest.fixture
def memory_task_store():
    store = MemoryTaskStore()
    app.dependency_overrides[get_task_store] = lambda: store
    app.dependency_overrides[get_audit_store] = lambda: MagicMock()
    app.dependency_overrides[get_rate_limit_store] = lambda: MagicMock()
    app.dependency_overrides[get_routing_service] = lambda: MagicMock()
    app.dependency_overrides[get_usage_store] = lambda: MagicMock()
    app.dependency_overrides[get_mcp_client] = lambda: MagicMock()
    return store


def _make_auth(scopes: list[str]) -> AuthContext:
    return AuthContext(
        key_id="key-123",
        team_id="team-1",
        org_id="org-1",
        scopes=scopes,
        allowed_model_groups=["*"],
        allowed_mcp_servers=["*"],
    )


def _running_task(task_id: str, *, team_id: str) -> dict:
    now = datetime.now(timezone.utc)
    return {
        "id": task_id,
        "team_id": team_id,
        "key_id": "key-123",
        "org_id": "org-1",
        "request_id": f"req-{task_id}",
        "origin_surface": "a2a_v1",
        "method": "tasks.send",
        "status": "running",
        "version": 1,
        "request_meta": {"context_id": f"ctx-{task_id}", "origin_surface": "a2a_v1"},
        "input_redacted": None,
        "result": None,
        "error": None,
        "created_at": now,
        "updated_at": now,
    }


def test_subscribe_to_task_requires_auth(memory_task_store):
    settings.a2a_protocol_mode = "v1"

    response = client.post(
        "/rpc",
        json={
            "jsonrpc": "2.0",
            "id": "req-no-auth",
            "method": "SubscribeToTask",
            "params": {"id": "task-1"},
        },
    )

    assert response.status_code == 401
    assert response.json()["detail"] == "Missing Authorization header"


def test_subscribe_to_task_rejects_invalid_auth_format(memory_task_store):
    settings.a2a_protocol_mode = "v1"

    response = client.post(
        "/rpc",
        json={
            "jsonrpc": "2.0",
            "id": "req-bad-auth",
            "method": "SubscribeToTask",
            "params": {"id": "task-1"},
        },
        headers={"Authorization": "Token nope"},
    )

    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid Authorization format"


def test_subscribe_to_task_requires_subscribe_scope(memory_task_store):
    settings.a2a_protocol_mode = "v1"
    _TASK_STATE["task-subscribe"] = _running_task("task-subscribe", team_id="team-1")
    app.dependency_overrides[get_auth_context] = lambda: _make_auth(["a2a.get"])

    response = client.post(
        "/rpc",
        json={
            "jsonrpc": "2.0",
            "id": "req-subscribe",
            "method": "SubscribeToTask",
            "params": {"id": "task-subscribe"},
        },
        headers=AUTH_HEADERS,
    )

    assert response.status_code == 200, response.text
    error = response.json()["error"]
    assert error["message"] == "Permission denied"
    assert error["data"]["talos_code"] == "RBAC_DENIED"
    assert error["data"]["operation"] == "SubscribeToTask"


@pytest.mark.asyncio
async def test_stream_generator_cross_team_logic():
    task_store = MemoryTaskStore()
    task_store.create_task(_running_task("task-team-2", team_id="team-2"))

    generator = stream_task_events(
        task_id="task-team-2",
        team_id="team-1",
        task_store=task_store,
        redis_client=MagicMock(),
        request_id="req-cross-team",
    )

    with pytest.raises(JsonRpcException) as exc:
        await anext(generator)

    assert exc.value.code == -32000
    assert "Task not found" in exc.value.message
