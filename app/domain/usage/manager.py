"""Usage Manager."""
from typing import Dict, Any, Optional
import logging
from datetime import datetime
from sqlalchemy import select, and_
from sqlalchemy.orm import Session
from sqlalchemy.dialects.postgresql import insert
from app.adapters.postgres.models import UsageEvent, UsageRollupDaily
from app.domain.budgets.pricing import PricingRegistry, get_pricing_registry
from app.utils.id import uuid7

logger = logging.getLogger(__name__)

class UsageManager:
    """Core domain service for unified usage recording and aggregation."""
    
    def __init__(self, db: Session, pricing: PricingRegistry = None):
        self.db = db
        self.pricing = pricing or get_pricing_registry()

    def record_usage(
        self,
        event_id: Optional[str],
        request_id: str,
        team_id: str,
        virtual_key_id: str,
        kind: str,
        provider: str, # or mcp_server_id
        model: str, # or tool_name
        input_tokens: int,
        output_tokens: int,
        status: str,
        cost_usd: Optional[Any] = None, # Decimal
        latency_ms: int = 0
    ) -> UsageEvent:
        """
        Record a unified usage event.
        - Calculates cost if not provided.
        - Idempotent via request_id (handled by DB constraint).
        - Updates Daily Rollups synchronously.
        """
        
        # Calculate cost if missing
        pricing_ver = self.pricing.version
        token_source = "reported" # Assume reported if passed
        
        if cost_usd is None:
            if kind == "llm":
                cost_usd, pricing_ver = self.pricing.get_llm_cost(
                    model_name=model, 
                    provider=provider, 
                    group_id=None, # In V1 we calculate plain cost based on model/provider
                    input_tokens=input_tokens, 
                    output_tokens=output_tokens
                )
                token_source = "estimated"
            elif kind == "mcp":
                cost_usd, pricing_ver = self.pricing.get_mcp_cost(
                    server_id=provider, 
                    tool_name=model
                )
                token_source = "estimated"
            else:
                 cost_usd = 0

        event = UsageEvent(
            id=event_id or str(uuid7()),
            request_id=request_id,
            key_id=virtual_key_id,
            team_id=team_id,
            org_id=None, # TODO: Populate if available in context
            surface=kind,
            target=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=cost_usd,
            pricing_version=pricing_ver,
            token_count_source=token_source,
            latency_ms=latency_ms,
            status=status
        )
        
        try:
            self.db.add(event)
            # Update Rollup
            self._update_rollup(team_id, virtual_key_id, cost_usd, input_tokens, output_tokens)
            
            self.db.commit()
            return event
        except Exception as e:
            self.db.rollback()
            # Idempotency conflict?
            if "uq_usage_event_request" in str(e):
                logger.info(f"Duplicate usage event for request {request_id}, ignoring.")
                # Return existing if possible, or just None/Mock
                return self.db.query(UsageEvent).filter_by(request_id=request_id).first()
            logger.error(f"Failed to record usage: {e}")
            raise e

    def _update_rollup(self, team_id, key_id, cost_usd, input_tokens, output_tokens):
        """Update or Insert Daily Rollup."""
        day = datetime.utcnow().date()
        
        stmt = insert(UsageRollupDaily).values(
            id=str(uuid7()),
            day=day,
            team_id=team_id,
            key_id=key_id,
            used_usd=cost_usd,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            request_count=1
        ).on_conflict_do_update(
            index_elements=['day', 'team_id', 'key_id'],
            set_={
                "used_usd": UsageRollupDaily.used_usd + cost_usd,
                "input_tokens": UsageRollupDaily.input_tokens + input_tokens,
                "output_tokens": UsageRollupDaily.output_tokens + output_tokens,
                "request_count": UsageRollupDaily.request_count + 1,
                "updated_at": datetime.utcnow()
            }
        )
        self.db.execute(stmt)
