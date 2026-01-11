import asyncio
import json
import logging
from datetime import datetime
from typing import AsyncGenerator, Optional
import redis.asyncio as redis
from app.domain.interfaces import TaskStore
from app.api.a2a.jsonrpc import JsonRpcException

logger = logging.getLogger(__name__)

# Constants
SSE_KEEP_ALIVE_INTERVAL = 15 # Seconds
SSE_MAX_DURATION = 900 # 15 Minutes
SSE_IDLE_TIMEOUT = 300 # 5 Minutes (Disconnect if no events)

async def stream_task_events(
    task_id: str,
    team_id: str,
    task_store: TaskStore,
    redis_client: redis.Redis,
    request_id: str,
    after_cursor: Optional[str] = None
) -> AsyncGenerator[str, None]:
    """
    Yields SSE events for a specific task with resumption support.
    """
    from fastapi.concurrency import run_in_threadpool
    from app.settings import settings
    
    task = await run_in_threadpool(task_store.get_task, task_id, team_id)
    if not task:
        raise JsonRpcException(-32000, "Task not found", data={"talos_code": "NOT_FOUND"})

    # Resumption logic: if after_cursor matches current state, skip initial
    current_event_id = f"{task_id}:{task['version']}"
    
    if after_cursor != current_event_id:
        # Initial State (unless they already have it)
        now_utc = datetime.now(datetime.UTC)
        initial_event = {
            "event_id": current_event_id,
            "task_id": task_id,
            "status": task["status"],
            "version": task["version"],
            "updated_at": task["updated_at"].isoformat() if task["updated_at"] else now_utc.isoformat(),
            "type": "initial"
        }
        yield f"id: {initial_event['event_id']}\ndata: {json.dumps(initial_event)}\n\n"
    
    # End if already final?
    if task["status"] in ("completed", "failed", "canceled"):
        return

    # 3. Redis Subscription
    pubsub = redis_client.pubsub()
    channel = f"a2a:tasks:{task_id}"
    await pubsub.subscribe(channel)
    
    start_time = datetime.now(datetime.UTC)
    last_activity = datetime.now(datetime.UTC)
    
    # Use settings for limits
    max_duration = settings.a2a_sse_max_duration_seconds
    idle_timeout = settings.a2a_sse_idle_timeout_seconds
    
    # Active streams gauge
    gauge_key = "a2a:metrics:active_streams"
    if redis_client:
        await redis_client.incr(gauge_key)
        
    try:
        while True:
            current_time = datetime.now(datetime.UTC)
            
            # Check Max Duration
            if (current_time - start_time).total_seconds() > max_duration:
                err = {
                    "error": {
                        "talos_code": "TIMEOUT",
                        "message": "Max stream duration reached",
                        "request_id": request_id
                    }
                }
                yield f"event: error\ndata: {json.dumps(err)}\n\n"
                break
                
            # Check Idle
            if (current_time - last_activity).total_seconds() > idle_timeout:
                err = {
                    "error": {
                        "talos_code": "IDLE_TIMEOUT",
                        "message": "Stream idle",
                        "request_id": request_id
                    }
                }
                yield f"event: error\ndata: {json.dumps(err)}\n\n"
                break

            # Wait for message
            message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
            
            if message:
                last_activity = datetime.now(datetime.UTC) # Actual Redis activity resets idle timer
                data_str = message["data"]
                try:
                    event_data = json.loads(data_str)
                    yield f"id: {event_data.get('event_id')}\ndata: {data_str}\n\n"
                    
                    if event_data.get("status") in ("completed", "failed", "canceled"):
                        break
                except json.JSONDecodeError:
                    pass
            
            # Keep Alive logic
            if int(current_time.timestamp()) % SSE_KEEP_ALIVE_INTERVAL == 0:
                 yield ": keep-alive\n\n"
                 
            await asyncio.sleep(0.1)
            
    finally:
        if redis_client:
            await redis_client.decr(gauge_key)
        await pubsub.unsubscribe(channel)
        await pubsub.close()
