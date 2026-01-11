"""Domain interfaces for persistence stores."""
from abc import ABC, abstractmethod
from typing import List, Optional, Dict, Any

class UpstreamStore(ABC):
    @abstractmethod
    def list_upstreams(self) -> List[Dict[str, Any]]:
        pass

    @abstractmethod
    def get_upstream(self, upstream_id: str) -> Optional[Dict[str, Any]]:
        pass

    @abstractmethod
    def create_upstream(self, upstream: Dict[str, Any]) -> None:
        pass

    @abstractmethod
    def update_upstream(self, upstream_id: str, updates: Dict[str, Any], expected_version: Optional[int] = None) -> Dict[str, Any]:
        pass

    @abstractmethod
    def delete_upstream(self, upstream_id: str) -> None:
        pass

class ModelGroupStore(ABC):
    @abstractmethod
    def list_model_groups(self) -> List[Dict[str, Any]]:
        pass

    @abstractmethod
    def get_model_group(self, group_id: str) -> Optional[Dict[str, Any]]:
        pass

    @abstractmethod
    def create_model_group(self, group: Dict[str, Any]) -> None:
        pass

    @abstractmethod
    def update_model_group(self, group_id: str, updates: Dict[str, Any], expected_version: Optional[int] = None) -> Dict[str, Any]:
        pass

    @abstractmethod
    def delete_model_group(self, group_id: str) -> None:
        pass

class RoutingPolicyStore(ABC):
    @abstractmethod
    def list_policies(self) -> List[Dict[str, Any]]:
        pass
    
    @abstractmethod
    def get_policy(self, policy_id: str, version: Optional[int] = None) -> Optional[Dict[str, Any]]:
        pass
    
    @abstractmethod
    def create_policy(self, policy: Dict[str, Any]) -> None:
        """Create new policy version (immutable)."""
        pass
        
    @abstractmethod
    def delete_policy(self, policy_id: str) -> None:
        # Deletes all versions? Or marking keys as revoked? Usually immutable config isn't deleted, just unused.
        pass

class SecretStore(ABC):
    @abstractmethod
    def list_secrets(self) -> List[Dict[str, Any]]:
        """Return metadata only."""
        pass

    @abstractmethod
    def get_secret_value(self, name: str) -> Optional[str]:
        """Return decrypted value."""
        pass

    @abstractmethod
    def set_secret(self, name: str, value: str) -> None:
        pass

    @abstractmethod
    def delete_secret(self, name: str) -> bool:
        pass

class McpStore(ABC):
    @abstractmethod
    def list_servers(self) -> List[Dict[str, Any]]:
        pass

    @abstractmethod
    def get_server(self, server_id: str) -> Optional[Dict[str, Any]]:
        pass

    @abstractmethod
    def create_server(self, server: Dict[str, Any]) -> None:
        pass
        
    @abstractmethod
    def update_server(self, server_id: str, updates: Dict[str, Any], expected_version: Optional[int] = None) -> Dict[str, Any]:
        pass

    @abstractmethod
    def delete_server(self, server_id: str) -> None:
        pass

    @abstractmethod
    def list_policies(self, team_id: str) -> List[Dict[str, Any]]:
        pass
        
    @abstractmethod
    def upsert_policy(self, policy: Dict[str, Any]) -> None:
        pass

    @abstractmethod
    def delete_policy(self, policy_id: str) -> None:
        pass


class AuditStore(ABC):
    @abstractmethod
    def append_event(self, event: Dict[str, Any]) -> None:
        pass
    
    @abstractmethod
    def list_events(self, filters: Dict[str, Any], limit: int = 100) -> List[Dict[str, Any]]:
        pass

from dataclasses import dataclass
from datetime import datetime

@dataclass
class RateLimitResult:
    allowed: bool
    remaining: int
    reset_at: datetime
    limit: int

class RateLimitStore(ABC):
    @abstractmethod
    async def check_limit(self, key: str, limit: int, window_seconds: int = 60) -> RateLimitResult:
        pass

@dataclass
class SessionState:
    session_id: str
    public_key: str
    next_sequence: int
    created_at: datetime
    expires_at: datetime

class SessionStore(ABC):
    @abstractmethod
    async def create_session(self, session_id: str, public_key: str, ttl: int = 3600) -> SessionState:
        pass

    @abstractmethod
    async def get_session(self, session_id: str) -> Optional[SessionState]:
        pass

    @abstractmethod
    async def validate_sequence(self, session_id: str, sequence: int) -> bool:
        """Atomically validate sequence matches expected and increment."""
        pass


class UsageStore(ABC):
    @abstractmethod
    def record_usage(self, event: Dict[str, Any]) -> None:
        pass

    @abstractmethod
    def get_stats(self, window_hours: int = 24) -> Dict[str, Any]:
        """Aggregate stats (RPM, TPM, Cost)."""
        pass


