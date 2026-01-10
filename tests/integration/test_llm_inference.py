"""Integration tests for LLM Inference API."""
import pytest
from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)

TEST_KEY = "sk-test-key-1"
HEADERS = {"Authorization": f"Bearer {TEST_KEY}"}
ADMIN_HEADERS = {"X-Talos-Principal": "admin@talos.io"}


class TestLlmChatCompletions:
    """Tests for /v1/chat/completions."""

    def test_requires_auth(self):
        """Should return 401 without auth."""
        response = client.post("/v1/chat/completions", json={
            "model": "gpt-4-turbo",
            "messages": [{"role": "user", "content": "Hello"}]
        })
        assert response.status_code == 401

    def test_chat_completion_success(self):
        """Should return completion for allowed model."""
        response = client.post("/v1/chat/completions", headers=HEADERS, json={
            "model": "gpt-4-turbo",
            "messages": [{"role": "user", "content": "Hello"}]
        })
        assert response.status_code == 200
        data = response.json()
        assert "id" in data
        assert "choices" in data
        assert len(data["choices"]) == 1
        assert data["choices"][0]["message"]["role"] == "assistant"

    def test_model_not_allowed(self):
        """Should deny access to unauthorized model."""
        # gpt-4-turbo is in allowed_model_groups for test key
        # Try a model not in the list
        response = client.post("/v1/chat/completions", headers=HEADERS, json={
            "model": "claude-3",
            "messages": [{"role": "user", "content": "Hello"}]
        })
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "MODEL_NOT_ALLOWED"

    def test_streaming_not_supported(self):
        """Should explicitly reject streaming."""
        response = client.post("/v1/chat/completions", headers=HEADERS, json={
            "model": "gpt-4-turbo",
            "messages": [{"role": "user", "content": "Hello"}],
            "stream": True
        })
        assert response.status_code == 400
        assert response.json()["error"]["code"] == "STREAMING_NOT_SUPPORTED"


class TestLlmModels:
    """Tests for /v1/models."""

    def test_list_models(self):
        """Should return allowed models."""
        response = client.get("/v1/models", headers=HEADERS)
        assert response.status_code == 200
        data = response.json()
        assert data["object"] == "list"
        assert len(data["data"]) >= 1


class TestAdminLlm:
    """Tests for /admin/v1/llm endpoints."""

    def test_list_upstreams(self):
        """Should list upstreams."""
        response = client.get("/admin/v1/llm/upstreams", headers=ADMIN_HEADERS)
        assert response.status_code == 200
        assert "upstreams" in response.json()

    def test_list_model_groups(self):
        """Should list model groups."""
        response = client.get("/admin/v1/llm/model_groups", headers=ADMIN_HEADERS)
        assert response.status_code == 200
        assert "model_groups" in response.json()

    def test_list_routing_policies(self):
        """Should list routing policies."""
        response = client.get("/admin/v1/llm/routing_policies", headers=ADMIN_HEADERS)
        assert response.status_code == 200
        assert "policies" in response.json()
