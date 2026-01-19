"""Budget Cleanup Job.

This module provides the worker logic to release expired budget reservations.
It ensures that 'reserved_usd' in 'budget_scope' correctly reflects only ACTIVE reservations.
"""
import time
import logging
import signal
import sys
from datetime import datetime

from app.dependencies import get_write_db
from app.domain.budgets.service import BudgetService

logger = logging.getLogger(__name__)

class BudgetCleanupWorker:
    def __init__(self, interval_seconds: int = 60, batch_size: int = 100):
        self.interval = interval_seconds
        self.batch_size = batch_size
        self.running = False
        self.db = None
    
    def run(self):
        """Start the cleanup loop."""
        logger.info("Starting Budget Cleanup Worker...")
        self.running = True
        
        # Signal handling
        signal.signal(signal.SIGINT, self._handle_exit)
        signal.signal(signal.SIGTERM, self._handle_exit)
        
        # Main Loop
        while self.running:
            try:
                self._process_batch()
            except Exception as e:
                logger.error(f"Error in budget cleanup loop: {e}")
            
            # Sleep with interrupt check
            self._sleep()
            
        logger.info("Budget Cleanup Worker stopped.")

    def _process_batch(self):
        # We start a fresh session per batch to keep transactions short
        # Dependencies generator usage manually
        db_gen = get_write_db()
        try:
            db = next(db_gen)
            if not db:
                 logger.error("Failed to acquire DB session for cleanup")
                 return
            
            service = BudgetService(db)
            count = service.release_expired_reservations(limit=self.batch_size)
            
            if count > 0:
                logger.info(f"Released {count} expired reservations")
            
        except Exception as e:
            logger.error(f"Batch processing failed: {e}")
        finally:
            if db:
                db_gen.close()

    def _sleep(self):
        for _ in range(self.interval):
            if not self.running: break
            time.sleep(1)

    def _handle_exit(self, signum, frame):
        logger.info("Shutdown signal received")
        self.running = False

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    
    # Simple CLI
    import os
    interval = int(os.getenv("BUDGET_CLEANUP_INTERVAL", "60"))
    worker = BudgetCleanupWorker(interval_seconds=interval)
    worker.run()
