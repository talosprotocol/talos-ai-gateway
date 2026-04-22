"""Budget Service (Redis Backed)."""
import logging
import time
from typing import Optional, Dict, List
from decimal import Decimal
from datetime import datetime, timezone, timedelta

import redis.asyncio as redis
from sqlalchemy import select, func, update
from sqlalchemy.orm import Session

from app.domain.budgets.pricing import PricingRegistry, get_pricing_registry
from app.adapters.postgres.models import BudgetScope, BudgetReservation
from app.utils.id import uuid7

logger = logging.getLogger(__name__)

# Constants
EXPIRY_SECONDS = 900  # 15 minutes reservation TTL
LUA_RESERVE = """
local team_res_key = KEYS[1]
local team_used_key = KEYS[2]
local key_res_key = KEYS[3]
local key_used_key = KEYS[4]

local limit_team = tonumber(ARGV[1])
local limit_key = tonumber(ARGV[2])
local cost = tonumber(ARGV[3])
local ttl = tonumber(ARGV[4])

local tr = tonumber(redis.call('get', team_res_key) or 0)
local tu = tonumber(redis.call('get', team_used_key) or 0)
local kr = tonumber(redis.call('get', key_res_key) or 0)
local ku = tonumber(redis.call('get', key_used_key) or 0)

-- Check Limits
if (tr + tu + cost > limit_team) then
    return {0, "Team Limit Exceeded", tr + tu + cost, limit_team}
end
if (kr + ku + cost > limit_key) then
    return {0, "Key Limit Exceeded", kr + ku + cost, limit_key}
end

-- Apply Reservation
redis.call('incrbyfloat', team_res_key, cost)
redis.call('incrbyfloat', key_res_key, cost)

-- Refresh TTL
redis.call('expire', team_res_key, ttl)
redis.call('expire', key_res_key, ttl)
redis.call('expire', team_used_key, ttl)
redis.call('expire', key_used_key, ttl)

return {1, "OK", tr + tu + cost}
"""

LUA_SETTLE = """
local team_res_key = KEYS[1]
local team_used_key = KEYS[2]
local key_res_key = KEYS[3]
local key_used_key = KEYS[4]
local reservation_key = KEYS[5]

local reserved_cost = tonumber(ARGV[1])
local actual_cost = tonumber(ARGV[2])
local ttl = tonumber(ARGV[3])

-- Check if already settled
if redis.call('exists', reservation_key) == 1 then
    return 0 -- Already Processed
end

-- Release Reservation, Apply Usage
redis.call('incrbyfloat', team_res_key, -reserved_cost)
redis.call('incrbyfloat', key_res_key, -reserved_cost)

redis.call('incrbyfloat', team_used_key, actual_cost)
redis.call('incrbyfloat', key_used_key, actual_cost)

-- Mark request as settled (store simple flag with TTL)
redis.call('setex', reservation_key, ttl, "1")

-- Refresh TTL
redis.call('expire', team_res_key, ttl)
redis.call('expire', key_res_key, ttl)
redis.call('expire', team_used_key, ttl)
redis.call('expire', key_used_key, ttl)

return 1
"""

class BudgetExceededError(Exception):
    def __init__(self, message: str, remaining: Decimal, limit: Decimal):
        self.message = message
        self.remaining = remaining
        self.limit = limit
        super().__init__(message)

