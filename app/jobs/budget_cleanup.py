import asyncio
import logging
from datetime import datetime, timezone

from fastapi.concurrency import run_in_threadpool

from app.dependencies import SessionLocal
from app.domain.budgets.service import BudgetService

logger = logging.getLogger(__name__)

async def budget_cleanup_worker(shutdown_event: asyncio.Event, interval_seconds: int = 60):
    """
    Background worker to release expired budget reservations.
    Periodically checks for ACTIVE reservations that have passed their expires_at.
    """
    logger.info("Starting Budget Cleanup Worker")
    
    while not shutdown_event.is_set():
        try:
            from app.adapters.redis.client import get_redis_client
            redis = await get_redis_client()
            service = BudgetService(redis)
            # release_expired_reservations is now async returning 0 or placeholder
            count = service.release_expired_reservations(limit=100)
            if count > 0:
                logger.info(f"Released {count} expired budget reservations.")
        except Exception as e:
            logger.error(f"Error in budget cleanup worker: {e}", exc_info=True)
            
        try:
            # Wait for interval or shutdown signal
            await asyncio.wait_for(shutdown_event.wait(), timeout=interval_seconds)
        except asyncio.TimeoutError:
            continue
            
    logger.info("Budget Cleanup Worker Stopped")
