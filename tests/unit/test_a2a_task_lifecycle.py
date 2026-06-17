"""Unit tests for A2A Task Lifecycle Manager."""
import pytest
from unittest.mock import MagicMock
from app.domain.a2a.task_lifecycle import TaskLifecycleManager, TaskState
from app.adapters.memory_store.stores import MemoryTaskStore

@pytest.fixture
def clean_memory_store():
    # Use MemoryTaskStore and clear state to ensure isolation
    from app.adapters.memory_store.stores import _TASK_STATE
    _TASK_STATE.clear()
    return MemoryTaskStore()

@pytest.mark.asyncio
async def test_task_lifecycle_transitions(clean_memory_store):
    store = clean_memory_store
    manager = TaskLifecycleManager(store)
    
    task_id = "task-1"
    team_id = "team-a"
    
    # 1. Create a task in PENDING state
    task_data = {
        "id": task_id,
        "team_id": team_id,
        "status": TaskState.PENDING,
        "version": 1,
        "request_meta": {"purpose": "testing"},
        "state_metadata": {},
        "artifacts": None
    }
    store.create_task(task_data)
    
    # 2. Transition from PENDING to IN_PROGRESS
    res = await manager.update_state(
        task_id=task_id,
        team_id=team_id,
        state=TaskState.IN_PROGRESS,
        metadata={"worker_id": "agent-x"}
    )
    
    assert res["task_id"] == task_id
    assert res["new_state"] == TaskState.IN_PROGRESS
    assert res["version"] == 2
    
    # Verify retrieved task state
    task = store.get_task(task_id, team_id)
    assert task["status"] == TaskState.IN_PROGRESS
    assert task["version"] == 2
    assert task["state_metadata"]["worker_id"] == "agent-x"
    assert len(task["state_metadata"]["history"]) == 1
    assert task["state_metadata"]["history"][0]["from_state"] == TaskState.PENDING
    assert task["state_metadata"]["history"][0]["to_state"] == TaskState.IN_PROGRESS

    # 3. Transition from IN_PROGRESS to COMPLETED with artifacts
    artifacts_payload = {"download_url": "https://example.com/artifact"}
    res2 = await manager.update_state(
        task_id=task_id,
        team_id=team_id,
        state=TaskState.COMPLETED,
        artifacts=artifacts_payload,
        metadata={"completed_by": "agent-x"}
    )
    
    assert res2["new_state"] == TaskState.COMPLETED
    assert res2["version"] == 3
    
    # Verify final task state
    task = store.get_task(task_id, team_id)
    assert task["status"] == TaskState.COMPLETED
    assert task["artifacts"] == artifacts_payload
    assert task["state_metadata"]["completed_by"] == "agent-x"
    assert len(task["state_metadata"]["history"]) == 2
    assert task["state_metadata"]["history"][1]["from_state"] == TaskState.IN_PROGRESS
    assert task["state_metadata"]["history"][1]["to_state"] == TaskState.COMPLETED

@pytest.mark.asyncio
async def test_task_lifecycle_not_found(clean_memory_store):
    manager = TaskLifecycleManager(clean_memory_store)
    
    with pytest.raises(ValueError, match="Task missing-task not found"):
        await manager.update_state(
            task_id="missing-task",
            team_id="team-a",
            state=TaskState.IN_PROGRESS
        )
