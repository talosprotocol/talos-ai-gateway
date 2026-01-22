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
            if not SessionLocal:
                logger.warning("Budget cleanup worker: DB not configured")
            else:
                db = SessionLocal()
                try:
                    service = BudgetService(db)
                    # release_expired_reservations is sync
                    count = await run_in_threadpool(service.release_expired_reservations, limit=100)
                    if count > 0:
                        logger.info(f"Released {count} expired budget reservations.")
                finally:
                    db.close()
        except Exception as e:
            logger.error(f"Error in budget cleanup worker: {e}", exc_info=True)
            
        try:
            # Wait for interval or shutdown signal
            await asyncio.wait_for(shutdown_event.wait(), timeout=interval_seconds)
        except asyncio.TimeoutError:
            continue
            
    logger.info("Budget Cleanup Worker Stopped")
