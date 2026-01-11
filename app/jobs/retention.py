import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import List

from fastapi.concurrency import run_in_threadpool

from app.settings import settings
from app.adapters.redis.client import get_redis_client
from app.adapters.postgres.task_store import PostgresTaskStore
from app.adapters.postgres.session import SessionLocal

logger = logging.getLogger(__name__)

async def retention_worker(shutdown_event: asyncio.Event):
    """
    Background worker to delete expired tasks and cleanup Redis.
    Runs daily (or on startup), respecting shutdown event.
    """
    logger.info("Starting A2A Task Retention Worker")
    
    while not shutdown_event.is_set():
        try:
            days = settings.a2a_task_retention_days
            cutoff = datetime.now(timezone.utc) - timedelta(days=days)
            
            logger.info(f"Running task retention cleanup. Cutoff: {cutoff}")
            
            deleted_ids: List[str] = []
            
            if settings.dev_mode:
                # In Dev Mode, MemoryTaskStore is used.
                # Use Global Memory Store State hack or Skip?
                # The dependency injection uses MemoryTaskStore.
                # If we want to clean memory store, we instantiate it.
                # Since MemoryTaskStore shares _TASK_STATE global, it works.
                from app.adapters.memory_store.stores import MemoryTaskStore
                store = MemoryTaskStore()
                deleted_ids = store.delete_expired_tasks(cutoff)
            else:
                # Production: Postgres
                if not SessionLocal:
                    logger.warning("Retention worker: DB not configured")
                else:
                    db = SessionLocal()
                    try:
                        store = PostgresTaskStore(db)
                        # delete_expired_tasks is sync
                        deleted_ids = await run_in_threadpool(store.delete_expired_tasks, cutoff)
                    finally:
                        db.close()
            
            if deleted_ids:
                logger.info(f"Deleted {len(deleted_ids)} expired tasks from store.")
                
                # Cleanup Redis (Last Event Cache)
                redis = await get_redis_client()
                if redis:
                    keys = [f"a2a:last_event:{tid}" for tid in deleted_ids]
                    if keys:
                        # Batch delete
                        # If huge, chunk it? Assuming retention is run daily, volume might be high but redis delete is fast.
                        # Chunking safe practice:
                        chunk_size = 1000
                        for i in range(0, len(keys), chunk_size):
                            chunk = keys[i:i + chunk_size]
                            await redis.delete(*chunk)
                    logger.info(f"Cleaned up {len(keys)} Redis cache keys.")
            else:
                logger.info("No expired tasks found.")
                
        except Exception as e:
            logger.error(f"Error in retention worker: {e}", exc_info=True)
            
        # Sleep for 24h, verify shutdown signal
        try:
            # 86400 seconds = 24 hours
            await asyncio.wait_for(shutdown_event.wait(), timeout=86400)
        except asyncio.TimeoutError:
            continue # Loop again
            
    logger.info("A2A Task Retention Worker Stopped")
