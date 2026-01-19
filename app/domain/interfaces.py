"""Domain interfaces for persistence stores."""
from abc import ABC, abstractmethod
from typing import List, Optional, Dict, Any, TypedDict, Protocol
from datetime import datetime
from dataclasses import dataclass

class PrincipalStore(Protocol):
    def get_principal(self, principal_id: str) -> Optional[Dict[str, Any]]:
        ...

class UpstreamStore(ABC):
    @abstractmethod
    def list_upstreams(self) -> List[Dict[str, Any]]: pass
    @abstractmethod
    def get_upstream(self, upstream_id: str) -> Optional[Dict[str, Any]]: pass
    @abstractmethod
    def create_upstream(self, upstream: Dict[str, Any]) -> None: pass
    @abstractmethod
    def update_upstream(self, upstream_id: str, updates: Dict[str, Any], expected_version: Optional[int] = None) -> Dict[str, Any]: pass
    @abstractmethod
    def delete_upstream(self, upstream_id: str) -> None: pass

class ModelGroupStore(ABC):
    @abstractmethod
    def list_model_groups(self) -> List[Dict[str, Any]]: pass
    @abstractmethod
    def get_model_group(self, group_id: str) -> Optional[Dict[str, Any]]: pass
    @abstractmethod
    def create_model_group(self, group: Dict[str, Any]) -> None: pass
    @abstractmethod
    def update_model_group(self, group_id: str, updates: Dict[str, Any], expected_version: Optional[int] = None) -> Dict[str, Any]: pass
    @abstractmethod
    def delete_model_group(self, group_id: str) -> None: pass

class RoutingPolicyStore(ABC):
    @abstractmethod
    def list_policies(self) -> List[Dict[str, Any]]: pass
    @abstractmethod
    def get_policy(self, policy_id: str, version: Optional[int] = None) -> Optional[Dict[str, Any]]: pass
    @abstractmethod
    def create_policy(self, policy: Dict[str, Any]) -> None: pass
    @abstractmethod
    def delete_policy(self, policy_id: str) -> None: pass

class SecretStore(ABC):
    @abstractmethod
    def list_secrets(self) -> List[Dict[str, Any]]: pass
    @abstractmethod
    def get_secret_value(self, name: str) -> Optional[str]: pass
    @abstractmethod
    def set_secret(self, name: str, value: str, expected_kek_id: Optional[str] = None) -> bool: pass
    @abstractmethod
    def delete_secret(self, name: str) -> bool: pass
    @abstractmethod
    def get_stale_counts(self) -> Dict[str, int]: pass
    @abstractmethod
    def get_secrets_batch(self, batch_size: int, cursor: Optional[str] = None) -> List[Dict[str, Any]]: pass

class McpStore(ABC):
    @abstractmethod
    def list_servers(self) -> List[Dict[str, Any]]: pass
    @abstractmethod
    def get_server(self, server_id: str) -> Optional[Dict[str, Any]]: pass
    @abstractmethod
    def create_server(self, server: Dict[str, Any]) -> None: pass
    @abstractmethod
    def update_server(self, server_id: str, updates: Dict[str, Any], expected_version: Optional[int] = None) -> Dict[str, Any]: pass
    @abstractmethod
    def delete_server(self, server_id: str) -> None: pass
    @abstractmethod
    def list_policies(self, team_id: str) -> List[Dict[str, Any]]: pass
    @abstractmethod
    def upsert_policy(self, policy: Dict[str, Any]) -> None: pass
    @abstractmethod
    def delete_policy(self, policy_id: str) -> None: pass

class AuditStore(ABC):
    @abstractmethod
    def append_event(self, event: Dict[str, Any]) -> None: pass
    @abstractmethod
    def list_events(self, filters: Dict[str, Any], limit: int = 100) -> List[Dict[str, Any]]: pass

    @abstractmethod
    def get_dashboard_stats(self, window_hours: int = 24) -> Dict[str, Any]: pass

from pydantic import BaseModel, Field

class RateLimitResult(BaseModel):
    allowed: bool
    remaining: int
    reset_at: datetime
    limit: int

class RateLimitStore(ABC):
    @abstractmethod
    async def check_limit(self, key: str, limit: int, window_seconds: int = 60) -> RateLimitResult: pass

class SessionState(BaseModel):
    session_id: str
    public_key: str
    next_sequence: int
    created_at: datetime
    expires_at: datetime

class SessionStore(ABC):
    @abstractmethod
    async def create_session(self, session_id: str, public_key: str, ttl: int = 3600) -> SessionState: pass
    @abstractmethod
    async def get_session(self, session_id: str) -> Optional[SessionState]: pass
    @abstractmethod
    async def validate_sequence(self, session_id: str, sequence: int) -> bool: pass

class UsageStore(ABC):
    @abstractmethod
    def record_usage(self, event: Dict[str, Any]) -> None: pass
    @abstractmethod
    def get_stats(self, window_hours: int = 24) -> Dict[str, Any]: pass

class A2ATaskRecord(TypedDict):
    id: str
    team_id: str
    key_id: str
    org_id: Optional[str]
    request_id: str
    method: str
    status: str
    version: int
    request_meta: Dict[str, Any]
    input_redacted: Optional[Dict[str, Any]]
    result: Optional[Dict[str, Any]]
    error: Optional[Dict[str, Any]]
    created_at: datetime
    updated_at: datetime

class TaskStore(ABC):
    @abstractmethod
    def create_task(self, task_data: A2ATaskRecord) -> None: pass
    @abstractmethod
    def update_task_status(self, task_id: str, status: str, expected_version: int, result: Optional[Dict[str, Any]] = None, error: Optional[Dict[str, Any]] = None) -> int: pass
    @abstractmethod
    def get_task(self, task_id: str, team_id: str) -> Optional[A2ATaskRecord]: pass
    @abstractmethod
    def delete_expired_tasks(self, cutoff_date: datetime) -> List[str]: pass

class RotationOperationStore(ABC):
    @abstractmethod
    def create_operation(self, op: Dict[str, Any]) -> None: pass
    @abstractmethod
    def get_operation(self, op_id: str) -> Optional[Dict[str, Any]]: pass
    @abstractmethod
    def update_operation(self, op_id: str, updates: Dict[str, Any]) -> None: pass
    @abstractmethod
    def get_active_operation(self) -> Optional[Dict[str, Any]]: pass
