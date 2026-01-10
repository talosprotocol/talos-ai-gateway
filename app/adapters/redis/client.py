"""Redis Adapter - Connection and utilities."""
import redis.asyncio as redis
from typing import Optional
import os

_redis_client: Optional[redis.Redis] = None


async def get_redis() -> redis.Redis:
    """Get or create Redis connection."""
    global _redis_client
    if _redis_client is None:
        redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
        _redis_client = redis.from_url(redis_url, decode_responses=True)
    return _redis_client


async def close_redis():
    """Close Redis connection."""
    global _redis_client
    if _redis_client:
        await _redis_client.close()
        _redis_client = None


# Rate Limit Keys
def rate_limit_key(team_id: str, key_id: str, surface: str, target: str) -> str:
    """Generate rate limit bucket key."""
    return f"rl:{surface}:{team_id}:{key_id}:{target}:rpm"


def tpm_key(team_id: str, key_id: str, surface: str, target: str) -> str:
    """Generate TPM bucket key."""
    return f"rl:{surface}:{team_id}:{key_id}:{target}:tpm"


# Cooldown Keys
def cooldown_key(upstream_id: str) -> str:
    """Generate cooldown key for upstream."""
    return f"cooldown:{upstream_id}"


# Schema Cache Keys
def schema_cache_key(server_id: str, tool_name: str) -> str:
    """Generate schema cache key."""
    return f"mcp:schema:{server_id}:{tool_name}"


def tool_list_cache_key(server_id: str) -> str:
    """Generate tool list cache key."""
    return f"mcp:tools:{server_id}"
