"""LLM Router - Upstreams, Model Groups, Routing with Redis Cooldown."""
from typing import Dict, List, Optional
import hashlib
from datetime import datetime, timedelta
import asyncio

from app.adapters.redis.client import get_redis, cooldown_key

# In-memory stores for MVP (upstreams/groups/policies)
UPSTREAMS: Dict[str, dict] = {
    "openai-us": {
        "id": "openai-us",
        "provider": "openai",
        "endpoint": "https://api.openai.com/v1",
        "credentials_ref": "secret:openai-api-key",
        "tags": {"region": "us"},
        "enabled": True
    },
    "azure-eu": {
        "id": "azure-eu",
        "provider": "azure",
        "endpoint": "https://talos-eu.openai.azure.com",
        "credentials_ref": "secret:azure-api-key",
        "tags": {"region": "eu"},
        "enabled": True
    }
}

MODEL_GROUPS: Dict[str, dict] = {
    "gpt-4-turbo": {
        "id": "gpt-4-turbo",
        "name": "GPT-4 Turbo",
        "deployments": [
            {"upstream_id": "openai-us", "model_name": "gpt-4-turbo-preview", "weight": 50},
            {"upstream_id": "azure-eu", "model_name": "gpt-4-turbo", "weight": 50}
        ],
        "fallback_groups": ["gpt-3.5-turbo"]
    },
    "gpt-3.5-turbo": {
        "id": "gpt-3.5-turbo",
        "name": "GPT-3.5 Turbo",
        "deployments": [
            {"upstream_id": "openai-us", "model_name": "gpt-3.5-turbo", "weight": 100}
        ],
        "fallback_groups": []
    }
}

ROUTING_POLICIES: Dict[str, dict] = {
    "default": {
        "id": "default",
        "version": 1,
        "strategy": "weighted_hash",
        "retries": {"max_attempts": 3, "backoff_ms": 500, "backoff_multiplier": 2},
        "timeout_ms": 30000,
        "cooldown": {"failure_threshold": 3, "window_seconds": 60, "cooldown_seconds": 300}
    }
}

# In-memory cooldown fallback
COOLDOWN_STATE: Dict[str, datetime] = {}


def list_upstreams() -> List[dict]:
    return list(UPSTREAMS.values())


def get_upstream(upstream_id: str) -> Optional[dict]:
    return UPSTREAMS.get(upstream_id)


def create_upstream(data: dict) -> dict:
    upstream_id = data.get("id") or f"upstream-{len(UPSTREAMS)+1}"
    data["id"] = upstream_id
    UPSTREAMS[upstream_id] = data
    return data


def list_model_groups() -> List[dict]:
    return list(MODEL_GROUPS.values())


def get_model_group(group_id: str) -> Optional[dict]:
    return MODEL_GROUPS.get(group_id)


def create_model_group(data: dict) -> dict:
    group_id = data.get("id") or f"model-{len(MODEL_GROUPS)+1}"
    data["id"] = group_id
    MODEL_GROUPS[group_id] = data
    return data


def get_routing_policy(policy_id: str = "default") -> Optional[dict]:
    return ROUTING_POLICIES.get(policy_id)


async def is_upstream_cooled_down_async(upstream_id: str) -> bool:
    """Check if upstream is in cooldown via Redis."""
    try:
        redis = await get_redis()
        key = cooldown_key(upstream_id)
        result = await redis.get(key)
        return result is not None
    except Exception:
        # Fallback to in-memory
        cooldown_until = COOLDOWN_STATE.get(upstream_id)
        if cooldown_until and datetime.utcnow() < cooldown_until:
            return True
        return False


def is_upstream_cooled_down(upstream_id: str) -> bool:
    """Sync check for cooldown (in-memory fallback)."""
    cooldown_until = COOLDOWN_STATE.get(upstream_id)
    if cooldown_until and datetime.utcnow() < cooldown_until:
        return True
    return False


def select_upstream(model_group_id: str, request_id: str, policy_id: str = "default") -> Optional[dict]:
    """Deterministic hash-based weighted selection."""
    group = get_model_group(model_group_id)
    if not group:
        return None
    
    policy = get_routing_policy(policy_id)
    deployments = group.get("deployments", [])
    
    # Filter out cooled-down upstreams (sync check)
    available = []
    for d in deployments:
        if not is_upstream_cooled_down(d["upstream_id"]):
            available.append(d)
    
    if not available:
        # All upstreams cooled down, try fallback
        for fallback_id in group.get("fallback_groups", []):
            result = select_upstream(fallback_id, request_id, policy_id)
            if result:
                result["fallback_from"] = model_group_id
                return result
        return None
    
    # Deterministic hash-based selection
    hash_input = f"{policy.get('version', 1)}:{model_group_id}:{request_id}"
    hash_val = int(hashlib.sha256(hash_input.encode()).hexdigest(), 16)
    
    # Weighted selection
    total_weight = sum(d.get("weight", 100) for d in available)
    target = hash_val % total_weight
    
    cumulative = 0
    for d in available:
        cumulative += d.get("weight", 100)
        if target < cumulative:
            upstream = get_upstream(d["upstream_id"])
            return {
                "upstream": upstream,
                "model_name": d["model_name"],
                "model_group_id": model_group_id,
                "policy_version": policy.get("version", 1)
            }
    
    return None


async def mark_upstream_failed_async(upstream_id: str, policy_id: str = "default"):
    """Mark upstream as failed and trigger cooldown via Redis."""
    policy = get_routing_policy(policy_id)
    cooldown_config = policy.get("cooldown", {})
    cooldown_seconds = cooldown_config.get("cooldown_seconds", 300)
    
    try:
        redis = await get_redis()
        key = cooldown_key(upstream_id)
        await redis.setex(key, cooldown_seconds, "1")
    except Exception:
        # Fallback to in-memory
        mark_upstream_failed(upstream_id, policy_id)


def mark_upstream_failed(upstream_id: str, policy_id: str = "default"):
    """Mark upstream as failed (in-memory fallback)."""
    policy = get_routing_policy(policy_id)
    cooldown_config = policy.get("cooldown", {})
    cooldown_seconds = cooldown_config.get("cooldown_seconds", 300)
    COOLDOWN_STATE[upstream_id] = datetime.utcnow() + timedelta(seconds=cooldown_seconds)
