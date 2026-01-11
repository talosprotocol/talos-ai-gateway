"""Redis Store Implementations."""
from typing import Dict, Any, Optional
from datetime import datetime, timedelta, timezone
import logging

from app.domain.interfaces import RateLimitStore, RateLimitResult
from app.adapters.redis.client import get_redis

logger = logging.getLogger(__name__)

class RedisRateLimitStore(RateLimitStore):
    async def check_limit(self, key: str, limit: int, window_seconds: int = 60) -> RateLimitResult:
        redis = await get_redis()
        now = datetime.now(timezone.utc)
        
        # Simple Fixed Window / Sliding Expiration
        # INCR key
        try:
            current = await redis.incr(key)
            if current == 1:
                await redis.expire(key, window_seconds)
            
            ttl = await redis.ttl(key)
            # If TTL is -1 (lazy expire fail?) or -2 (gone?), handle gracefully
            if ttl < 0:
                 ttl = window_seconds # Fallback
            
            reset_at = now + timedelta(seconds=ttl)
            
            remaining = max(0, limit - current)
            allowed = current <= limit
            
            return RateLimitResult(
                allowed=allowed,
                remaining=remaining,
                reset_at=reset_at,
                limit=limit
            )
        except Exception as e:
            logger.error(f"Redis rate limit error: {e}")
            # Fail open or closed? Typically fail open for reliability unless critical.
            # But let's return a strict result if redis errors, or maybe allow?
            # User requirement: "Reliability". If redis fails, maybe allow but log.
            # Let's fallback to allowed=True, limit=0 (bypass)
            return RateLimitResult(allowed=True, remaining=1, reset_at=now, limit=limit)

from app.domain.interfaces import SessionStore, SessionState

class RedisSessionStore(SessionStore):
    async def create_session(self, session_id: str, public_key: str, ttl: int = 3600) -> SessionState:
        redis = await get_redis()
        now = datetime.now(timezone.utc)
        expires = now + timedelta(seconds=ttl)
        
        mapping = {
            "pk": public_key,
            "seq": "1", # Start at 1
            "created": now.isoformat(),
            "expires": expires.isoformat()
        }
        
        pipeline = redis.pipeline()
        pipeline.hset(f"session:{session_id}", mapping=mapping)
        pipeline.expire(f"session:{session_id}", ttl)
        await pipeline.execute()
        
        return SessionState(session_id, public_key, 1, now, expires)

    async def get_session(self, session_id: str) -> Optional[SessionState]:
        redis = await get_redis()
        data = await redis.hgetall(f"session:{session_id}")
        if not data:
            return None
        
        return SessionState(
            session_id=session_id,
            public_key=data["pk"],
            next_sequence=int(data["seq"]),
            created_at=datetime.fromisoformat(data["created"]),
            expires_at=datetime.fromisoformat(data["expires"])
        )

    async def validate_sequence(self, session_id: str, sequence: int) -> bool:
        redis = await get_redis()
        key = f"session:{session_id}"
        
        # Simple optimistic lock or script
        # Using Lua for atomicity
        script = """
        local current = redis.call('HGET', KEYS[1], 'seq')
        if current and tonumber(current) == tonumber(ARGV[1]) then
            redis.call('HINCRBY', KEYS[1], 'seq', 1)
            return 1
        else
            return 0
        end
        """
        result = await redis.eval(script, 1, key, sequence)
        return bool(result)
