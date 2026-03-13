from typing import Dict, Optional, Any
from app.domain.interfaces import TaskStore

class TaskState:
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"

class TaskLifecycleManager:
    def __init__(self, task_store: TaskStore):
        self.task_store = task_store

    async def update_state(
        self, 
        task_id: str, 
        team_id: str,
        state: str, 
        artifacts: Optional[Dict[str, Any]] = None,
        metadata: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Transitions a task to a new state and attaches artifacts/metadata.
        """
        task = self.task_store.get_task(task_id, team_id)
        if not task:
            raise ValueError(f"Task {task_id} not found")

        current_version = task.get("version", 0)
        
        # Merge metadata if provided
        state_metadata = task.get("state_metadata") or {}
        if metadata:
            state_metadata.update(metadata)
        
        # Record transition in state_metadata
        history = state_metadata.get("history") or []
        history.append({
            "from_state": task.get("status"),
            "to_state": state,
            "timestamp": "now" # Mocking timestamp for now, TaskStore handles updated_at
        })
        state_metadata["history"] = history

        new_version = self.task_store.update_task_status(
            task_id=task_id,
            status=state,
            expected_version=current_version,
            artifacts=artifacts,
            state_metadata=state_metadata
        )
        
        return {
            "task_id": task_id,
            "new_state": state,
            "version": new_version
        }
