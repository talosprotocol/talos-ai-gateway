"""LLM upstream client with bounded retry and structured failures."""

from __future__ import annotations

import asyncio
import os
import re
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Dict, Mapping, Optional

import httpx

# Timeout configuration
DEFAULT_TIMEOUT = 180.0
OLLAMA_DEFAULT_HOST = "http://localhost:11434/v1"
DEFAULT_UPSTREAM_RETRY_MAX_ATTEMPTS = 1
DEFAULT_UPSTREAM_RETRY_MAX_WAIT_SECONDS = 1.0
_RESET_DURATION_RE = re.compile(r"(\d+(?:\.\d+)?)(ms|s|m|h|d)")


def _load_dotenv():
    """Load .env file."""
    try:
        from dotenv import load_dotenv
        for parent in Path(__file__).resolve().parents:
            env_path = parent / ".env"
            if env_path.exists():
                load_dotenv(env_path, override=False)
    except ImportError:
        pass


def _retry_max_attempts() -> int:
    raw = os.getenv("UPSTREAM_RETRY_MAX_ATTEMPTS")
    if raw is None:
        return DEFAULT_UPSTREAM_RETRY_MAX_ATTEMPTS
    try:
        return max(0, int(raw))
    except ValueError:
        return DEFAULT_UPSTREAM_RETRY_MAX_ATTEMPTS


def _retry_max_wait_seconds() -> float:
    raw = os.getenv("UPSTREAM_RETRY_MAX_WAIT_SECONDS")
    if raw is None:
        return DEFAULT_UPSTREAM_RETRY_MAX_WAIT_SECONDS
    try:
        return max(0.0, float(raw))
    except ValueError:
        return DEFAULT_UPSTREAM_RETRY_MAX_WAIT_SECONDS


def _parse_retry_after_seconds(value: Optional[str]) -> Optional[float]:
    if not value:
        return None

    raw = value.strip()
    if not raw:
        return None

    try:
        return max(0.0, float(raw))
    except ValueError:
        pass

    matches = list(_RESET_DURATION_RE.finditer(raw))
    if matches:
        total_seconds = 0.0
        consumed = 0
        for match in matches:
            if match.start() != consumed:
                break
            amount = float(match.group(1))
            unit = match.group(2)
            total_seconds += {
                "ms": amount / 1000.0,
                "s": amount,
                "m": amount * 60.0,
                "h": amount * 3600.0,
                "d": amount * 86400.0,
            }[unit]
            consumed = match.end()
        if consumed == len(raw):
            return max(0.0, total_seconds)

    try:
        target = parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        return None

    if target.tzinfo is None:
        target = target.replace(tzinfo=timezone.utc)
    return max(0.0, (target - datetime.now(timezone.utc)).total_seconds())


def _extract_retry_after_seconds(headers: Mapping[str, str]) -> Optional[float]:
    for header_name in ("retry-after", "x-ratelimit-reset-requests", "x-ratelimit-reset-tokens"):
        parsed = _parse_retry_after_seconds(headers.get(header_name))
        if parsed is not None:
            return parsed
    return None


def _extract_request_id(headers: Mapping[str, str]) -> Optional[str]:
    return headers.get("x-request-id") or headers.get("request-id")


def _error_body(response: httpx.Response) -> Optional[str]:
    try:
        payload = response.json()
    except ValueError:
        payload = response.text

    if isinstance(payload, dict):
        error = payload.get("error")
        if isinstance(error, dict):
            message = error.get("message")
            if isinstance(message, str) and message:
                return message
        return str(payload)
    if isinstance(payload, str) and payload:
        return payload
    return None


def _retry_delay_seconds(
    *,
    response: httpx.Response,
    attempt: int,
    max_attempts: int,
    max_wait_seconds: float,
) -> Optional[float]:
    if attempt >= max_attempts or max_wait_seconds <= 0:
        return None

    retry_after = _extract_retry_after_seconds(response.headers)
    if retry_after is None:
        retry_after = min(0.5 * (2**attempt), max_wait_seconds)

    if retry_after > max_wait_seconds:
        return None
    return max(0.0, retry_after)


def resolve_endpoint(endpoint: str) -> str:
    """Resolve endpoint, handling env: prefix."""
    _load_dotenv()
    
    if endpoint.startswith("env:"):
        env_var = endpoint[4:]
        return os.getenv(env_var, OLLAMA_DEFAULT_HOST)
    return endpoint


