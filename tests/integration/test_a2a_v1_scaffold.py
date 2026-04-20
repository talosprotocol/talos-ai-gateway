from fastapi.testclient import TestClient
import pytest

from app.main import app
from app.settings import settings


client = TestClient(app)


@pytest.fixture(autouse=True)
def reset_a2a_settings():
    original_mode = settings.a2a_protocol_mode
    original_visibility = settings.a2a_agent_card_visibility
    yield
    settings.a2a_protocol_mode = original_mode
    settings.a2a_agent_card_visibility = original_visibility


def test_agent_card_v1_mode_exposes_supported_interfaces():
    settings.a2a_protocol_mode = "v1"
    settings.a2a_agent_card_visibility = "public"

    response = client.get("/.well-known/agent-card.json")

    assert response.status_code == 200
    data = response.json()
    assert "supportedInterfaces" in data
    assert data["supportedInterfaces"][0]["protocolBinding"] == "JSONRPC"
    assert data["supportedInterfaces"][0]["url"] == "http://testserver/rpc"
    assert data["supportedInterfaces"][0]["protocolVersion"] == "1.0"
    assert data["capabilities"]["pushNotifications"] is True
    assert data["capabilities"]["extendedAgentCard"] is True
    assert "profile" not in data
    assert "securityRequirements" in data
    assert "security" not in data
    assert "extensions" not in data
    assert "stateTransitionHistory" not in data["capabilities"]
    assert data["capabilities"]["extensions"][0]["uri"].startswith("https://talosprotocol.com/extensions/a2a/")


def test_agent_card_defaults_to_public_visibility_in_dual_mode():
    settings.a2a_protocol_mode = "dual"

    response = client.get("/.well-known/agent-card.json")

    assert response.status_code == 200
    data = response.json()
    assert data["supportedInterfaces"][0]["url"] == "http://testserver/rpc"


def test_agent_card_dual_mode_keeps_compat_extension_out_of_public_discovery():
    settings.a2a_protocol_mode = "dual"
    settings.a2a_agent_card_visibility = "public"

    response = client.get("/.well-known/agent-card.json")

    assert response.status_code == 200
    data = response.json()
    extension_uris = {item["uri"] for item in data["capabilities"]["extensions"]}
    assert "https://talosprotocol.com/extensions/a2a/compat-jsonrpc/v0" not in extension_uris


def test_v1_rpc_hidden_in_compat_mode():
    settings.a2a_protocol_mode = "compat"

    response = client.post(
        "/rpc",
        json={"jsonrpc": "2.0", "id": 1, "method": "SendMessage", "params": {}},
    )

    assert response.status_code == 404


def test_root_rpc_compat_hidden_in_strict_v1_mode():
    settings.a2a_protocol_mode = "v1"

    response = client.post(
        "/",
        json={"jsonrpc": "2.0", "id": 7, "method": "SendMessage", "params": {}},
        headers={"Authorization": "Bearer sk-test-key"},
    )

    assert response.status_code == 404


def test_v1_rpc_scaffold_visible_in_v1_mode():
    settings.a2a_protocol_mode = "v1"

    response = client.post(
        "/rpc",
        json={"jsonrpc": "2.0", "id": 7, "method": "SendMessage", "params": {}},
        headers={"Authorization": "Bearer sk-test-key"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["jsonrpc"] == "2.0"
    assert data["id"] == 7
    assert data["error"]["code"] == -32602
    assert "message" in data["error"]["data"]["details"]


def test_v1_rpc_rejects_invalid_dev_bearer_token():
    settings.a2a_protocol_mode = "v1"

    response = client.post(
        "/rpc",
        json={"jsonrpc": "2.0", "id": 7, "method": "SendMessage", "params": {}},
        headers={"Authorization": "Bearer invalid-token"},
    )

    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid key"
    assert response.headers["www-authenticate"] == "Bearer"


def test_v1_rpc_returns_parse_error_for_malformed_json():
    settings.a2a_protocol_mode = "v1"

    response = client.post(
        "/rpc",
        data='{"jsonrpc":"2.0","method":"SendMessage"',
        headers={
            "Authorization": "Bearer sk-test-key",
            "Content-Type": "application/json",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["error"]["code"] == -32700


def test_extended_agent_card_requires_auth_and_returns_more_detail():
    settings.a2a_protocol_mode = "v1"
    settings.a2a_agent_card_visibility = "public"

    unauthenticated = client.get("/extendedAgentCard")
    assert unauthenticated.status_code == 401

    authenticated = client.get(
        "/extendedAgentCard",
        headers={"Authorization": "Bearer sk-test-key"},
    )

    assert authenticated.status_code == 200
    data = authenticated.json()
    assert data["capabilities"]["extendedAgentCard"] is True
    assert len(data["skills"]) == 3
    assert data["skills"][0]["examples"]


def test_extended_agent_card_dual_mode_includes_compat_extension_for_authenticated_clients():
    settings.a2a_protocol_mode = "dual"
    settings.a2a_agent_card_visibility = "public"

    authenticated = client.get(
        "/extendedAgentCard",
        headers={"Authorization": "Bearer sk-test-key"},
    )

    assert authenticated.status_code == 200
    data = authenticated.json()
    extension_uris = {item["uri"] for item in data["capabilities"]["extensions"]}
    assert "https://talosprotocol.com/extensions/a2a/compat-jsonrpc/v0" in extension_uris
