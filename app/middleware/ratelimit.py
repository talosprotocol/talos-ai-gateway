"""Rate Limiting - Redis Token Bucket implementation."""
from typing import Dict
from datetime import datetime, timedelta, timezone
from dataclasses import dataclass

from app.adapters.redis.client import get_redis, rate_limit_key

# Fallback in-memory state when Redis unavailable
RATE_LIMIT_STATE: Dict[str, dict] = {}


@dataclass
class RateLimitResult:
    allowed: bool
    remaining: int
    reset_at: datetime
    limit: int


async def check_rate_limit_async(
    key_id: str,
    team_id: str,
    surface: str,
    target: str,
    rpm_limit: int = 60,
) -> RateLimitResult:
    """Check rate limits using Redis token bucket."""
    bucket_key = rate_limit_key(team_id, key_id, surface, target)
    now = datetime.now(timezone.utc)
    window_seconds = 60
    
    try:
        redis = await get_redis()
        
        # Use Redis INCR with TTL for sliding window
        current = await redis.incr(bucket_key)
        
        if current == 1:
            # First request in window, set TTL
            await redis.expire(bucket_key, window_seconds)
        
        ttl = await redis.ttl(bucket_key)
        reset_at = now + timedelta(seconds=max(ttl, 0))
        
        if current > rpm_limit:
            return RateLimitResult(
                allowed=False,
                remaining=0,
                reset_at=reset_at,
                limit=rpm_limit
            )
        
        return RateLimitResult(
            allowed=True,
            remaining=rpm_limit - current,
            reset_at=reset_at,
            limit=rpm_limit
        )
        
    except Exception:
        # Fallback to in-memory if Redis unavailable
        return check_rate_limit(key_id, team_id, surface, target, rpm_limit)


def check_rate_limit(
    key_id: str,
    team_id: str,
    surface: str,
    target: str,
    rpm_limit: int = 60,
    tpm_limit: int = 100000,
    tokens_used: int = 0
) -> RateLimitResult:
    """Check rate limits using in-memory token bucket (fallback)."""
    now = datetime.utcnow()
    bucket_key = f"rl:{surface}:{team_id}:{key_id}:{target}:rpm"
    
    bucket = RATE_LIMIT_STATE.get(bucket_key)
    
    if not bucket or bucket["reset_at"] < now:
        bucket = {
            "count": 0,
            "reset_at": now + timedelta(minutes=1),
            "limit": rpm_limit
        }
    
    if bucket["count"] >= rpm_limit:
        return RateLimitResult(
            allowed=False,
            remaining=0,
            reset_at=bucket["reset_at"],
            limit=rpm_limit
        )
    
    bucket["count"] += 1
    RATE_LIMIT_STATE[bucket_key] = bucket
    
    return RateLimitResult(
        allowed=True,
        remaining=rpm_limit - bucket["count"],
        reset_at=bucket["reset_at"],
        limit=rpm_limit
    )


def get_rate_limit_headers(result: RateLimitResult) -> Dict[str, str]:
    """Generate rate limit headers for response."""
    return {
        "X-RateLimit-Limit": str(result.limit),
        "X-RateLimit-Remaining": str(result.remaining),
        "X-RateLimit-Reset": result.reset_at.isoformat() + "Z"
    }
