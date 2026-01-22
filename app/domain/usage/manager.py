"""Usage Manager for Phase 15."""
from decimal import Decimal
from typing import Optional, Dict, Any
from sqlalchemy.orm import Session
import logging

from app.adapters.postgres.models import UsageEvent, UsageRollupDaily
from app.domain.budgets.service import BudgetService
from app.domain.budgets.pricing import PricingRegistry, get_pricing_registry
from app.utils.id import uuid7

logger = logging.getLogger(__name__)

class UsageManager:
    """Consolidated service for recording usage and settling budgets."""
    
    def __init__(self, db: Session, budget_service: Optional[BudgetService] = None, pricing: Optional[PricingRegistry] = None):
        self.db = db
        self.budget_service = budget_service or BudgetService(db)
        self.pricing = pricing or get_pricing_registry()

    async def record_event(
        self,
        request_id: str,
        team_id: str,
        key_id: str,
        org_id: str,
        surface: str,
        target: str,
        input_tokens: int = 0,
        output_tokens: int = 0,
        latency_ms: int = 0,
        status: str = "success",
        token_count_source: str = "unknown"
    ) -> Decimal:
        """
        Record usage event and settle budget reservation.
        Idempotent via unique request_id constraint in DB.
        """
        try:
            # 1. Calculate Actual Cost
            if surface == "llm":
                cost, pricing_ver = self.pricing.get_llm_cost(
                    model_name=target,
                    provider="unknown", # TODO: pass provider
                    group_id=None,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens
                )
            elif surface == "mcp":
                # target is server_id:tool_name
                parts = target.split(":", 1)
                server_id = parts[0]
                tool_name = parts[1] if len(parts) > 1 else "unknown"
                cost, pricing_ver = self.pricing.get_mcp_cost(server_id, tool_name)
            else:
                cost, pricing_ver = Decimal("0.00"), "v1"

            # 2. Record Event (Idempotent check via try/except or exists)
            event = UsageEvent(
                id=str(uuid7()),
                request_id=request_id,
                team_id=team_id,
                key_id=key_id,
                org_id=org_id,
                surface=surface,
                target=target,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cost_usd=cost,
                latency_ms=latency_ms,
                status=status,
                pricing_version=pricing_ver,
                token_count_source=token_count_source
            )
            self.db.add(event)
            
            # 3. Settle Budget
            # This is also idempotent inside settle()
            self.budget_service.settle(request_id, cost)
            
            # 4. Optional: Async Rollup Trigger
            # For Phase 15, we do synchronous rollup update or wait for job.
            # Let's do a simple sync rollup update (atomic increment)
            # Actually, per Phase 15 spec, jobs handle intensive rollups, 
            # but for real-time dashboard accuracy, a sync increment is helpful.
            self._update_rollups(event)
            
            self.db.commit()
            return cost

        except Exception as e:
            logger.error(f"Error recording usage for {request_id}: {e}")
            self.db.rollback()
            return Decimal("0.00")

    def _update_rollups(self, event: UsageEvent):
        """Update daily rollups via atomic increment."""
        day = event.timestamp.date()
        
        # This is a 'soft' update, we don't lock the rollup row for long 
        # as it's just for stats, but BudgetScope handles the strict enforcement.
        from sqlalchemy.dialects.postgresql import insert
        stmt = insert(UsageRollupDaily).values(
            id=str(uuid7()),
            day=day,
            team_id=event.team_id,
            key_id=event.key_id,
            used_usd=event.cost_usd,
            input_tokens=event.input_tokens,
            output_tokens=event.output_tokens,
            request_count=1
        ).on_conflict_do_update(
            constraint="uq_usage_rollup_day",
            set_={
                "used_usd": UsageRollupDaily.used_usd + event.cost_usd,
                "input_tokens": UsageRollupDaily.input_tokens + event.input_tokens,
                "output_tokens": UsageRollupDaily.output_tokens + event.output_tokens,
                "request_count": UsageRollupDaily.request_count + 1,
                "updated_at": event.timestamp
            }
        )
        self.db.execute(stmt)