class BudgetService:
    """Redis-backed Budget Service using Lua scripts for atomicity."""
    
    def __init__(self, redis_client: redis.Redis, pricing: Optional[PricingRegistry] = None):
        self.redis = redis_client
        self.pricing = pricing or get_pricing_registry()
        self._reserve_script = self.redis.register_script(LUA_RESERVE)
        self._settle_script = self.redis.register_script(LUA_SETTLE)

    def _get_keys(self, team_id: str, key_id: str, period: str):
        base = f"budget:{period}"
        return (
            f"{base}:team:{team_id}:reserved",
            f"{base}:team:{team_id}:used",
            f"{base}:key:{key_id}:reserved",
            f"{base}:key:{key_id}:used"
        )

    async def reserve(
        self,
        db: Session,
        request_id: str,
        team_id: str,
        key_id: str,
        budget_mode: str,
        estimate_usd: Decimal,
        limit_usd_team: Decimal,
        limit_usd_key: Decimal,
        overdraft_usd: Decimal
    ) -> Dict[str, str]:
        
        period = datetime.now(timezone.utc).strftime("%Y-%m")
        period_start = datetime.now(timezone.utc).replace(day=1, hour=0, minute=0, second=0, microsecond=0).date()
        keys = self._get_keys(team_id, key_id, period)
        
        # Adjust limits with overdraft
        eff_limit_team = float(limit_usd_team + overdraft_usd)
        eff_limit_key = float(limit_usd_key + overdraft_usd)
        cost = float(estimate_usd)

        # MODE: OFF -> No Check
        if budget_mode == "off":
            return self._headers("off", "cache", 999999, eff_limit_team, 0)
        
        # WARN/HARD -> Execute Script (Atomic Check)
        run_limit_team = eff_limit_team if budget_mode == "hard" else 999999999
        run_limit_key = eff_limit_key if budget_mode == "hard" else 999999999

        try:
            res = await self._reserve_script(
                keys=keys,
                args=[run_limit_team, run_limit_key, cost, 30*86400] # 30 day TTL for Persistence Worker
            )
        except Exception as e:
            logger.error(f"Redis Error in reserve: {e}")
            if budget_mode == "hard":
                raise e
            return {}

        allowed = res[0]
        msg = res[1]
        usage = Decimal(str(res[2])) if len(res) > 2 else Decimal(0)

        effective_limit = min(limit_usd_team, limit_usd_key)
        remaining = Decimal(effective_limit) + overdraft_usd - usage

        if allowed == 0:
            if budget_mode == "hard":
                raise BudgetExceededError(msg, remaining, Decimal(effective_limit))
            # WARN mode: Log but allow
            logger.warning(f"Budget WARN: {msg}")

        # --- Phase 15: DB Persistence ---
        # 1. Record Reservation in DB
        expires_at = datetime.now(timezone.utc) + timedelta(minutes=10)
        res_row = BudgetReservation(
            id=str(uuid7()),
            request_id=request_id,
            scope_team_id=team_id,
            scope_key_id=key_id,
            reserved_usd=estimate_usd,
            status="ACTIVE",
            expires_at=expires_at
        )
        db.add(res_row)

        # 2. Update Scopes (Sync Reservation to DB)
        if team_id != "none":
            db.execute(
                update(BudgetScope)
                .where(BudgetScope.scope_type == "team", BudgetScope.scope_id == team_id, BudgetScope.period_start == period_start)
                .values(reserved_usd=BudgetScope.reserved_usd + estimate_usd)
            )
        if key_id != "none":
            db.execute(
                update(BudgetScope)
                .where(BudgetScope.scope_type == "virtual_key", BudgetScope.scope_id == key_id, BudgetScope.period_start == period_start)
                .values(reserved_usd=BudgetScope.reserved_usd + estimate_usd)
            )
        
        # We commit here to ensure the reservation is saved even if the subsequent upstream call fails.
        db.commit()

        return self._headers(budget_mode, "redis", remaining, effective_limit, usage)

    async def settle(
        self,
        db: Session,
        request_id: str,
        team_id: str,
        key_id: str,
        estimate_usd: Decimal,
        actual_cost_usd: Decimal
    ):
        """Settle reservation in both Redis and Database."""
        period = datetime.now(timezone.utc).strftime("%Y-%m")
        period_start = datetime.now(timezone.utc).date().replace(day=1)
        keys = self._get_keys(team_id, key_id, period)
        reservation_key = f"budget:req:{request_id}:settled"
        
        # 1. Redis Settlement (Atomic with Lua)
        all_keys = list(keys) + [reservation_key]
        res = await self._settle_script(
            keys=all_keys,
            args=[float(estimate_usd), float(actual_cost_usd), 30*86400]
        )
        
        # If already settled in Redis (res == 0), skip DB to maintain idempotency
        if res == 0:
            return

        # 2. Database Settlement
        # Update Reservation status
        db.execute(
            update(BudgetReservation)
            .where(BudgetReservation.request_id == request_id)
            .values(status="SETTLED")
        )

        # Update Scopes (reserved_usd -= estimate, used_usd += actual)
        if team_id != "none":
            db.execute(
                update(BudgetScope)
                .where(BudgetScope.scope_type == "team", BudgetScope.scope_id == team_id, BudgetScope.period_start == period_start)
                .values(reserved_usd=BudgetScope.reserved_usd - estimate_usd, used_usd=BudgetScope.used_usd + actual_cost_usd)
            )
            
        if key_id != "none":
            db.execute(
                update(BudgetScope)
                .where(BudgetScope.scope_type == "virtual_key", BudgetScope.scope_id == key_id, BudgetScope.period_start == period_start)
                .values(reserved_usd=BudgetScope.reserved_usd - estimate_usd, used_usd=BudgetScope.used_usd + actual_cost_usd)
            )

        db.commit()

    def _headers(self, mode, source, rem, limit, used):
        return {
            "X-Talos-Budget-Mode": mode,
            "X-Talos-Budget-Source": source,
            "X-Talos-Budget-Remaining-Usd": f"{rem:.6f}",
            "X-Talos-Budget-Limit-Usd": f"{limit:.6f}",
            "X-Talos-Budget-Used-Usd": f"{used:.6f}"
        }

    async def release_expired_reservations(self, db: Session, limit: int = 100) -> int:
        """
        Find and release expired reservations from DB and sync with Redis.
        Status: ACTIVE -> EXPIRED
        Subtracts from BudgetScope.reserved_usd and Redis :reserved counters.
        """
        now = datetime.now(timezone.utc)
        expired = db.query(BudgetReservation).filter(
            BudgetReservation.status == "ACTIVE",
            BudgetReservation.expires_at < now
        ).limit(limit).all()

        if not expired:
            return 0

        count = 0
        
        # Group by (team_id, key_id, period_start) to batch DB updates
        updates = {}

        for res in expired:
            try:
                # 1. Update Reservation Status
                res.status = "EXPIRED"

                # 2. Sync Redis
                # Important: Redis keys are per-month. Use res.created_at to find the right month.
                res_period = res.created_at.strftime("%Y-%m")
                res_period_start = res.created_at.date().replace(day=1)
                
                keys = self._get_keys(res.scope_team_id, res.scope_key_id, res_period)
                neg_cost = -float(res.reserved_usd)
                
                if res.scope_team_id != "none":
                    await self.redis.incrbyfloat(keys[0], neg_cost)
                    # Batch DB updates
                    k = ("team", res.scope_team_id, res_period_start)
                    updates[k] = updates.get(k, Decimal("0")) + res.reserved_usd
                    
                if res.scope_key_id != "none":
                    await self.redis.incrbyfloat(keys[2], neg_cost)
                    k = ("virtual_key", res.scope_key_id, res_period_start)
                    updates[k] = updates.get(k, Decimal("0")) + res.reserved_usd
                
                count += 1
            except Exception as e:
                logger.error(f"Failed to release reservation {res.request_id}: {e}")

        # 3. Apply Batched DB Updates to BudgetScope
        for (s_type, s_id, p_start), amount in updates.items():
            db.execute(
                update(BudgetScope)
                .where(
                    BudgetScope.scope_type == s_type,
                    BudgetScope.scope_id == s_id,
                    BudgetScope.period_start == p_start
                )
                .values(reserved_usd=BudgetScope.reserved_usd - amount)
            )

        db.commit()
        return count

    async def reconcile_drift(self, db: Session, fix_drift: bool = True) -> int:
        """
        Sum all ACTIVE reservations in DB for the scope's period and compare with BudgetScope.reserved_usd.
        Optionally fixes the drift in DB and overwrites Redis counters.
        """
        now = datetime.now(timezone.utc)
        errors = 0

        # Get all scopes that might have active reservations (current month and potentially previous if long-lived)
        # For simplicity and Phase 15 requirements, we focus on current period scopes.
        period_start = now.replace(day=1).date()
        scopes = db.query(BudgetScope).filter(BudgetScope.period_start == period_start).all()

        for scope in scopes:
            # 1. Sum ACTIVE reservations that belong to this scope's period
            # We filter by created_at month matching scope.period_start
            next_month = (scope.period_start + timedelta(days=32)).replace(day=1)
            
            stmt = select(func.sum(BudgetReservation.reserved_usd)).where(
                BudgetReservation.status == 'ACTIVE',
                BudgetReservation.created_at >= datetime.combine(scope.period_start, datetime.min.time(), tzinfo=timezone.utc),
                BudgetReservation.created_at < datetime.combine(next_month, datetime.min.time(), tzinfo=timezone.utc)
            )

            if scope.scope_type == 'team':
                stmt = stmt.where(BudgetReservation.scope_team_id == scope.scope_id)
            else:
                stmt = stmt.where(BudgetReservation.scope_key_id == scope.scope_id)

            real_reserved = db.scalar(stmt) or Decimal("0")

            # 2. Compare
            if abs(scope.reserved_usd - real_reserved) > Decimal("0.000001"):
                logger.critical(
                    f"BUDGET DRIFT DETECTED: {scope.scope_type}:{scope.scope_id} "
                    f"Ledger={scope.reserved_usd}, Actual={real_reserved}"
                )
                errors += 1

                if fix_drift:
                    logger.warning(f"Auto-healing drift for {scope.scope_id}")
                    scope.reserved_usd = real_reserved
                    
                    # 3. Sync to Redis (Overwrite)
                    period_str = scope.period_start.strftime("%Y-%m")
                    base = f"budget:{period_str}"
                    r_type = "team" if scope.scope_type == "team" else "key"
                    redis_key = f"{base}:{r_type}:{scope.scope_id}:reserved"
                    await self.redis.set(redis_key, float(real_reserved))

        if fix_drift and errors > 0:
            db.commit()

        return errors
