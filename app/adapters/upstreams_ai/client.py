"""LLM Upstream Client - Real HTTP invocation."""
import httpx
from typing import Dict, Any, Optional
import os

# Timeout configuration
DEFAULT_TIMEOUT = 30.0


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
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }
    
    payload = {
        "model": model_name,
        "messages": messages,
        "temperature": temperature
    }
    
    if max_tokens:
        payload["max_tokens"] = max_tokens
    
    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.post(
            f"{endpoint}/chat/completions",
            headers=headers,
            json=payload
        )
        
        if response.status_code == 429:
            raise UpstreamRateLimitError("Upstream rate limited")
        elif response.status_code >= 500:
            raise UpstreamServerError(f"Upstream returned {response.status_code}")
        elif response.status_code >= 400:
            raise UpstreamClientError(f"Upstream returned {response.status_code}: {response.text}")
        
        return response.json()


class UpstreamError(Exception):
    """Base upstream error."""
    pass


class UpstreamRateLimitError(UpstreamError):
    """Upstream rate limited (429)."""
    pass


class UpstreamServerError(UpstreamError):
    """Upstream server error (5xx)."""
    pass


class UpstreamClientError(UpstreamError):
    """Upstream client error (4xx)."""
    pass


def get_api_key(credentials_ref: str) -> str:
    """Resolve credentials reference to actual API key.
    
    In production, this would integrate with a secrets manager.
    For now, it reads from environment variables.
    """
    # credentials_ref format: "secret:NAME" or "env:VAR_NAME"
    if credentials_ref.startswith("secret:"):
        secret_name = credentials_ref[7:]
        # Map to env var
        env_var = secret_name.upper().replace("-", "_")
        return os.getenv(env_var, "")
    elif credentials_ref.startswith("env:"):
        env_var = credentials_ref[4:]
        return os.getenv(env_var, "")
    else:
        return credentials_ref
