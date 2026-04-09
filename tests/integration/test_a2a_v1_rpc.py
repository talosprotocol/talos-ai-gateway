import json
import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.adapters.upstreams_ai.client import UpstreamRateLimitError
from app.dependencies import (
    get_audit_store,
    get_rate_limit_store,
    get_routing_service,
    get_usage_store,
)
from app.main import app
from app.middleware.auth_public import get_auth_context
from app.settings import settings
from app.adapters.memory_store.stores import _TASK_STATE


client = TestClient(app)
AUTH_HEADERS = {"Authorization": "Bearer sk-test-key"}
V1_OPERATION_SCOPES = [
    "a2a.discovery.read",
    "a2a.send",
    "a2a.get",
    "a2a.list",
    "a2a.cancel",
    "a2a.subscribe",
    "a2a.push_config.read",
    "a2a.push_config.write",
    "llm.invoke",
]


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
def mock_auth_context():
    return make_auth_context(V1_OPERATION_SCOPES)


def make_auth_context(scopes):
    auth_ctx = MagicMock()
    auth_ctx.key_id = "key-123"
    auth_ctx.team_id = "team-1"
    auth_ctx.org_id = "org-1"
    auth_ctx.scopes = scopes
    auth_ctx.allowed_model_groups = ["*"]
    auth_ctx.allowed_mcp_servers = ["*"]
    return auth_ctx


def test_send_message_returns_v1_task(mock_auth_context):
    settings.a2a_protocol_mode = "v1"
    app.dependency_overrides[get_auth_context] = lambda: mock_auth_context

    with patch("app.domain.a2a.dispatcher.invoke_openai_compatible", new_callable=AsyncMock) as mock_invoke:
        rate_limit = MagicMock()
        rate_limit.check_limit = AsyncMock(return_value=MagicMock(allowed=True))
        routing = MagicMock()
        routing.select_upstream.return_value = {
            "upstream": {"endpoint": "http://mock", "id": "u1"},
            "model_name": "gpt-4o",
        }
        usage_store = MagicMock()
        audit_store = MagicMock()
        mock_invoke.return_value = {
            "choices": [{"message": {"content": "Hello from A2A v1"}}],
            "usage": {"prompt_tokens": 4, "completion_tokens": 5},
        }

        app.dependency_overrides[get_rate_limit_store] = lambda: rate_limit
        app.dependency_overrides[get_routing_service] = lambda: routing
        app.dependency_overrides[get_usage_store] = lambda: usage_store
        app.dependency_overrides[get_audit_store] = lambda: audit_store

        response = client.post(
            "/rpc",
            json={
                "jsonrpc": "2.0",
                "id": "req-1",
                "method": "SendMessage",
                "params": {
                    "message": {
                        "messageId": "msg-1",
                        "role": "user",
                        "parts": [{"kind": "text", "text": "Hello"}],
                    },
                    "configuration": {"historyLength": 1},
                },
            },
            headers=AUTH_HEADERS,
        )

    assert response.status_code == 200, response.text
    data = response.json()
    task = data["result"]["task"]
    assert task["contextId"]
    assert task["status"]["state"] == "TASK_STATE_COMPLETED"
    assert task["artifacts"][0]["parts"][0]["text"] == "Hello from A2A v1"
    assert "kind" not in task["artifacts"][0]["parts"][0]
    assert task["history"][0]["messageId"] == "msg-1"
    assert "kind" not in task["history"][0]["parts"][0]
    assert task["metadata"]["originSurface"] == "a2a_v1"
    assert data["result"]["message"]["parts"][0]["text"] == "Hello from A2A v1"