async def invoke_openai_compatible(
    endpoint: str,
    model_name: str,
    messages: list,
    api_key: str,
    temperature: float = 1.0,
    max_tokens: Optional[int] = None,
    timeout: float = DEFAULT_TIMEOUT
) -> Dict[str, Any]:
    """Invoke an OpenAI-compatible endpoint."""
    # Resolve endpoint if it's an env reference
    resolved_endpoint = resolve_endpoint(endpoint)
    
    headers = {
        "Content-Type": "application/json"
    }
    
    # Only add auth header if api_key is provided
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    
    payload = {
        "model": model_name,
        "messages": messages,
        "temperature": temperature
    }
    
    if max_tokens:
        payload["max_tokens"] = max_tokens
    
    retry_attempts = _retry_max_attempts()
    retry_max_wait_seconds = _retry_max_wait_seconds()

    async with httpx.AsyncClient(timeout=timeout) as client:
        for attempt in range(retry_attempts + 1):
            try:
                response = await client.post(
                    f"{resolved_endpoint}/chat/completions",
                    headers=headers,
                    json=payload,
                )
            except httpx.TimeoutException as exc:
                raise UpstreamTransportError(
                    "Upstream request timed out",
                    request_id=None,
                    status_code=None,
                    retry_after_seconds=None,
                ) from exc
            except httpx.RequestError as exc:
                raise UpstreamTransportError(
                    "Upstream transport error",
                    request_id=None,
                    status_code=None,
                    retry_after_seconds=None,
                ) from exc

            request_id = _extract_request_id(response.headers)
            retry_after_seconds = _extract_retry_after_seconds(response.headers)
            response_body = _error_body(response)

            if response.status_code == 429:
                delay = _retry_delay_seconds(
                    response=response,
                    attempt=attempt,
                    max_attempts=retry_attempts,
                    max_wait_seconds=retry_max_wait_seconds,
                )
                if delay is not None:
                    if delay > 0:
                        await asyncio.sleep(delay)
                    continue
                raise UpstreamRateLimitError(
                    response_body or "Upstream rate limited",
                    request_id=request_id,
                    status_code=response.status_code,
                    retry_after_seconds=retry_after_seconds,
                )
            if response.status_code >= 500:
                raise UpstreamServerError(
                    response_body or f"Upstream returned {response.status_code}",
                    request_id=request_id,
                    status_code=response.status_code,
                    retry_after_seconds=retry_after_seconds,
                )
            if response.status_code >= 400:
                raise UpstreamClientError(
                    response_body or f"Upstream returned {response.status_code}",
                    request_id=request_id,
                    status_code=response.status_code,
                    retry_after_seconds=retry_after_seconds,
                )

            return response.json()


async def stream_openai_compatible(
    endpoint: str,
    model_name: str,
    messages: list,
    api_key: str,
    temperature: float = 1.0,
    max_tokens: Optional[int] = None,
    timeout: float = DEFAULT_TIMEOUT
) -> AsyncGenerator[str, None]:
    """Stream from an OpenAI-compatible endpoint."""
    
    # Resolve endpoint if it's an env reference
    resolved_endpoint = resolve_endpoint(endpoint)
    
    headers = {
        "Content-Type": "application/json"
    }
    
    # Only add auth header if api_key is provided
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    
    payload = {
        "model": model_name,
        "messages": messages,
        "temperature": temperature,
        "stream": True
    }
    
    if max_tokens:
        payload["max_tokens"] = max_tokens
    
    async with httpx.AsyncClient(timeout=timeout) as client:
        async with client.stream(
            "POST",
            f"{resolved_endpoint}/chat/completions",
            headers=headers,
            json=payload,
        ) as response:
            if response.status_code >= 400:
                # For streaming errors, we need to read the body first
                await response.read()
                request_id = _extract_request_id(response.headers)
                retry_after_seconds = _extract_retry_after_seconds(response.headers)
                response_body = _error_body(response)
                
                if response.status_code == 429:
                    raise UpstreamRateLimitError(
                        response_body or "Upstream rate limited",
                        request_id=request_id,
                        status_code=response.status_code,
                        retry_after_seconds=retry_after_seconds,
                    )
                if response.status_code >= 500:
                    raise UpstreamServerError(
                        response_body or f"Upstream returned {response.status_code}",
                        request_id=request_id,
                        status_code=response.status_code,
                        retry_after_seconds=retry_after_seconds,
                    )
                raise UpstreamClientError(
                    response_body or f"Upstream returned {response.status_code}",
                    request_id=request_id,
                    status_code=response.status_code,
                    retry_after_seconds=retry_after_seconds,
                )

            async for line in response.aiter_lines():
                if line.startswith("data: "):
                    yield line


async def invoke_ollama(
    endpoint: str,
    model_name: str,
    messages: list,
    temperature: float = 1.0,
    timeout: float = DEFAULT_TIMEOUT
) -> Dict[str, Any]:
    """Invoke Ollama's OpenAI-compatible endpoint."""
    # Ollama uses the same OpenAI-compatible API, no auth needed
    return await invoke_openai_compatible(
        endpoint=endpoint,
        model_name=model_name,
        messages=messages,
        api_key="",  # Ollama doesn't need auth
        temperature=temperature,
        timeout=timeout
    )


class UpstreamError(Exception):
    """Base upstream error."""

    def __init__(
        self,
        message: str,
        *,
        request_id: Optional[str] = None,
        status_code: Optional[int] = None,
        retry_after_seconds: Optional[float] = None,
    ) -> None:
        super().__init__(message)
        self.request_id = request_id
        self.status_code = status_code
        self.retry_after_seconds = retry_after_seconds


class UpstreamRateLimitError(UpstreamError):
    """Upstream rate limited (429)."""
    pass


class UpstreamServerError(UpstreamError):
    """Upstream server error (5xx)."""
    pass


class UpstreamClientError(UpstreamError):
    """Upstream client error (4xx)."""
    pass


class UpstreamTransportError(UpstreamError):
    """Upstream transport-level failure."""
    pass


def get_api_key(credentials_ref: str) -> str:
    """Resolve credentials reference to actual API key.
    
    Resolves `secret:NAME` via secrets manager or fallback to env.
    Resolves `env:VAR_NAME` via environment variables.
    """
    _load_dotenv()
    
    # credentials_ref format: "secret:NAME" or "env:VAR_NAME" or empty
    if not credentials_ref:
        return ""
    
    if credentials_ref.startswith("secret:"):
        secret_name = credentials_ref[7:]
        
        # Try secrets manager first
        try:
            from app.domain.secrets import manager
            val = manager.get_secret_value(secret_name)
            if val:
                return val
        except (AttributeError, ImportError):
            pass
            
        # Fallback to env var mapping
        env_var = secret_name.upper().replace("-", "_")
        return os.getenv(env_var, "")
    elif credentials_ref.startswith("env:"):
        env_var = credentials_ref[4:]
        return os.getenv(env_var, "")
    else:
        return credentials_ref
