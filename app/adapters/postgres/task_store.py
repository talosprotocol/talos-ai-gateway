from typing import Dict, Optional, Any, List
from datetime import datetime, timezone
from sqlalchemy import and_, or_
from sqlalchemy.orm import Session
from app.domain.interfaces import TaskStore
from app.adapters.postgres.models import A2ATask

def to_dict(obj):
    if not obj:
        return None
    d = {c.name: getattr(obj, c.name) for c in obj.__table__.columns}
    return d

class PostgresTaskStore(TaskStore):
    def __init__(self, db: Session):
        self.db = db

    def create_task(self, task_data: Dict[str, Any]) -> None:
        obj = A2ATask(**task_data)
        self.db.add(obj)
        self.db.commit()

    def update_task_status(
        self, 
        task_id: str, 
        status: str, 
        expected_version: int,
        result: Optional[Dict] = None, 
        artifacts: Optional[Dict] = None,
        state_metadata: Optional[Dict] = None,
        error: Optional[Dict] = None
    ) -> int:
        updates = {
            "status": status,
            "version": expected_version + 1,
            "updated_at": datetime.now(timezone.utc)
        }
        if result is not None:
            updates["result"] = result
        if artifacts is not None:
            updates["artifacts"] = artifacts
        if state_metadata is not None:
            updates["state_metadata"] = state_metadata
        if error is not None:
            updates["error"] = error
            
        # Atomic Update (CAS)
        row_count = self.db.query(A2ATask).filter(
            A2ATask.id == task_id,
            A2ATask.version == expected_version
        ).update(updates)
        
        self.db.commit()
        
        if row_count == 0:
             raise ValueError(f"Version conflict or Task {task_id} not found: expected {expected_version}")
             
        return expected_version + 1

    def delete_expired_tasks(self, cutoff_date: datetime) -> List[str]:
        """Delete tasks older than cutoff in batches of 1000."""
        all_deleted_ids = []
        batch_size = 1000
        
        while True:
            # Select next batch of IDs
            expired_batch = (
                self.db.query(A2ATask.id)
                .filter(A2ATask.created_at < cutoff_date)
                .limit(batch_size)
                .all()
            )
            
            if not expired_batch:
                break
                
            batch_ids = [row[0] for row in expired_batch]
            
            # Delete this batch
            self.db.query(A2ATask).filter(A2ATask.id.in_(batch_ids)).delete(synchronize_session=False)
            self.db.commit()
            
            all_deleted_ids.extend(batch_ids)
            
            # If we got less than the batch size, we're done
            if len(batch_ids) < batch_size:
                break
                
        return all_deleted_ids

    def get_task(self, task_id: str, team_id: str) -> Optional[Dict[str, Any]]:
        # Enforce tenancy check in query
        obj = self.db.query(A2ATask).filter(
            A2ATask.id == task_id,
            A2ATask.team_id == team_id
        ).first()
        
        return to_dict(obj)

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
    ) -> tuple[List[Dict[str, Any]], Optional[tuple[datetime, str]], int]:
        query = self.db.query(A2ATask).filter(A2ATask.team_id == team_id)

        if status:
            query = query.filter(A2ATask.status == status)
        if context_id is not None:
            query = query.filter(
                A2ATask.request_meta["context_id"].astext == context_id
            )
        if status_timestamp_after is not None:
            query = query.filter(A2ATask.updated_at >= status_timestamp_after)
        total_size = query.count()

        paged_query = query
        if cursor_updated_at is not None and cursor_task_id is not None:
            paged_query = paged_query.filter(
                or_(
                    A2ATask.updated_at < cursor_updated_at,
                    and_(
                        A2ATask.updated_at == cursor_updated_at,
                        A2ATask.id < cursor_task_id,
                    ),
                )
            )

        paged_query = paged_query.order_by(A2ATask.updated_at.desc(), A2ATask.id.desc())

        rows = [to_dict(obj) for obj in paged_query.limit(page_size + 1).all()]

        next_cursor = None
        if len(rows) > page_size:
            last = rows[page_size - 1]
            last_updated = last.get("updated_at") or last.get("created_at")
            assert isinstance(last_updated, datetime)
            next_cursor = (last_updated, str(last["id"]))
            rows = rows[:page_size]

        return rows, next_cursor, total_size

    def create_task_push_notification_config(
        self,
        task_id: str,
        team_id: str,
        config: Dict[str, Any],
    ) -> Dict[str, Any]:
        obj = self._get_task_obj(task_id, team_id)
        request_meta = dict(obj.request_meta or {})
        configs = list(request_meta.get("push_notification_configs", []))

        existing_index = self._find_config_index(configs, str(config["id"]))
        stored = config.copy()
        if existing_index is None:
            configs.append(stored)
        else:
            configs[existing_index] = stored

        request_meta["push_notification_configs"] = configs
        obj.request_meta = request_meta
        obj.updated_at = datetime.now(timezone.utc)
        self.db.commit()
        return stored.copy()

    def get_task_push_notification_config(
        self,
        task_id: str,
        team_id: str,
        config_id: str,
    ) -> Optional[Dict[str, Any]]:
        obj = self._get_task_obj(task_id, team_id, required=False)
        if obj is None:
            return None
        request_meta = dict(obj.request_meta or {})
        configs = request_meta.get("push_notification_configs", [])
        for config in configs:
            if str(config.get("id")) == config_id:
                return dict(config)
        return None

    def list_task_push_notification_configs(
        self,
        task_id: str,
        team_id: str,
    ) -> List[Dict[str, Any]]:
        obj = self._get_task_obj(task_id, team_id, required=False)
        if obj is None:
            return []
        request_meta = dict(obj.request_meta or {})
        configs = request_meta.get("push_notification_configs", [])
        return [dict(config) for config in configs if isinstance(config, dict)]

    def delete_task_push_notification_config(
        self,
        task_id: str,
        team_id: str,
        config_id: str,
    ) -> bool:
        obj = self._get_task_obj(task_id, team_id)
        request_meta = dict(obj.request_meta or {})
        configs = list(request_meta.get("push_notification_configs", []))
        existing_index = self._find_config_index(configs, config_id)
        if existing_index is None:
            return False

        del configs[existing_index]
        request_meta["push_notification_configs"] = configs
        obj.request_meta = request_meta
        obj.updated_at = datetime.now(timezone.utc)
        self.db.commit()
        return True

    def _get_task_obj(
        self,
        task_id: str,
        team_id: str,
        *,
        required: bool = True,
    ) -> Optional[A2ATask]:
        obj = self.db.query(A2ATask).filter(
            A2ATask.id == task_id,
            A2ATask.team_id == team_id,
        ).first()
        if obj is None and required:
            raise KeyError("Task not found")
        return obj

    def _find_config_index(
        self,
        configs: List[Dict[str, Any]],
        config_id: str,
    ) -> Optional[int]:
        for index, config in enumerate(configs):
            if str(config.get("id")) == config_id:
                return index
        return None
