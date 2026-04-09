"""Memory Store Implementations."""
from typing import Dict, Optional, List
from datetime import datetime, timedelta, timezone
import logging

from app.domain.interfaces import (
    RateLimitStore,
    RateLimitResult,
    SessionStore,
    SessionState,
    TaskStore,
)

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

_TASK_STATE: Dict[str, dict] = {}

class MemoryTaskStore(TaskStore):
    def create_task(self, task_data: Dict) -> None:
        _TASK_STATE[task_data["id"]] = task_data.copy()

    def update_task_status(
        self, 
        task_id: str, 
        status: str, 
        expected_version: int,
        result: Optional[Dict] = None, 
        error: Optional[Dict] = None
    ) -> int:
        task = _TASK_STATE.get(task_id)
        if not task:
            raise KeyError("Task not found")
            
        current_version = task.get("version", 1)
        if current_version != expected_version:
             raise ValueError("Version mismatch")
             
        task["status"] = status
        if result:
            task["result"] = result
        if error:
            task["error"] = error
        task["version"] = current_version + 1
        return task["version"]
        
    def get_task(self, task_id: str, team_id: str) -> Optional[Dict]:
        task = _TASK_STATE.get(task_id)
        if not task:
            return None
        if task.get("team_id") != team_id:
            return None
        return task.copy()

    def list_tasks(
        self,
        team_id: str,
        *,
        context_id: Optional[str] = None,
        status: Optional[str] = None,
        page_size: int = 50,
        cursor_updated_at: Optional[datetime] = None,
        cursor_task_id: Optional[str] = None,
        status_timestamp_after: Optional[datetime] = None,
    ) -> tuple[List[Dict], Optional[tuple[datetime, str]], int]:
        filtered_tasks: List[Dict] = []
        for task in _TASK_STATE.values():
            if task.get("team_id") != team_id:
                continue
            if status and task.get("status") != status:
                continue

            request_meta = task.get("request_meta", {})
            if context_id and request_meta.get("context_id") != context_id:
                continue

            updated_at = self._normalize_datetime(task.get("updated_at") or task.get("created_at"))
            if status_timestamp_after and updated_at < status_timestamp_after:
                continue
            filtered_tasks.append(task.copy())

        filtered_tasks.sort(
            key=lambda item: (
                self._normalize_datetime(item.get("updated_at") or item.get("created_at")),
                str(item["id"]),
            ),
            reverse=True,
        )

        total_size = len(filtered_tasks)
        tasks = filtered_tasks
        if cursor_updated_at is not None and cursor_task_id is not None:
            cursor_key = (cursor_updated_at, cursor_task_id)
            tasks = [
                task
                for task in filtered_tasks
                if (
                    self._normalize_datetime(task.get("updated_at") or task.get("created_at")),
                    str(task["id"]),
                )
                < cursor_key
            ]

        page = tasks[: page_size + 1]
        next_cursor = None
        if len(page) > page_size:
            last = page[page_size - 1]
            next_cursor = (
                self._normalize_datetime(last.get("updated_at") or last.get("created_at")),
                str(last["id"]),
            )
            page = page[:page_size]

        return page, next_cursor, total_size

    def create_task_push_notification_config(
        self,
        task_id: str,
        team_id: str,
        config: Dict,
    ) -> Dict:
        task = self._get_mutable_task(task_id, team_id)
        request_meta = task.setdefault("request_meta", {})
        configs = request_meta.setdefault("push_notification_configs", [])
        existing_index = self._find_config_index(configs, str(config["id"]))
        stored = config.copy()
        if existing_index is None:
            configs.append(stored)
        else:
            configs[existing_index] = stored
        return stored.copy()

    def get_task_push_notification_config(
        self,
        task_id: str,
        team_id: str,
        config_id: str,
    ) -> Optional[Dict]:
        task = self.get_task(task_id, team_id)
        if not task:
            return None
        configs = task.get("request_meta", {}).get("push_notification_configs", [])
        for config in configs:
            if str(config.get("id")) == config_id:
                return config.copy()
        return None

    def list_task_push_notification_configs(
        self,
        task_id: str,
        team_id: str,
    ) -> List[Dict]:
        task = self.get_task(task_id, team_id)
        if not task:
            return []
        configs = task.get("request_meta", {}).get("push_notification_configs", [])
        return [config.copy() for config in configs if isinstance(config, dict)]

    def delete_task_push_notification_config(
        self,
        task_id: str,
        team_id: str,
        config_id: str,
    ) -> bool:
        task = self._get_mutable_task(task_id, team_id)
        request_meta = task.setdefault("request_meta", {})
        configs = request_meta.setdefault("push_notification_configs", [])
        existing_index = self._find_config_index(configs, config_id)
        if existing_index is None:
            return False
        del configs[existing_index]
        return True

    def delete_expired_tasks(self, cutoff_date: datetime) -> List[str]:
        to_delete = []
        # Need list() to iterate while modifying or just iterate items
        for tid, task in _TASK_STATE.items():
            # Ensure created_at exists
            if task.get("created_at") and task["created_at"] < cutoff_date:
                to_delete.append(tid)
                
        for tid in to_delete:
            del _TASK_STATE[tid]
            
        return to_delete

    def _normalize_datetime(self, value: Optional[datetime]) -> datetime:
        if value is None:
            return datetime.min.replace(tzinfo=timezone.utc)
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    def _get_mutable_task(self, task_id: str, team_id: str) -> Dict:
        task = _TASK_STATE.get(task_id)
        if not task or task.get("team_id") != team_id:
            raise KeyError("Task not found")
        return task

    def _find_config_index(self, configs: List[Dict], config_id: str) -> Optional[int]:
        for index, config in enumerate(configs):
            if str(config.get("id")) == config_id:
                return index
        return None
