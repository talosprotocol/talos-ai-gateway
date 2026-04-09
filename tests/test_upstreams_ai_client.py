from unittest.mock import AsyncMock, patch

import httpx
import pytest

from app.adapters.upstreams_ai.client import (
    UpstreamRateLimitError,
    get_api_key,
    invoke_openai_compatible,
)


def test_get_api_key_falls_back_to_env_when_secret_manager_has_no_direct_getter(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "env-openai-key")

    assert get_api_key("secret:openai-api-key") == "env-openai-key"


@pytest.mark.asyncio
async def test_invoke_openai_compatible_retries_once_on_retry_after_header(monkeypatch):
    monkeypatch.setenv("UPSTREAM_RETRY_MAX_ATTEMPTS", "1")
    monkeypatch.setenv("UPSTREAM_RETRY_MAX_WAIT_SECONDS", "1")

    request = httpx.Request("POST", "http://mock/chat/completions")
    responses = [
        httpx.Response(
            429,
            request=request,
            headers={"Retry-After": "0", "x-request-id": "req-rate-limit"},
            json={"error": {"message": "rate limited"}},
        ),
        httpx.Response(
            200,
            request=request,
            json={"choices": [{"message": {"content": "ok"}}]},
        ),
    ]
    client = AsyncMock()
    client.post = AsyncMock(side_effect=responses)
    client_ctx = AsyncMock()
    client_ctx.__aenter__.return_value = client
    client_ctx.__aexit__.return_value = False

    with patch("app.adapters.upstreams_ai.client.httpx.AsyncClient", return_value=client_ctx):
        result = await invoke_openai_compatible(
            endpoint="http://mock",
            model_name="gpt-test",
            messages=[{"role": "user", "content": "hi"}],
            api_key="secret",
        )

    assert result["choices"][0]["message"]["content"] == "ok"
    assert client.post.await_count == 2


@pytest.mark.asyncio
async def test_invoke_openai_compatible_surfaces_retry_metadata_from_rate_limit_response(monkeypatch):
    monkeypatch.setenv("UPSTREAM_RETRY_MAX_ATTEMPTS", "0")
    monkeypatch.setenv("UPSTREAM_RETRY_MAX_WAIT_SECONDS", "1")

    request = httpx.Request("POST", "http://mock/chat/completions")
    response = httpx.Response(
        429,
        request=request,
        headers={
            "x-ratelimit-reset-requests": "1.5s",
            "x-request-id": "req-429",
        },
        json={"error": {"message": "quota exceeded"}},
    )
    client = AsyncMock()
    client.post = AsyncMock(return_value=response)
    client_ctx = AsyncMock()
    client_ctx.__aenter__.return_value = client
    client_ctx.__aexit__.return_value = False

    with patch("app.adapters.upstreams_ai.client.httpx.AsyncClient", return_value=client_ctx):
        with pytest.raises(UpstreamRateLimitError) as exc:
            await invoke_openai_compatible(
                endpoint="http://mock",
                model_name="gpt-test",
                messages=[{"role": "user", "content": "hi"}],
                api_key="secret",
            )

    assert exc.value.request_id == "req-429"
    assert exc.value.status_code == 429
    assert exc.value.retry_after_seconds == pytest.approx(1.5)
    assert str(exc.value) == "quota exceeded"