def test_send_message_accepts_operation_level_send_scope_without_legacy_invoke():
    settings.a2a_protocol_mode = "v1"
    app.dependency_overrides[get_auth_context] = lambda: make_auth_context(["a2a.send", "llm.invoke"])

    with patch("app.domain.a2a.dispatcher.invoke_openai_compatible", new_callable=AsyncMock) as mock_invoke:
        rate_limit = MagicMock()
        rate_limit.check_limit = AsyncMock(return_value=MagicMock(allowed=True))
        routing = MagicMock()
        routing.select_upstream.return_value = {
            "upstream": {"endpoint": "http://mock", "id": "u1"},
            "model_name": "gpt-4o",
        }
        mock_invoke.return_value = {
            "choices": [{"message": {"content": "Scoped hello"}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 2},
        }

        app.dependency_overrides[get_rate_limit_store] = lambda: rate_limit
        app.dependency_overrides[get_routing_service] = lambda: routing
        app.dependency_overrides[get_usage_store] = lambda: MagicMock()
        app.dependency_overrides[get_audit_store] = lambda: MagicMock()

        response = client.post(
            "/rpc",
            json={
                "jsonrpc": "2.0",
                "id": "req-send-scope",
                "method": "SendMessage",
                "params": {
                    "message": {
                        "messageId": "msg-send-scope",
                        "role": "user",
                        "parts": [{"kind": "text", "text": "Hello"}],
                    },
                },
            },
            headers=AUTH_HEADERS,
        )

    assert response.status_code == 200, response.text
    data = response.json()
    assert data["result"]["task"]["status"]["state"] == "TASK_STATE_COMPLETED"
    assert data["result"]["message"]["parts"][0]["text"] == "Scoped hello"


def test_send_message_rejects_legacy_invoke_scope_in_strict_v1_mode():
    settings.a2a_protocol_mode = "v1"
    app.dependency_overrides[get_auth_context] = lambda: make_auth_context(["a2a.invoke", "llm.invoke", "a2a.stream"])

    response = client.post(
        "/rpc",
        json={
            "jsonrpc": "2.0",
            "id": "req-legacy-invoke",
            "method": "SendMessage",
            "params": {
                "message": {
                    "messageId": "msg-legacy-invoke",
                    "role": "user",
                    "parts": [{"kind": "text", "text": "Hello"}],
                },
            },
        },
        headers=AUTH_HEADERS,
    )

    assert response.status_code == 200, response.text
    error = response.json()["error"]
    assert error["data"]["talos_code"] == "RBAC_DENIED"
    assert error["data"]["required_scope_sets"] == [["a2a.send"]]


def test_send_message_accepts_legacy_invoke_scope_in_dual_mode():
    settings.a2a_protocol_mode = "dual"
    app.dependency_overrides[get_auth_context] = lambda: make_auth_context(["a2a.invoke", "llm.invoke", "a2a.stream"])

    with patch("app.domain.a2a.dispatcher.invoke_openai_compatible", new_callable=AsyncMock) as mock_invoke:
        rate_limit = MagicMock()
        rate_limit.check_limit = AsyncMock(return_value=MagicMock(allowed=True))
        routing = MagicMock()
        routing.select_upstream.return_value = {
            "upstream": {"endpoint": "http://mock", "id": "u1"},
            "model_name": "gpt-4o",
        }
        mock_invoke.return_value = {
            "choices": [{"message": {"content": "Dual legacy hello"}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 2},
        }

        app.dependency_overrides[get_rate_limit_store] = lambda: rate_limit
        app.dependency_overrides[get_routing_service] = lambda: routing
        app.dependency_overrides[get_usage_store] = lambda: MagicMock()
        app.dependency_overrides[get_audit_store] = lambda: MagicMock()

        response = client.post(
            "/rpc",
            json={
                "jsonrpc": "2.0",
                "id": "req-dual-legacy-invoke",
                "method": "SendMessage",
                "params": {
                    "message": {
                        "messageId": "msg-dual-legacy-invoke",
                        "role": "user",
                        "parts": [{"kind": "text", "text": "Hello"}],
                    },
                },
            },
            headers=AUTH_HEADERS,
        )

    assert response.status_code == 200, response.text
    assert response.json()["result"]["message"]["parts"][0]["text"] == "Dual legacy hello"


def test_root_rpc_compat_alias_accepts_send_message_in_dual_mode():
    settings.a2a_protocol_mode = "dual"
    app.dependency_overrides[get_auth_context] = lambda: make_auth_context(["a2a.invoke", "llm.invoke", "a2a.stream"])

    with patch("app.domain.a2a.dispatcher.invoke_openai_compatible", new_callable=AsyncMock) as mock_invoke:
        rate_limit = MagicMock()
        rate_limit.check_limit = AsyncMock(return_value=MagicMock(allowed=True))
        routing = MagicMock()
        routing.select_upstream.return_value = {
            "upstream": {"endpoint": "http://mock", "id": "u1"},
            "model_name": "gpt-4o",
        }
        mock_invoke.return_value = {
            "choices": [{"message": {"content": "Root alias hello"}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 2},
        }

        app.dependency_overrides[get_rate_limit_store] = lambda: rate_limit
        app.dependency_overrides[get_routing_service] = lambda: routing
        app.dependency_overrides[get_usage_store] = lambda: MagicMock()
        app.dependency_overrides[get_audit_store] = lambda: MagicMock()

        response = client.post(
            "/",
            json={
                "jsonrpc": "2.0",
                "id": "req-root-compat",
                "method": "SendMessage",
                "params": {
                    "message": {
                        "messageId": "msg-root-compat",
                        "role": "user",
                        "parts": [{"kind": "text", "text": "Hello"}],
                    },
                },
            },
            headers=AUTH_HEADERS,
        )

    assert response.status_code == 200, response.text
    assert response.json()["result"]["message"]["parts"][0]["text"] == "Root alias hello"


def test_send_message_can_use_dev_mock_llm_fallback(monkeypatch):
    settings.a2a_protocol_mode = "v1"
    app.dependency_overrides[get_auth_context] = lambda: make_auth_context(["a2a.send", "llm.invoke"])
    monkeypatch.setenv("A2A_MOCK_LLM_RESPONSES", "true")

    rate_limit = MagicMock()
    rate_limit.check_limit = AsyncMock(return_value=MagicMock(allowed=True))
    routing = MagicMock()
    routing.select_upstream.return_value = {
        "upstream": {
            "endpoint": "https://api.openai.com/v1",
            "id": "u-mock",
            "provider": "openai",
            "credentials_ref": "secret:openai-api-key",
        },
        "model_name": "gpt-4o",
    }

    app.dependency_overrides[get_rate_limit_store] = lambda: rate_limit
    app.dependency_overrides[get_routing_service] = lambda: routing
    app.dependency_overrides[get_usage_store] = lambda: MagicMock()
    app.dependency_overrides[get_audit_store] = lambda: MagicMock()

    with patch(
        "app.domain.a2a.dispatcher.invoke_openai_compatible",
        new=AsyncMock(side_effect=AssertionError("mock fallback should bypass the real upstream")),
    ):
        response = client.post(
            "/rpc",
            json={
                "jsonrpc": "2.0",
                "id": "req-mock-fallback",
                "method": "SendMessage",
                "params": {
                    "message": {
                        "messageId": "msg-mock-fallback",
                        "role": "user",
                        "parts": [{"kind": "text", "text": "Hello from the TCK"}],
                    },
                },
            },
            headers=AUTH_HEADERS,
        )

    assert response.status_code == 200, response.text
    data = response.json()
    assert data["result"]["task"]["status"]["state"] == "TASK_STATE_COMPLETED"
    assert "A2A mock responses enabled" in data["result"]["message"]["parts"][0]["text"]
    assert "Hello from the TCK" in data["result"]["message"]["parts"][0]["text"]


def test_send_message_surfaces_upstream_retry_metadata(mock_auth_context):
    settings.a2a_protocol_mode = "v1"
    app.dependency_overrides[get_auth_context] = lambda: mock_auth_context

    rate_limit = MagicMock()
    rate_limit.check_limit = AsyncMock(return_value=MagicMock(allowed=True))
    routing = MagicMock()
    routing.select_upstream.return_value = {
        "upstream": {"endpoint": "http://mock", "id": "u1"},
        "model_name": "gpt-4o",
    }

    app.dependency_overrides[get_rate_limit_store] = lambda: rate_limit
    app.dependency_overrides[get_routing_service] = lambda: routing
    app.dependency_overrides[get_usage_store] = lambda: MagicMock()
    app.dependency_overrides[get_audit_store] = lambda: MagicMock()

    with patch(
        "app.domain.a2a.dispatcher.invoke_openai_compatible",
        new=AsyncMock(
            side_effect=UpstreamRateLimitError(
                "quota exceeded",
                request_id="req-429",
                status_code=429,
                retry_after_seconds=1.5,
            )
        ),
    ):
        response = client.post(
            "/rpc",
            json={
                "jsonrpc": "2.0",
                "id": "req-rate-limit",
                "method": "SendMessage",
                "params": {
                    "message": {
                        "messageId": "msg-rate-limit",
                        "role": "user",
                        "parts": [{"kind": "text", "text": "Hello"}],
                    }
                },
            },
            headers=AUTH_HEADERS,
        )

    assert response.status_code == 200, response.text
    data = response.json()
    assert data["error"]["message"] == "Upstream rate limited"
    assert data["error"]["data"]["talos_code"] == "UPSTREAM_RATE_LIMITED"
    assert data["error"]["data"]["upstream_request_id"] == "req-429"
    assert data["error"]["data"]["upstream_status_code"] == 429
    assert data["error"]["data"]["retry_after_seconds"] == 1.5
    assert data["error"]["data"]["retry_after_ms"] == 1500


def test_get_task_uses_task_not_found_error_code():
    settings.a2a_protocol_mode = "v1"
    app.dependency_overrides[get_auth_context] = lambda: make_auth_context(["a2a.get"])

    response = client.post(
        "/rpc",
        json={
            "jsonrpc": "2.0",
            "id": "req-get-missing",
            "method": "GetTask",
            "params": {"id": "missing-task"},
        },
        headers=AUTH_HEADERS,
    )

    assert response.status_code == 200, response.text
    error = response.json()["error"]
    assert error["code"] == -32001
    assert error["data"]["talos_code"] == "NOT_FOUND"


def test_cancel_task_uses_task_not_found_error_code():
    settings.a2a_protocol_mode = "v1"
    app.dependency_overrides[get_auth_context] = lambda: make_auth_context(["a2a.cancel"])

    response = client.post(
        "/rpc",
        json={
            "jsonrpc": "2.0",
            "id": "req-cancel-missing",
            "method": "CancelTask",
            "params": {"id": "missing-task"},
        },
        headers=AUTH_HEADERS,
    )

    assert response.status_code == 200, response.text
    error = response.json()["error"]
    assert error["code"] == -32001
    assert error["data"]["talos_code"] == "NOT_FOUND"


def test_invalid_jsonrpc_id_type_is_rejected():
    settings.a2a_protocol_mode = "v1"
    app.dependency_overrides[get_auth_context] = lambda: make_auth_context(["a2a.send", "llm.invoke"])

    response = client.post(
        "/rpc",
        json={
            "jsonrpc": "2.0",
            "id": {"bad": "type"},
            "method": "SendMessage",
            "params": {},
        },
        headers=AUTH_HEADERS,
    )

    assert response.status_code == 200, response.text
    error = response.json()["error"]
    assert error["code"] == -32600


def test_get_task_omits_artifacts_by_default(mock_auth_context):
    settings.a2a_protocol_mode = "v1"
    app.dependency_overrides[get_auth_context] = lambda: mock_auth_context
    now = datetime.now(timezone.utc)

    _TASK_STATE["task-123"] = {
        "id": "task-123",
        "team_id": "team-1",
        "key_id": "key-123",
        "org_id": "org-1",
        "request_id": "req-123",
        "origin_surface": "a2a_v1",
        "method": "tasks.send",
        "status": "completed",
        "version": 2,
        "request_meta": {"context_id": "ctx-123", "origin_surface": "a2a_v1"},
        "input_redacted": {
            "messages": [
                {
                    "messageId": "msg-123",
                    "role": "user",
                    "parts": [{"kind": "text", "text": "hi"}],
                }
            ]
        },
        "result": {
            "task_id": "task-123",
            "artifacts": [
                {
                    "artifact_id": "artifact-1",
                    "name": "response.txt",
                    "content": {"text": "hello"},
                }
            ],
        },
        "created_at": now,
        "updated_at": now,
    }

    response = client.post(
        "/rpc",
        json={
            "jsonrpc": "2.0",
            "id": 2,
            "method": "GetTask",
            "params": {"id": "task-123", "historyLength": 1},
        },
        headers=AUTH_HEADERS,
    )

    assert response.status_code == 200, response.text
    data = response.json()
    assert data["result"]["id"] == "task-123"
    assert data["result"]["contextId"] == "ctx-123"
    assert "artifacts" not in data["result"]
    assert data["result"]["history"][0]["parts"][0]["text"] == "hi"
    assert "kind" not in data["result"]["history"][0]["parts"][0]


def test_get_task_accepts_operation_level_get_scope_without_legacy_invoke():
    settings.a2a_protocol_mode = "v1"
    app.dependency_overrides[get_auth_context] = lambda: make_auth_context(["a2a.get"])
    now = datetime.now(timezone.utc)

    _TASK_STATE["task-scope-get"] = {
        "id": "task-scope-get",
        "team_id": "team-1",
        "key_id": "key-123",
        "org_id": "org-1",
        "request_id": "req-scope-get",
        "origin_surface": "a2a_v1",
        "method": "tasks.send",
        "status": "completed",
        "version": 2,
        "request_meta": {"context_id": "ctx-scope-get", "origin_surface": "a2a_v1"},
        "input_redacted": None,
        "result": None,
        "error": None,
        "created_at": now,
        "updated_at": now,
    }

    response = client.post(
        "/rpc",
        json={
            "jsonrpc": "2.0",
            "id": "req-scope-get",
            "method": "GetTask",
            "params": {"id": "task-scope-get"},
        },
        headers=AUTH_HEADERS,
    )

    assert response.status_code == 200, response.text
    assert response.json()["result"]["id"] == "task-scope-get"


def test_cancel_task_updates_non_terminal_task(mock_auth_context):
    settings.a2a_protocol_mode = "v1"
    app.dependency_overrides[get_auth_context] = lambda: mock_auth_context
    now = datetime.now(timezone.utc)

    _TASK_STATE["task-cancel"] = {
        "id": "task-cancel",
        "team_id": "team-1",
        "key_id": "key-123",
        "org_id": "org-1",
        "request_id": "req-cancel",
        "origin_surface": "a2a_v1",
        "method": "tasks.send",
        "status": "queued",
        "version": 1,
        "request_meta": {"context_id": "ctx-cancel", "origin_surface": "a2a_v1"},
        "input_redacted": None,
        "result": None,
        "error": None,
        "created_at": now,
        "updated_at": now,
    }

    response = client.post(
        "/rpc",
        json={
            "jsonrpc": "2.0",
            "id": 3,
            "method": "CancelTask",
            "params": {"id": "task-cancel"},
        },
        headers=AUTH_HEADERS,
    )

    assert response.status_code == 200, response.text
    data = response.json()
    assert data["result"]["status"]["state"] == "TASK_STATE_CANCELED"
    assert data["result"]["status"]["message"]["parts"][0]["text"] == "Task canceled"
    assert _TASK_STATE["task-cancel"]["status"] == "canceled"


def test_list_tasks_paginates_and_filters_by_state(mock_auth_context):
    settings.a2a_protocol_mode = "v1"
    app.dependency_overrides[get_auth_context] = lambda: mock_auth_context
    base = datetime.now(timezone.utc)

    for index, minutes in enumerate([5, 3, 1], start=1):
        _TASK_STATE[f"task-{index}"] = {
            "id": f"task-{index}",
            "team_id": "team-1",
            "key_id": "key-123",
            "org_id": "org-1",
            "request_id": f"req-{index}",
            "origin_surface": "a2a_v1",
            "method": "tasks.send",
            "status": "completed" if index != 2 else "running",
            "version": index,
            "request_meta": {
                "context_id": "ctx-shared" if index != 3 else "ctx-other",
                "origin_surface": "a2a_v1",
            },
            "input_redacted": None,
            "result": None,
            "error": None,
            "created_at": base - timedelta(minutes=minutes + 1),
            "updated_at": base - timedelta(minutes=minutes),
        }

    first_page = client.post(
        "/rpc",
        json={
            "jsonrpc": "2.0",
            "id": 4,
            "method": "ListTasks",
            "params": {
                "pageSize": 1,
                "state": "TASK_STATE_COMPLETED",
            },
        },
        headers=AUTH_HEADERS,
    )

    assert first_page.status_code == 200, first_page.text
    first_payload = first_page.json()["result"]
    assert [task["id"] for task in first_payload["tasks"]] == ["task-3"]
    assert first_payload["pageSize"] == 1
    assert first_payload["totalSize"] == 2
    assert "nextPageToken" in first_payload

    second_page = client.post(
        "/rpc",
        json={
            "jsonrpc": "2.0",
            "id": 5,
            "method": "ListTasks",
            "params": {
                "pageSize": 1,
                "state": "completed",
                "pageToken": first_payload["nextPageToken"],
            },
        },
        headers=AUTH_HEADERS,
    )

    assert second_page.status_code == 200, second_page.text
    second_payload = second_page.json()["result"]
    assert [task["id"] for task in second_payload["tasks"]] == ["task-1"]
    assert second_payload["totalSize"] == 2
    assert "nextPageToken" not in second_payload


def test_subscribe_to_task_returns_sse_updates(mock_auth_context):
    settings.a2a_protocol_mode = "v1"
    app.dependency_overrides[get_auth_context] = lambda: mock_auth_context
    now = datetime.now(timezone.utc)

    _TASK_STATE["task-stream"] = {
        "id": "task-stream",
        "team_id": "team-1",
        "key_id": "key-123",
        "org_id": "org-1",
        "request_id": "req-stream",
        "origin_surface": "a2a_v1",
        "method": "tasks.send",
        "status": "running",
        "version": 1,
        "request_meta": {"context_id": "ctx-stream", "origin_surface": "a2a_v1"},
        "input_redacted": {
            "messages": [
                {
                    "messageId": "msg-stream",
                    "role": "user",
                    "parts": [{"kind": "text", "text": "stream this"}],
                }
            ]
        },
        "result": None,
        "error": None,
        "created_at": now,
        "updated_at": now,
    }

    async def fake_stream(*args, **kwargs):
        _TASK_STATE["task-stream"]["status"] = "completed"
        _TASK_STATE["task-stream"]["version"] = 2
        _TASK_STATE["task-stream"]["updated_at"] = now + timedelta(seconds=5)
        _TASK_STATE["task-stream"]["result"] = {
            "task_id": "task-stream",
            "artifacts": [
                {
                    "artifact_id": "artifact-stream",
                    "name": "response.txt",
                    "content": {"text": "stream complete"},
                }
            ],
        }
        yield (
            'id: task-stream:2\n'
            'data: {"event_id":"task-stream:2","task_id":"task-stream","status":"completed","version":2,"updated_at":"'
            + (now + timedelta(seconds=5)).isoformat().replace("+00:00", "Z")
            + '"}\n\n'
        )

    with patch("app.api.a2a_v1.service.stream_task_events", fake_stream):
        with client.stream(
            "POST",
            "/rpc",
            json={
                "jsonrpc": "2.0",
                "id": 6,
                "method": "SubscribeToTask",
                "params": {"id": "task-stream", "includeArtifacts": True, "historyLength": 1},
            },
            headers=AUTH_HEADERS,
        ) as response:
            body = "".join(response.iter_text())
            content_type = response.headers["content-type"]

    assert response.status_code == 200
    assert content_type.startswith("text/event-stream")

    payloads = []
    for line in body.splitlines():
        if line.startswith("data: "):
            payloads.append(json.loads(line[6:]))

    assert payloads[0]["jsonrpc"] == "2.0"
    assert payloads[0]["id"] == 6
    assert payloads[0]["result"]["task"]["id"] == "task-stream"
    assert payloads[1]["result"]["artifactUpdate"]["artifact"]["parts"][0]["text"] == "stream complete"
    assert payloads[2]["result"]["statusUpdate"]["status"]["state"] == "TASK_STATE_COMPLETED"
    assert payloads[2]["result"]["statusUpdate"]["metadata"]["final"] is True


def test_subscribe_to_task_requires_subscribe_scope_when_legacy_stream_missing():
    settings.a2a_protocol_mode = "v1"
    app.dependency_overrides[get_auth_context] = lambda: make_auth_context(["a2a.get"])
    now = datetime.now(timezone.utc)

    _TASK_STATE["task-subscribe-scope"] = {
        "id": "task-subscribe-scope",
        "team_id": "team-1",
        "key_id": "key-123",
        "org_id": "org-1",
        "request_id": "req-subscribe-scope",
        "origin_surface": "a2a_v1",
        "method": "tasks.send",
        "status": "running",
        "version": 1,
        "request_meta": {"context_id": "ctx-subscribe-scope", "origin_surface": "a2a_v1"},
        "input_redacted": None,
        "result": None,
        "error": None,
        "created_at": now,
        "updated_at": now,
    }

    response = client.post(
        "/rpc",
        json={
            "jsonrpc": "2.0",
            "id": "req-subscribe-scope",
            "method": "SubscribeToTask",
            "params": {"id": "task-subscribe-scope"},
        },
        headers=AUTH_HEADERS,
    )

    assert response.status_code == 200, response.text
    error = response.json()["error"]
    assert error["message"] == "Permission denied"
    assert error["data"]["talos_code"] == "RBAC_DENIED"
    assert error["data"]["operation"] == "SubscribeToTask"


def test_send_streaming_message_returns_jsonrpc_sse(mock_auth_context):
    settings.a2a_protocol_mode = "v1"
    app.dependency_overrides[get_auth_context] = lambda: mock_auth_context
    now = datetime.now(timezone.utc)

    async def fake_handle_send(self, params, request_id, capability=None):
        task_id = params["task_id"]
        _TASK_STATE[task_id] = {
            "id": task_id,
            "team_id": "team-1",
            "key_id": "key-123",
            "org_id": "org-1",
            "request_id": str(request_id),
            "origin_surface": "a2a_v1",
            "method": "tasks.send",
            "status": "running",
            "version": 1,
            "request_meta": {
                "context_id": params["context_id"],
                "origin_surface": "a2a_v1",
            },
            "input_redacted": params["input_redacted"],
            "result": None,
            "error": None,
            "created_at": now,
            "updated_at": now,
        }
        await asyncio.sleep(0.01)
        _TASK_STATE[task_id]["status"] = "completed"
        _TASK_STATE[task_id]["version"] = 2
        _TASK_STATE[task_id]["updated_at"] = now + timedelta(seconds=1)
        _TASK_STATE[task_id]["result"] = {
            "task_id": task_id,
            "artifacts": [
                {
                    "artifact_id": f"{task_id}:artifact",
                    "name": "response.txt",
                    "content": {"text": "streamed hello"},
                }
            ],
        }
        return _TASK_STATE[task_id]["result"]

    async def fake_stream(*args, **kwargs):
        task_id = kwargs["task_id"]
        await asyncio.sleep(0.02)
        yield (
            f'id: {task_id}:2\n'
            f'data: {json.dumps({"event_id": f"{task_id}:2", "task_id": task_id, "status": "completed", "version": 2, "updated_at": (now + timedelta(seconds=1)).isoformat().replace("+00:00", "Z")})}\n\n'
        )

    with patch("app.api.a2a_v1.service.A2ADispatcher.handle_send", fake_handle_send), patch(
        "app.api.a2a_v1.service.stream_task_events",
        fake_stream,
    ):
        with client.stream(
            "POST",
            "/rpc",
            json={
                "jsonrpc": "2.0",
                "id": "stream-1",
                "method": "SendStreamingMessage",
                "params": {
                    "message": {
                        "messageId": "msg-streaming",
                        "role": "user",
                        "parts": [{"kind": "text", "text": "hello stream"}],
                    },
                    "configuration": {"historyLength": 1},
                },
            },
            headers=AUTH_HEADERS,
        ) as response:
            body = "".join(response.iter_text())
            content_type = response.headers["content-type"]

    assert response.status_code == 200
    assert content_type.startswith("text/event-stream")
    payloads = [
        json.loads(line[6:])
        for line in body.splitlines()
        if line.startswith("data: ")
    ]
    assert payloads[0]["jsonrpc"] == "2.0"
    assert payloads[0]["id"] == "stream-1"
    assert payloads[0]["result"]["task"]["status"]["state"] in {"TASK_STATE_WORKING", "TASK_STATE_COMPLETED"}
    assert payloads[-2]["result"]["artifactUpdate"]["artifact"]["parts"][0]["text"] == "streamed hello"
    assert payloads[-1]["result"]["statusUpdate"]["status"]["state"] == "TASK_STATE_COMPLETED"


def test_push_notification_config_crud_uses_official_method_aliases_in_dual_mode(mock_auth_context):
    settings.a2a_protocol_mode = "dual"
    app.dependency_overrides[get_auth_context] = lambda: mock_auth_context
    now = datetime.now(timezone.utc)

    _TASK_STATE["task-push"] = {
        "id": "task-push",
        "team_id": "team-1",
        "key_id": "key-123",
        "org_id": "org-1",
        "request_id": "req-push",
        "origin_surface": "a2a_v1",
        "method": "tasks.send",
        "status": "queued",
        "version": 1,
        "request_meta": {"context_id": "ctx-push", "origin_surface": "a2a_v1"},
        "input_redacted": None,
        "result": None,
        "error": None,
        "created_at": now,
        "updated_at": now,
    }

    create_response = client.post(
        "/rpc",
        json={
            "jsonrpc": "2.0",
            "id": "push-create",
            "method": "tasks/pushNotificationConfig/set",
            "params": {
                "taskId": "task-push",
                "url": "https://client.example.com/a2a/push",
                "token": "push-token",
                "authentication": {
                    "scheme": "Bearer",
                    "credentials": "push-secret",
                },
            },
        },
        headers=AUTH_HEADERS,
    )

    assert create_response.status_code == 200, create_response.text
    created = create_response.json()["result"]
    config_id = created["id"]
    assert created["taskId"] == "task-push"
    assert created["authentication"]["credentials"] == "[REDACTED]"

    get_response = client.post(
        "/rpc",
        json={
            "jsonrpc": "2.0",
            "id": "push-get",
            "method": "tasks/pushNotificationConfig/get",
            "params": {"taskId": "task-push", "id": config_id},
        },
        headers=AUTH_HEADERS,
    )
    assert get_response.status_code == 200, get_response.text
    assert get_response.json()["result"]["authentication"]["credentials"] == "[REDACTED]"

    list_response = client.post(
        "/rpc",
        json={
            "jsonrpc": "2.0",
            "id": "push-list",
            "method": "tasks/pushNotificationConfig/list",
            "params": {"taskId": "task-push"},
        },
        headers=AUTH_HEADERS,
    )
    assert list_response.status_code == 200, list_response.text
    listed = list_response.json()["result"]["configs"]
    assert len(listed) == 1
    assert listed[0]["id"] == config_id

    delete_response = client.post(
        "/rpc",
        json={
            "jsonrpc": "2.0",
            "id": "push-delete",
            "method": "tasks/pushNotificationConfig/delete",
            "params": {"taskId": "task-push", "id": config_id},
        },
        headers=AUTH_HEADERS,
    )
    assert delete_response.status_code == 200, delete_response.text
    assert delete_response.json()["result"]["deleted"] is True

    final_list = client.post(
        "/rpc",
        json={
            "jsonrpc": "2.0",
            "id": "push-list-final",
            "method": "tasks/pushNotificationConfig/list",
            "params": {"taskId": "task-push"},
        },
        headers=AUTH_HEADERS,
    )
    assert final_list.status_code == 200, final_list.text
    assert final_list.json()["result"]["configs"] == []


def test_send_message_with_push_notification_config_schedules_delivery(mock_auth_context):
    settings.a2a_protocol_mode = "dual"
    app.dependency_overrides[get_auth_context] = lambda: mock_auth_context

    with patch("app.domain.a2a.dispatcher.invoke_openai_compatible", new_callable=AsyncMock) as mock_invoke, patch(
        "app.domain.a2a.dispatcher.schedule_push_notifications"
    ) as mock_schedule:
        rate_limit = MagicMock()
        rate_limit.check_limit = AsyncMock(return_value=MagicMock(allowed=True))
        routing = MagicMock()
        routing.select_upstream.return_value = {
            "upstream": {"endpoint": "http://mock", "id": "u1"},
            "model_name": "gpt-4o",
        }
        usage_store = MagicMock()
        audit_store = MagicMock()
        mock_invoke.return_value = {
            "choices": [{"message": {"content": "Hello with push"}}],
            "usage": {"prompt_tokens": 4, "completion_tokens": 5},
        }

        app.dependency_overrides[get_rate_limit_store] = lambda: rate_limit
        app.dependency_overrides[get_routing_service] = lambda: routing
        app.dependency_overrides[get_usage_store] = lambda: usage_store
        app.dependency_overrides[get_audit_store] = lambda: audit_store

        response = client.post(
            "/rpc",
            json={
                "jsonrpc": "2.0",
                "id": "push-send",
                "method": "message/send",
                "params": {
                    "message": {
                        "messageId": "msg-push",
                        "role": "user",
                        "parts": [{"kind": "text", "text": "Hello"}],
                    },
                    "configuration": {
                        "taskPushNotificationConfig": {
                            "url": "https://client.example.com/a2a/push",
                            "token": "push-token",
                            "authentication": {
                                "scheme": "Bearer",
                                "credentials": "push-secret",
                            },
                        }
                    },
                },
            },
            headers=AUTH_HEADERS,
        )

    assert response.status_code == 200, response.text
    assert mock_schedule.called
    delivered_configs, payload = mock_schedule.call_args.args
    assert delivered_configs[0]["url"] == "https://client.example.com/a2a/push"
    assert payload["statusUpdate"]["status"]["state"] in {"TASK_STATE_WORKING", "TASK_STATE_COMPLETED"}


def test_strict_v1_rejects_legacy_message_send_alias(mock_auth_context):
    settings.a2a_protocol_mode = "v1"
    app.dependency_overrides[get_auth_context] = lambda: mock_auth_context

    response = client.post(
        "/rpc",
        json={
            "jsonrpc": "2.0",
            "id": "legacy-alias-v1",
            "method": "message/send",
            "params": {
                "message": {
                    "messageId": "msg-legacy-alias-v1",
                    "role": "user",
                    "parts": [{"kind": "text", "text": "Hello"}],
                },
            },
        },
        headers=AUTH_HEADERS,
    )

    assert response.status_code == 200, response.text
    error = response.json()["error"]
    assert error["code"] == -32601


def test_strict_v1_rejects_legacy_push_notification_alias(mock_auth_context):
    settings.a2a_protocol_mode = "v1"
    app.dependency_overrides[get_auth_context] = lambda: mock_auth_context

    response = client.post(
        "/rpc",
        json={
            "jsonrpc": "2.0",
            "id": "legacy-push-v1",
            "method": "tasks/pushNotificationConfig/list",
            "params": {"taskId": "task-push"},
        },
        headers=AUTH_HEADERS,
    )

    assert response.status_code == 200, response.text
    error = response.json()["error"]
    assert error["code"] == -32601


def test_get_extended_agent_card_rpc_returns_authenticated_detail(mock_auth_context):
    settings.a2a_protocol_mode = "v1"
    app.dependency_overrides[get_auth_context] = lambda: mock_auth_context

    response = client.post(
        "/rpc",
        json={
            "jsonrpc": "2.0",
            "id": "card-1",
            "method": "GetExtendedAgentCard",
            "params": {},
        },
        headers=AUTH_HEADERS,
    )

    assert response.status_code == 200, response.text
    payload = response.json()["result"]
    assert payload["capabilities"]["extendedAgentCard"] is True
    assert len(payload["skills"]) == 3
    assert payload["skills"][0]["examples"]


def test_get_extended_agent_card_rpc_accepts_discovery_scope_without_legacy_invoke():
    settings.a2a_protocol_mode = "v1"
    app.dependency_overrides[get_auth_context] = lambda: make_auth_context(["a2a.discovery.read"])

    response = client.post(
        "/rpc",
        json={
            "jsonrpc": "2.0",
            "id": "card-scope-1",
            "method": "GetExtendedAgentCard",
            "params": {},
        },
        headers=AUTH_HEADERS,
    )

    assert response.status_code == 200, response.text
    payload = response.json()["result"]
    assert payload["capabilities"]["extendedAgentCard"] is True
