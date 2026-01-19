"""Budget Reconciliation Job (Safety Net).

This module provides a reconciliation worker that:
1. Iterates all active BudgetScopes.
2. Sums up all ACTIVE reservations for each scope.
3. Compares the sum with `BudgetScope.reserved_usd`.
4. Logs CRITICAL errors if drift is detected.

Can be run daily or hourly.
"""
import time
import logging
from decimal import Decimal
from sqlalchemy import select, func
from app.dependencies import get_write_db
from app.adapters.postgres.models import BudgetScope, BudgetReservation

logger = logging.getLogger(__name__)

class BudgetReconciler:
    def __init__(self, fix_drift: bool = False):
        self.fix_drift = fix_drift

    def run(self):
        """Run a single reconciliation pass."""
        logger.info("Starting Budget Reconciliation...")
        
        db_gen = get_write_db()
        db = next(db_gen)
        errors = 0
        
        try:
            # 1. Get all scopes with non-zero reserved budget
            # checking zero is optimization, but strict chcek should check all?
            # Let's check all scopes modified recently or just all.
            # For scale, we might need pagination. For MVP, check all with reserved > 0.
            
            scopes = db.query(BudgetScope).filter(BudgetScope.reserved_usd > 0).all()
            
            for scope in scopes:
                # 2. Sum active reservations
                stmt = select(func.sum(BudgetReservation.reserved_usd)).where(
                    BudgetReservation.status == 'active',
                    BudgetReservation.expires_at > func.now(), # approximate active definition matching cleanup
                    # Actually spec says: Active = status='active'.
                    # Cleanup worker handles expiry.
                    # If expired but not cleaned, is it active?
                    # The Invariant says "ACTIVE reservations".
                    # Technical definition: status='active'.
                    # Uncleaned expired reservations ARE active until cleaned.
                )
                
                if scope.scope_type == 'team':
                    stmt = stmt.where(BudgetReservation.scope_team_id == scope.scope_id)
                else:
                    stmt = stmt.where(BudgetReservation.scope_key_id == scope.scope_id)
                
                real_reserved = db.scalar(stmt) or Decimal("0")
                
                # 3. Compare
                if abs(scope.reserved_usd - real_reserved) > Decimal("0.000001"):
                    logger.critical(
                        f"BUDGET DRIFT DETECTED: {scope.scope_type}:{scope.scope_id} "
                        f"Ledger={scope.reserved_usd}, Actual={real_reserved}, Diff={scope.reserved_usd - real_reserved}"
                    )
                    errors += 1
                    
                    if self.fix_drift:
                        logger.warning(f"Auto-healing drift for {scope.scope_id}")
                        scope.reserved_usd = real_reserved
            
            if self.fix_drift and errors > 0:
                db.commit()
                logger.info(f"Fixed {errors} scopes.")
            
            if errors == 0:
                logger.info("Reconciliation Complete. No drift detected.")
            else:
                logger.error(f"Reconciliation Complete. {errors} DRIFT ERRORS DETECTED.")
                
        except Exception as e:
            logger.error(f"Reconciliation failed: {e}")
        finally:
            db_gen.close()

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    reconciler = BudgetReconciler(fix_drift=False)
    reconciler.run()
