from typing import Dict, Optional, Any, List
from datetime import datetime, timezone
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
        error: Optional[Dict] = None
    ) -> int:
        updates = {
            "status": status,
            "version": expected_version + 1,
            "updated_at": datetime.now(timezone.utc)
        }
        if result is not None:
            updates["result"] = result
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
