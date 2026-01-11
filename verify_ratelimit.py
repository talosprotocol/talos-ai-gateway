import asyncio
import os
import sys

# Ensure app in path
sys.path.append(os.getcwd())

from app.adapters.redis.stores import RedisRateLimitStore

async def main():
    print("Testing Redis Rate Limiting...")
    store = RedisRateLimitStore()
    key = "test:rl:verify:1"
    limit = 5
    window = 10
    
    print(f"Checking limit {limit} for key {key} (Window: {window}s)")
    
    for i in range(1, 8):
        res = await store.check_limit(key, limit, window)
        allowed = "ALLOWED" if res.allowed else "DENIED"
        print(f"Request {i}: {allowed} (Rem: {res.remaining})")
        
        if i <= limit and not res.allowed:
            print("[FAILURE] Should be allowed")
            sys.exit(1)
        if i > limit and res.allowed:
            print("[FAILURE] Should be denied")
            sys.exit(1)

    print("[SUCCESS] Redis Rate Limiting verified.")
    
    # Cleanup connection
    from app.adapters.redis.client import close_redis
    await close_redis()

if __name__ == "__main__":
    if not os.getenv("REDIS_URL"):
        # For verification script, default to localhost
        os.environ["REDIS_URL"] = "redis://localhost:6379/0"
    
    asyncio.run(main())
