import asyncio
import logging
from datetime import datetime, timezone

from fastapi.concurrency import run_in_threadpool

from app.dependencies import SessionLocal
from app.domain.budgets.service import BudgetService

logger = logging.getLogger(__name__)

async def budget_cleanup_worker(shutdown_event: asyncio.Event, interval_seconds: int = 60):
    """
    Background worker to release expired budget reservations and reconcile drift.
    Periodically checks for ACTIVE reservations that have passed their expires_at.
    """
    logger.info("Starting Budget Cleanup Worker")
    
    while not shutdown_event.is_set():
        try:
            from app.adapters.redis.client import get_redis_client
            redis = await get_redis_client()
            service = BudgetService(redis)
            
            with SessionLocal() as db:
                # 1. Release Expired Reservations
                count = await service.release_expired_reservations(db, limit=100)
                if count > 0:
                    logger.info(f"Released {count} expired budget reservations.")
                
                # 2. Reconcile Drift (Ensures Ledger and Redis are in sync with Active set)
                # Running this every minute is safe for small-medium scale.
                drifts = await service.reconcile_drift(db, fix_drift=True)
                if drifts > 0:
                    logger.warning(f"Reconciled {drifts} budget scopes with drift.")
                    
        except Exception as e:
            logger.error(f"Error in budget cleanup worker: {e}", exc_info=True)
            
        try:
            # Wait for interval or shutdown signal
            await asyncio.wait_for(shutdown_event.wait(), timeout=interval_seconds)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            continue
            
    logger.info("Budget Cleanup Worker Stopped")
