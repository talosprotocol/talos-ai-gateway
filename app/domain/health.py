"""Health State Management for Upstreams."""
import time
from typing import Dict, Optional

class HealthState:
    def __init__(self):
        self._failures: Dict[str, int] = {}
        self._last_failure: Dict[str, float] = {}
        self._cooldown: int = 60 # seconds

    def mark_failed(self, upstream_id: str):
        self._failures[upstream_id] = self._failures.get(upstream_id, 0) + 1
        self._last_failure[upstream_id] = time.time()

    def mark_success(self, upstream_id: str):
        if upstream_id in self._failures:
            del self._failures[upstream_id]
        if upstream_id in self._last_failure:
            del self._last_failure[upstream_id]

    def is_healthy(self, upstream_id: str) -> bool:
        if upstream_id not in self._last_failure:
            return True
        # Check cooldown
        if time.time() - self._last_failure[upstream_id] > self._cooldown:
            # Reset
            self.mark_success(upstream_id)
            return True
        return False

# Global instance
_health_state = HealthState()

def get_health_state() -> HealthState:
    return _health_state
