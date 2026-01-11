"""Memory Store Implementations."""
from typing import Dict, Any, Optional
from datetime import datetime, timedelta
import logging

from app.domain.interfaces import RateLimitStore, RateLimitResult

logger = logging.getLogger(__name__)

# Global state for memory store
_RATE_LIMIT_STATE: Dict[str, dict] = {}

class MemoryRateLimitStore(RateLimitStore):
    async def check_limit(self, key: str, limit: int, window_seconds: int = 60) -> RateLimitResult:
        now = datetime.utcnow()
        bucket = _RATE_LIMIT_STATE.get(key)
        
        # Lazy cleanup/reset
        if not bucket or bucket["reset_at"] < now:
            bucket = {
                "count": 0,
                "reset_at": now + timedelta(seconds=window_seconds),
                "limit": limit
            }
            
        bucket["count"] += 1
        _RATE_LIMIT_STATE[key] = bucket
        
        current = bucket["count"]
        remaining = max(0, limit - current)
        allowed = current <= limit
        
        return RateLimitResult(
            allowed=allowed,
            remaining=remaining,
            reset_at=bucket["reset_at"],
            limit=limit
        )

from app.domain.interfaces import SessionStore, SessionState

_SESSION_STATE: Dict[str, dict] = {}

class MemorySessionStore(SessionStore):
    async def create_session(self, session_id: str, public_key: str, ttl: int = 3600) -> SessionState:
        now = datetime.utcnow()
        expires = now + timedelta(seconds=ttl)
        _SESSION_STATE[session_id] = {
            "pk": public_key,
            "seq": 1,
            "created": now,
            "expires": expires
        }
        return SessionState(session_id, public_key, 1, now, expires)
        
    async def get_session(self, session_id: str) -> Optional[SessionState]:
        data = _SESSION_STATE.get(session_id)
        if not data:
            return None
        # Check expiry
        if data["expires"] < datetime.utcnow():
            return None
        return SessionState(session_id, data["pk"], data["seq"], data["created"], data["expires"])
        
    async def validate_sequence(self, session_id: str, sequence: int) -> bool:
        data = _SESSION_STATE.get(session_id)
        if not data:
            return False
            
        if data["seq"] == sequence:
            data["seq"] += 1
            return True
        return False
