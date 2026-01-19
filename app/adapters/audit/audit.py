"""Audit event adapter."""
from datetime import datetime, timezone
from typing import Optional
from app.utils.id import uuid7


def emit_event(
    action: str,
    resource_type: str,
    request_id: str,
    principal_id: str,
    resource_id: Optional[str] = None,
    outcome: str = "success",
    error_code: Optional[str] = None,
    version_before: Optional[int] = None,
    version_after: Optional[int] = None,
    context: str = "api"
) -> dict:
    """Emit an audit event.
    
    In production, this would write to an audit log store.
    For MVP, we print to stdout.
    """
    event = {
        "event_id": uuid7(),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "principal_id": principal_id,
        "action": action,
        "resource_type": resource_type,
        "resource_id": resource_id,
        "outcome": outcome,
        "error_code": error_code,
        "request_id": request_id,
        "context": context,
        "scope": "platform",
        "version_before": version_before,
        "version_after": version_after
    }
    
    # MVP: print to stdout (would go to audit store in production)
    print(f"[AUDIT] {action} {resource_type}/{resource_id} by {principal_id}: {outcome}")
    
    return event
