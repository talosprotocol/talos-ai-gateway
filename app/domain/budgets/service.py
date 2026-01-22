"""Budget Service."""
from typing import Optional, Tuple, Dict, Any
from decimal import Decimal, ROUND_UP
from datetime import datetime, timedelta
import logging
from sqlalchemy import select, and_, exists
from sqlalchemy.orm import Session
from sqlalchemy.dialects.postgresql import insert

from app.adapters.postgres.models import (
    BudgetScope, BudgetReservation, UsageRollupDaily, Team, VirtualKey
)
from app.domain.budgets.pricing import PricingRegistry, get_pricing_registry
from app.utils.id import uuid7

logger = logging.getLogger(__name__)

# Constants
EXPIRY_SECONDS = 900 # 15 minutes
ALERT_COOLDOWN_SECONDS = 3600 # 1 hour

class BudgetExceededError(Exception):
    def __init__(self, message: str, remaining: Decimal, limit: Decimal):
        self.message = message
        self.remaining = remaining
        self.limit = limit
        super().__init__(message)

class BudgetService:
    """Core domain service for Budget enforcement and management."""
    
    def __init__(self, db: Session, pricing: PricingRegistry = None):
        self.db = db
        self.pricing = pricing or get_pricing_registry()

    def reserve(
        self,
        request_id: str,
        team_id: str,
        key_id: str,
        budget_mode: str,
        estimate_usd: Decimal,
        limit_usd_team: Decimal,
        limit_usd_key: Decimal,
        overdraft_usd: Decimal
    ) -> Dict[str, str]:
        """
        Attempt to reserve budget execution.
        
        Modes:
        - OFF: Always allow. No DB locks.
        - WARN: Always allow, but update state/alerts. No DB locks unless alerting.
        - HARD: Strict check. atomic update with dual-scope locking.
        
        Returns: Dict of headers to return.
        Raises: BudgetExceededError if HARD mode and limit exceeded.
        """
        
        period_start = datetime.utcnow().date().replace(day=1)
        
        # 1. Performance Optimization: OFF/WARN do not lock or create reservations
        if budget_mode in ("off", "warn"):
            # Compute headers from cache/non-locking reads if possible, 
            # here we do a simple non-locking read for current usage to provide headers.
            # In a high-scale prod, this might come from Redis.
            
            # Simple non-locking read for headers
            team_usage = self.db.query(BudgetScope).filter(
                BudgetScope.scope_type == "team",
                BudgetScope.scope_id == team_id,
                BudgetScope.period_start == period_start
            ).first()
            
            key_usage = self.db.query(BudgetScope).filter(
                BudgetScope.scope_type == "virtual_key",
                BudgetScope.scope_id == key_id,
                BudgetScope.period_start == period_start
            ).first()
            
            used = max(
                team_usage.used_usd if team_usage else Decimal("0"),
                key_usage.used_usd if key_usage else Decimal("0")
            )
            
            limit = min(limit_usd_team, limit_usd_key)
            # remaining = (limit + overdraft) - (used + reserved)
            # Since we don't lock, reserved might be slightly off, but that's acceptable for WARN/OFF
            res_val = max(
                team_usage.reserved_usd if team_usage else Decimal("0"),
                key_usage.reserved_usd if key_usage else Decimal("0")
            )
            remaining = (limit + overdraft_usd) - (used + res_val)

            if budget_mode == "warn" and remaining <= 0:
                # WARN mode needs to check/emit alert once per hour
                # This requires a write, but only for the alert state
                if team_usage:
                    self._check_and_emit_alert(team_usage, "team", remaining)
                if key_usage:
                    self._check_and_emit_alert(key_usage, "virtual_key", remaining)
                self.db.commit()

            return self._get_headers(
                mode=budget_mode, 
                source="cache" if budget_mode == "off" else "ledger",
                remaining=remaining,
                limit=limit,
                used=used
            )

        # 2. HARD MODE: Atomic reservation with deterministic dual-scope locking
        try:
            # DETERMINISTIC LOCK ORDER: Team first, then Key
            # This prevents deadlocks between concurrent requests for the same team across different keys
            
            # Fetch Team Scope (LOCKED)
            team_scope = self._get_or_create_scope_locked(
                "team", team_id, period_start, limit_usd_team, overdraft_usd
            )
            
            # Fetch Key Scope (LOCKED)
            key_scope = self._get_or_create_scope_locked(
                "virtual_key", key_id, period_start, limit_usd_key, overdraft_usd
            )
            
            # Calculate State
            team_avail = (team_scope.limit_usd + team_scope.overdraft_usd) - (team_scope.used_usd + team_scope.reserved_usd)
            key_avail = (key_scope.limit_usd + key_scope.overdraft_usd) - (key_scope.used_usd + key_scope.reserved_usd)
            
            effective_remaining = min(team_avail, key_avail)
            
            logger.info(f"Budget Reservation Check: req={request_id} team={team_id} team_avail={team_avail} key={key_id} key_avail={key_avail} effective={effective_remaining} estimate={estimate_usd}")
            
            if effective_remaining < estimate_usd:
                # BLOCK
                raise BudgetExceededError(
                    "Budget exceeded", 
                    remaining=effective_remaining,
                    limit=min(team_scope.limit_usd, key_scope.limit_usd)
                )
            
            # Apply Reservation
            team_scope.reserved_usd += estimate_usd
            key_scope.reserved_usd += estimate_usd
            
            # Create Reservation Record
            res = BudgetReservation(
                id=str(uuid7()),
                request_id=request_id,
                scope_team_id=team_id,
                scope_key_id=key_id,
                reserved_usd=estimate_usd,
                status="ACTIVE",
                expires_at=datetime.utcnow().replace(second=0, microsecond=0) + 
                           timedelta(seconds=EXPIRY_SECONDS) 
            )
            self.db.add(res)
            self.db.commit()
            
            return self._get_headers(
                mode="hard", source="ledger",
                remaining=effective_remaining - estimate_usd,
                limit=min(team_scope.limit_usd, key_scope.limit_usd),
                used=max(team_scope.used_usd, key_scope.used_usd)
            )

        except BudgetExceededError:
            self.db.rollback()
            raise
        except Exception as e:
            logger.error(f"Error in budget reserve: {e}")
            self.db.rollback()
            raise e

        return {}

    def settle(
        self,
        request_id: str,
        actual_cost_usd: Decimal
    ):
        """
        Settle a reservation.
        Idempotent: Checks if request_id is already settled.
        """
        try:
            # Atomic: Find reservation FOR UPDATE
            res = self.db.query(BudgetReservation).\
                filter(BudgetReservation.request_id == request_id).\
                with_for_update().first()
            
            if not res or res.status != "ACTIVE":
                # Already settled, expired, or released (or never reserved in OFF/WARN)
                return 

            period_start = datetime.utcnow().date().replace(day=1)
            
            # Decrease Reserved and Increase Used in the same transaction
            # Fetch scopes in deterministic order: Team then Key
            team_scope = self._get_scope(res.scope_team_id, "team", period_start)
            key_scope = self._get_scope(res.scope_key_id, "virtual_key", period_start)
            
            if team_scope:
                # Atomic update
                team_scope.reserved_usd = max(Decimal(0), team_scope.reserved_usd - res.reserved_usd)
                team_scope.used_usd += actual_cost_usd
                
            if key_scope:
                # Atomic update
                key_scope.reserved_usd = max(Decimal(0), key_scope.reserved_usd - res.reserved_usd)
                key_scope.used_usd += actual_cost_usd
                
            res.status = "SETTLED"
            self.db.commit()
            
        except Exception as e:
            logger.error(f"Error settling budget for {request_id}: {e}")
            self.db.rollback()
            # Non-blocking for response, but critical for accounting

    def _get_or_create_scope_locked(self, s_type, s_id, period, limit, overdraft):
        # Insert if not exists, then select for update
        # Using on_conflict approach usually, or check-then-create
        
        # 1. Try Select for Update
        scope = self.db.query(BudgetScope).\
            filter(
                BudgetScope.scope_type == s_type,
                BudgetScope.scope_id == s_id,
                BudgetScope.period_start == period
            ).with_for_update().first()
            
        if not scope:
            # Create
            stmt = insert(BudgetScope).values(
                id=str(uuid7()),
                scope_type=s_type,
                scope_id=s_id,
                period_start=period,
                limit_usd=limit,
                overdraft_usd=overdraft,
                used_usd=0,
                reserved_usd=0
            ).on_conflict_do_nothing()
            self.db.execute(stmt)
            
            # Select again
            scope = self.db.query(BudgetScope).\
                filter(
                    BudgetScope.scope_type == s_type,
                    BudgetScope.scope_id == s_id,
                    BudgetScope.period_start == period
                ).with_for_update().first()
                
            if not scope:
                # Should strict fail?
                raise Exception(f"Failed to create budget scope for {s_type}:{s_id}")

        return scope
        
    def _get_scope(self, scope_id, scope_type, period):
        return self.db.query(BudgetScope).filter(
             BudgetScope.scope_type == scope_type,
             BudgetScope.scope_id == scope_id,
             BudgetScope.period_start == period
        ).with_for_update().first()

    def _check_and_emit_alert(self, scope: BudgetScope, s_type: str, avail: Decimal):
        now = datetime.utcnow()
        if scope.last_alert_at:
             diff = (now - scope.last_alert_at).total_seconds()
             if diff < ALERT_COOLDOWN_SECONDS:
                 return # Suppress
        
        # Emit Alert logic (log for now)
        logger.warning(f"BUDGET ALERT: {s_type} {scope.scope_id} budget exhausted. Remaining: {avail}")
        
        scope.last_alert_at = now

    def _get_headers(self, mode, source, remaining, limit, used):
        return {
            "X-Talos-Budget-Mode": mode,
            "X-Talos-Budget-Source": source,
            "X-Talos-Budget-Remaining-Usd": str(remaining),
            "X-Talos-Budget-Limit-Usd": str(limit),
            "X-Talos-Budget-Used-Usd": str(used)
        }

    def release_expired_reservations(self, limit: int = 100) -> int:
        """
        Release any active reservations that have expired.
        Returns number of released reservations.
        """
        now = datetime.utcnow()
        released_count = 0
        
        try:
            # 1. Find expired active reservations
            # Lock them to prevent race with settle
            expired = self.db.query(BudgetReservation).\
                filter(
                    BudgetReservation.status == "ACTIVE",
                    BudgetReservation.expires_at <= now
                ).with_for_update(skip_locked=True).limit(limit).all()
            
            for res in expired:
                # Infer period from creation time
                res_period = res.created_at.date().replace(day=1)
                
                # Decrement Scopes directly
                # Lock order: Team then Key
                self.db.query(BudgetScope).filter(
                    BudgetScope.scope_type == "team",
                    BudgetScope.scope_id == res.scope_team_id,
                    BudgetScope.period_start == res_period
                ).with_for_update().update({
                    BudgetScope.reserved_usd: BudgetScope.reserved_usd - res.reserved_usd
                }, synchronize_session=False)

                self.db.query(BudgetScope).filter(
                    BudgetScope.scope_type == "virtual_key",
                    BudgetScope.scope_id == res.scope_key_id,
                    BudgetScope.period_start == res_period
                ).with_for_update().update({
                    BudgetScope.reserved_usd: BudgetScope.reserved_usd - res.reserved_usd
                }, synchronize_session=False)

                res.status = "EXPIRED"
                released_count += 1
            
            self.db.commit()
            return released_count
            
        except Exception as e:
            logger.error(f"Error in release_expired_reservations: {e}")
            self.db.rollback()
            return 0

