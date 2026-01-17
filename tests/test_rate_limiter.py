import pytest
import time
from unittest.mock import patch, MagicMock, AsyncMock
from app.core.rate_limiter import RateLimiter, MemoryRateLimitStorage, RedisRateLimitStorage

@pytest.mark.asyncio
async def test_memory_rate_limiter():
    storage = MemoryRateLimitStorage()
    limiter = RateLimiter(storage)
    
    key = "test_user"
    limit = "5/60" # 5 reqs per 60s -> 0.0833 tokens/sec
    
    # helper to mock time
    with patch("time.time") as mock_time:
        start_time = 1000.0
        mock_time.return_value = start_time
        
        # 1. Consume 5 tokens immediately (should all succeed)
        for i in range(5):
            allowed, headers = await limiter.check(key, limit)
            assert allowed is True
            assert headers["X-RateLimit-Remaining"] == str(4 - i)
            
        # 2. Consume 6th token (should fail)
        allowed, headers = await limiter.check(key, limit)
        assert allowed is False
        assert headers["X-RateLimit-Remaining"] == "0"
        
        # 3. Advance time by 12 seconds (enough for 1 token: 12 * (5/60) = 1.0)
        mock_time.return_value = start_time + 12.0
        allowed, headers = await limiter.check(key, limit)
        assert allowed is True 
        assert headers["X-RateLimit-Remaining"] == "0" # Consumed the 1 we just got

@pytest.mark.asyncio
async def test_redis_rate_limiter_lua():
    # Mock Redis client
    mock_redis = MagicMock()
    # Use AsyncMock for async methods
    mock_redis.eval = AsyncMock()
    
    # Lua script return signature: {allowed, tokens, wait_time}
    # Case 1: Allowed
    mock_redis.eval.return_value = [1, 4, 0.0] 
    
    storage = RedisRateLimitStorage(mock_redis)
    limiter = RateLimiter(storage)
    
    allowed, headers = await limiter.check("redis_key", "5/60")
    assert allowed is True
    assert headers["X-RateLimit-Remaining"] == "4"
    
    # Helper to assert Lua script called
    mock_redis.eval.assert_called_once()  

    # Case 2: Rejected
    mock_redis.eval.return_value = [0, 0, 10.5]
    allowed, headers = await limiter.check("redis_key", "5/60")
    assert allowed is False
    assert headers["X-RateLimit-Remaining"] == "0"
