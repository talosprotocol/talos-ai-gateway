"""Audit Adapter - Event emission."""
from typing import Dict, Any, Optional
from datetime import datetime
import uuid
import json

# In-memory audit log for MVP
AUDIT_LOG: list = []


def emit_event(
    event_type: str,
    surface: str,  # "llm" or "mcp"
    request_id: str,
    key_id: str,
    team_id: str,
    org_id: str,
    target: Optional[str] = None,
    outcome: str = "success",
    error_code: Optional[str] = None,
    latency_ms: Optional[int] = None,
    policy_version: Optional[int] = None,
    metadata: Optional[Dict[str, Any]] = None
) -> str:
    """Emit an audit event."""
    event_id = str(uuid.uuid4())
    
    event = {
        "event_id": event_id,
        "event_type": event_type,
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "surface": surface,
        "request_id": request_id,
        "key_id": key_id,
        "team_id": team_id,
        "org_id": org_id,
        "target": target,
        "outcome": outcome,
        "error_code": error_code,
        "latency_ms": latency_ms,
        "policy_version": policy_version
    }
    
    # Redact sensitive metadata
    if metadata:
        event["metadata_hash"] = hash(json.dumps(metadata, sort_keys=True))
    
    AUDIT_LOG.append(event)
    
    # In production, this would send to talos-audit-service
    return event_id


def get_recent_events(limit: int = 100) -> list:
    """Get recent audit events."""
    return AUDIT_LOG[-limit:]
