import asyncio
import logging
from app.adapters.redis.client import get_redis_client

logger = logging.getLogger(__name__)

async def revocation_worker(shutdown_event: asyncio.Event):
    """
    Listens to 'keys:revoked' channel and invalidates local/shared caches.
    In this implementation, since all instances share Redis as the cache,
    an invalidation in one instance is immediately visible to others.
    However, if an instance has a secondary in-memory cache, this worker
    would flush it.
    """
    logger.info("Starting revocation worker...")
    
    while not shutdown_event.is_set():
        try:
            redis_client = await get_redis_client()
            if not redis_client:
                await asyncio.sleep(5)
                continue
                
            pubsub = redis_client.pubsub()
            await pubsub.subscribe("keys:revoked")
            
            logger.info("Subscribed to 'keys:revoked' channel")
            
            while not shutdown_event.is_set():
                message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
                if message:
                    key_hash = message['data'].decode('utf-8')
                    logger.info(f"Revocation signal received for key: {key_hash}")
                    # Invalidate the shared Redis cache entry
                    # (Even though the emitter likely already did this, redundancy is good)
                    await redis_client.delete(f"key:{key_hash}")
                    
            await pubsub.unsubscribe("keys:revoked")
            break
            
        except Exception as e:
            logger.error(f"Error in revocation worker: {e}")
            await asyncio.sleep(5) # Backoff
            
    logger.info("Revocation worker stopped.")
