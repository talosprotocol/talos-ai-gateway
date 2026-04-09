"""Routing Service."""
import os
import random
import logging
from typing import Optional, Dict, Any
from app.domain.interfaces import UpstreamStore, ModelGroupStore, RoutingPolicyStore
from app.domain.health import HealthState

logger = logging.getLogger(__name__)

class RoutingService:
    def __init__(self, 
                 upstream_store: UpstreamStore, 
                 model_group_store: ModelGroupStore,
                 policy_store: RoutingPolicyStore,
                 health_state: HealthState):
        self.u_store = upstream_store
        self.mg_store = model_group_store
        self.p_store = policy_store
        self.health = health_state

    def default_model_group_id(self) -> Optional[str]:
        preferred_ids = [
            os.getenv("A2A_DEFAULT_MODEL_GROUP"),
            os.getenv("DEFAULT_MODEL_GROUP"),
        ]
        for preferred_id in preferred_ids:
            if not preferred_id:
                continue
            group = self.mg_store.get_model_group(preferred_id)
            if group and group.get("enabled", True) and group.get("deployments"):
                return preferred_id

        for group in self.mg_store.list_model_groups():
            group_id = group.get("id")
            if not group_id:
                continue
            if not group.get("enabled", True):
                continue
            if not group.get("deployments"):
                continue
            return str(group_id)
        return None

    def select_upstream(self, model_group_id: str, request_id: str) -> Optional[Dict[str, Any]]:
        group = self.mg_store.get_model_group(model_group_id)
        if not group or not group.get("enabled", True):
            return None

        deployments = group.get("deployments", [])
        if not deployments:
            # Check fallbacks? Not implemented yet
            return None

        # Filter healthy and enabled upstreams
        candidates = []
        for dep in deployments:
            uid = dep["upstream_id"]
            # Check availability
            # Optimization: Fetch all upstreams in batch if possible, or individual
            upstream = self.u_store.get_upstream(uid)
            if not upstream:
                continue
            if not upstream.get("enabled", True):
                continue
            if not self.health.is_healthy(uid):
                continue
                
            candidates.append({
                "upstream": upstream,
                "weight": dep.get("weight", 100),
                "model_name": dep.get("model_name")
            })

        if not candidates:
            return None

        # Routing Logic: Weighted Hash / Random
        # MVP: Weighted Random
        total_weight = sum(c["weight"] for c in candidates)
        if total_weight == 0:
            choice = random.choice(candidates)
        else:
            r = random.uniform(0, total_weight)
            upto = 0
            choice = candidates[-1]
            for c in candidates:
                if upto + c["weight"] >= r:
                    choice = c
                    break
                upto += c["weight"]
        
        return {
            "upstream": choice["upstream"],
            "model_name": choice["model_name"]
        }

    def mark_failure(self, upstream_id: str):
        self.health.mark_failed(upstream_id)
