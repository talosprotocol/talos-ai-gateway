import pytest
from datetime import datetime, timezone
from typing import Optional
from unittest.mock import MagicMock, AsyncMock, patch
from fastapi.testclient import TestClient
from app.main import app
from app.api.a2a.routes import get_sse_auth
from app.middleware.auth_public import AuthContext, get_auth_context
from app.adapters.postgres.task_store import PostgresTaskStore
from app.dependencies import get_task_store
from app.adapters.redis.client import get_redis_client
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from app.adapters.postgres.models import Base
from app.settings import settings
from app.api.a2a.jsonrpc import JsonRpcException

# Setup In-Memory SQLite
SQLALCHEMY_DATABASE_URL = "sqlite://"
engine = create_engine(SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False}, poolclass=StaticPool)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

@pytest.fixture
def db_session():
    Base.metadata.create_all(bind=engine)
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()
        Base.metadata.drop_all(bind=engine)

client = TestClient(app)

# -----------------------------------------------------------------------------
# Part 1: Route Security Tests (Integration)
# Verifies: Auth Required, Scope Required, Dev Mode Token
# -----------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_sse_route_security():
    # Mock stream_task_events to avoid execution
    with patch("app.api.a2a.routes.stream_task_events") as m_stream:
        # 1. No Auth -> 401
        resp = client.get("/a2a/v1/tasks/task-1/events")
        assert resp.status_code == 401

        # 2. Query Token in Prod -> 401
        settings.dev_mode = False
        resp = client.get("/a2a/v1/tasks/task-1/events?token=sk-test-key-1")
        assert resp.status_code == 401
        
        # 3. Scope Missing -> 403
        # Mock auth to return context without a2a.stream
        async def mock_auth_no_scope():
             return AuthContext(
                 key_id="k1", team_id="t1", org_id="o1",
                 scopes=["a2a.invoke"], # Missing stream
                 allowed_model_groups=["*"], allowed_mcp_servers=["*"]
             )
        from app.api.a2a.routes import get_integrated_auth
        app.dependency_overrides[get_integrated_auth] = mock_auth_no_scope
        
        resp = client.get("/a2a/v1/tasks/task-1/events") 
        assert resp.status_code == 403
        data = resp.json()
        assert data["error"]["talos_code"] == "RBAC_DENIED"
        assert "request_id" in data["error"]
        app.dependency_overrides = {}

# -----------------------------------------------------------------------------
# Part 2: Generator Logic Tests (Unit)
# Verifies: Cross-Team Isolation (Not Found)
# -----------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_stream_generator_cross_team_logic(db_session):
    from app.domain.a2a.streaming import stream_task_events
    import fastapi.concurrency
    import asyncio
    
    # Setup Store
    task_store = PostgresTaskStore(db_session)
    
    # Create Task for Team 2
    task_store.create_task({
        "id": "task-2", "team_id": "team-2", "key_id": "k2",
        "status": "queued", "version": 1, "method": "tasks.send"
    })
    
    # Patch run_in_threadpool because stream_task_events uses it
    async def fake_run(func, *args, **kwargs):
        if asyncio.iscoroutinefunction(func): return await func(*args, **kwargs)
        return func(*args, **kwargs)
    
    with patch("fastapi.concurrency.run_in_threadpool", side_effect=fake_run):
        # Attempt to access as Team 1
        gen = stream_task_events(
            task_id="task-2", 
            team_id="team-1", 
            task_store=task_store, 
            redis_client=MagicMock(),
            request_id="req-1"
        )
        
        # Expect JsonRpcException (Task Not Found) immediately
        with pytest.raises(JsonRpcException) as exc:
            await gen.__anext__()
            
        assert exc.value.code == -32000
        assert "Task not found" in exc.value.message
