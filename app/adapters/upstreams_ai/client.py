"""LLM Upstream Client - Real HTTP invocation."""
import httpx
from typing import Dict, Any, Optional
import os

# Timeout configuration
DEFAULT_TIMEOUT = 30.0
OLLAMA_DEFAULT_HOST = "http://localhost:11434/v1"


def _load_dotenv():
    """Load .env file."""
    try:
        from dotenv import load_dotenv
        from pathlib import Path
        env_path = Path(__file__).parent.parent.parent.parent / ".env"
        load_dotenv(env_path)
    except ImportError:
        pass


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
    
    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.post(
            f"{resolved_endpoint}/chat/completions",
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
        except ImportError:
            pass
            
        # Fallback to env var mapping
        env_var = secret_name.upper().replace("-", "_")
        return os.getenv(env_var, "")
    elif credentials_ref.startswith("env:"):
        env_var = credentials_ref[4:]
        return os.getenv(env_var, "")
    else:
        return credentials_ref
