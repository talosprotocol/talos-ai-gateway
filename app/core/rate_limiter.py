import time
import abc
from typing import Optional, Tuple
import logging

logger = logging.getLogger(__name__)

class RateLimitStorage(abc.ABC):
    @abc.abstractmethod
    async def consume(self, key: str, capacity: int, refill_rate: float, cost: int = 1) -> Tuple[bool, int, float]:
        """
        Consume tokens from the bucket.
        
        Args:
            key: Unique identifier for the bucket.
            capacity: Max tokens in the bucket.
            refill_rate: Tokens added per second.
            cost: Tokens to consume.
            
        Returns:
            (allowed, remaining_tokens, reset_after_seconds)
        """
        pass

class MemoryRateLimitStorage(RateLimitStorage):
    def __init__(self):
        # key -> (tokens, last_refill_timestamp)
        self._buckets: dict[str, Tuple[float, float]] = {}

    async def consume(self, key: str, capacity: int, refill_rate: float, cost: int = 1) -> Tuple[bool, int, float]:
        now = time.time()
        tokens, last_refill = self._buckets.get(key, (float(capacity), now))
        
        # Calculate refill
        delta = now - last_refill
        added = delta * refill_rate
        tokens = min(float(capacity), tokens + added)
        
        if tokens >= cost:
            tokens -= cost
            self._buckets[key] = (tokens, now)
            return True, int(tokens), 0.0
        else:
            # Not enough tokens
            self._buckets[key] = (tokens, now)
            needed = cost - tokens
            wait_time = needed / refill_rate
            return False, int(tokens), wait_time

class RedisRateLimitStorage(RateLimitStorage):
    def __init__(self, redis_client):
        self.redis = redis_client

    async def consume(self, key: str, capacity: int, refill_rate: float, cost: int = 1) -> Tuple[bool, int, float]:
        # Using a Lua script for atomicity
        lua_script = """
        local key = KEYS[1]
        local capacity = tonumber(ARGV[1])
        local refill_rate = tonumber(ARGV[2])
        local cost = tonumber(ARGV[3])
        local now = tonumber(ARGV[4])
        
        local state = redis.call('HMGET', key, 'tokens', 'last_refill')
        local tokens = tonumber(state[1])
        local last_refill = tonumber(state[2])
        
        if not tokens then
            tokens = capacity
            last_refill = now
        end
        
        local delta = now - last_refill
        local added = delta * refill_rate
        tokens = math.min(capacity, tokens + added)
        
        local allowed = 0
        local wait_time = 0
        
        if tokens >= cost then
            tokens = tokens - cost
            allowed = 1
        else
            local needed = cost - tokens
            wait_time = needed / refill_rate
        end
        
        redis.call('HMSET', key, 'tokens', tokens, 'last_refill', now)
        -- Set expiry to when bucket would be full from empty (safety cleanup)
        redis.call('EXPIRE', key, math.ceil(capacity / refill_rate))
        
        return {allowed, tokens, wait_time}
        """
        
        try:
            now = time.time()
            # Redis client is async
            result = await self.redis.eval(lua_script, 1, key, capacity, refill_rate, cost, now)
            allowed = bool(result[0])
            remaining = int(float(result[1]))
            reset_after = float(result[2])
            return allowed, remaining, reset_after
        except Exception as e:
            logger.error(f"Redis rate limit error: {e}")
            
            # Runtime Failure Policy (Normative)
            # If MODE=prod: Fail Closed
            # If MODE=dev: Configurable fail open/closed
            import os
            mode = os.getenv("MODE", "dev").lower()
            
            if mode == "prod":
                # Prod MUST fail closed
                # Caller (middleware) should map this to 503 SERVER_OVERLOADED
                # We return a special signal or raise. Raising ensures middleware catches it.
                raise RuntimeError("Redis runtime failure in PROD") from e
            else:
                # Dev
                fail_open = os.getenv("RATE_LIMIT_DEV_FAIL_OPEN", "false").lower() == "true"
                if fail_open:
                    return True, capacity, 0.0
                else:
                    # Dev fail closed (return 503 RATE_LIMITER_UNAVAILABLE)
                    raise RuntimeError("Redis runtime failure in DEV") from e

class RateLimiter:
    def __init__(self, storage: RateLimitStorage):
        self.storage = storage

    async def check(self, key: str, limit: str) -> Tuple[bool, dict]:
        """
        Check rate limit.
        limit format: "requests/window_seconds", e.g., "5/60" (5 req per 60s)
        """
        try:
            count, seconds = map(int, limit.split('/'))
            refill_rate = count / seconds
            
            allowed, remaining, reset_after = await self.storage.consume(key, count, refill_rate)
            
            headers = {
                "X-RateLimit-Limit": str(count),
                "X-RateLimit-Remaining": str(remaining),
                "X-RateLimit-Reset": str(int(time.time() + reset_after))
            }
            
            if not allowed:
                 import math
                 headers["Retry-After"] = str(int(math.ceil(reset_after)))
            
            return allowed, headers
        except ValueError:
            logger.error(f"Invalid limit format: {limit}")
            return True, {}

    async def check_throughput(self, key: str, rps: float, burst: int) -> Tuple[bool, dict]:
        """
        Check rate limit using RPS and Burst directly.
        """
        allowed, remaining, reset_after = await self.storage.consume(key, burst, rps)
        
        headers = {
            "X-RateLimit-Limit": str(burst),
            "X-RateLimit-Remaining": str(remaining),
            "X-RateLimit-Reset": str(int(time.time() + reset_after))
        }
        
        if not allowed:
             import math
             headers["Retry-After"] = str(int(math.ceil(reset_after)))
        
        return allowed, headers
