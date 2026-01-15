"""LLM Router - Upstreams, Model Groups, Routing with Redis Cooldown."""
from typing import Dict, List, Optional
import hashlib
from datetime import datetime, timedelta, timezone

from app.adapters.redis.client import get_redis, cooldown_key
from app.config_loader import get_upstreams, get_model_groups, get_routing_policies, save_config

# Load from config file (mutable at runtime)
UPSTREAMS: Dict[str, dict] = dict(get_upstreams())
MODEL_GROUPS: Dict[str, dict] = dict(get_model_groups())
ROUTING_POLICIES: Dict[str, dict] = dict(get_routing_policies())

# In-memory cooldown fallback
COOLDOWN_STATE: Dict[str, datetime] = {}


def list_upstreams() -> List[dict]:
    return list(UPSTREAMS.values())


def get_upstream(upstream_id: str) -> Optional[dict]:
    return UPSTREAMS.get(upstream_id)


def create_upstream(data: dict) -> dict:
    """Add or update an upstream."""
    upstream_id = data.get("id") or f"upstream-{len(UPSTREAMS)+1}"
    data["id"] = upstream_id
    UPSTREAMS[upstream_id] = data
    return data


def delete_upstream(upstream_id: str) -> bool:
    """Delete an upstream by ID."""
    if upstream_id in UPSTREAMS:
        del UPSTREAMS[upstream_id]
        return True
    return False


def list_model_groups() -> List[dict]:
    return list(MODEL_GROUPS.values())


def get_model_group(group_id: str) -> Optional[dict]:
    return MODEL_GROUPS.get(group_id)


def create_model_group(data: dict) -> dict:
    """Add or update a model group."""
    group_id = data.get("id") or f"model-{len(MODEL_GROUPS)+1}"
    data["id"] = group_id
    MODEL_GROUPS[group_id] = data
    return data


def delete_model_group(group_id: str) -> bool:
    """Delete a model group by ID."""
    if group_id in MODEL_GROUPS:
        del MODEL_GROUPS[group_id]
        return True
    return False


def get_routing_policy(policy_id: str = "default") -> Optional[dict]:
    return ROUTING_POLICIES.get(policy_id)


def save_current_config(config_path: Optional[str] = None) -> None:
    """Save current in-memory config to file."""
    config = {
        "upstreams": UPSTREAMS,
        "model_groups": MODEL_GROUPS,
        "routing_policies": ROUTING_POLICIES
    }
    save_config(config, config_path)


def reload_from_file() -> None:
    """Reload config from file into memory."""
    global UPSTREAMS, MODEL_GROUPS, ROUTING_POLICIES
    UPSTREAMS = dict(get_upstreams())
    MODEL_GROUPS = dict(get_model_groups())
    ROUTING_POLICIES = dict(get_routing_policies())


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
        if cooldown_until and datetime.now(timezone.utc) < cooldown_until:
            return True
        return False


def is_upstream_cooled_down(upstream_id: str) -> bool:
    """Sync check for cooldown (in-memory fallback)."""
    cooldown_until = COOLDOWN_STATE.get(upstream_id)
    if cooldown_until and datetime.now(timezone.utc) < cooldown_until:
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
    COOLDOWN_STATE[upstream_id] = datetime.now(timezone.utc) + timedelta(seconds=cooldown_seconds)
