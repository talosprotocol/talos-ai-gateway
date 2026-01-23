import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, AsyncMock, patch
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.main import app
from app.middleware.auth_public import AuthContext
from app.adapters.postgres.models import Base, A2ATask
from app.adapters.postgres.task_store import PostgresTaskStore
from app.dependencies import get_task_store, get_audit_store, get_rate_limit_store, get_mcp_client

# Setup In-Memory SQLite for "Real DB" tests (closest we get without spinning up Postgres container)
SQLALCHEMY_DATABASE_URL = "sqlite://"

engine = create_engine(
    SQLALCHEMY_DATABASE_URL,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
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

@pytest.fixture
def mock_auth_context():
    return AuthContext(
        key_id="key-123",
        team_id="team-alpha",
        org_id="org-1",
        scopes=["a2a.invoke", "llm.invoke"],
        allowed_model_groups=["gpt-4o"],
        allowed_mcp_servers=["*"]
    )

client = TestClient(app)

AUTH_HEADERS = {
    "Authorization": "Bearer test-token",
    "Content-Type": "application/json"
}

@pytest.mark.asyncio
async def test_persistence_lifecycle_and_privacy(db_session, mock_auth_context):
    # Override Auth
    from app.api.a2a.routes import get_auth_context_or_none
    app.dependency_overrides[get_auth_context_or_none] = lambda: mock_auth_context
    
    # Real Store with SQLite
    task_store = PostgresTaskStore(db_session)
    app.dependency_overrides[get_task_store] = lambda: task_store

    # Mocks for other dependencies
    with patch("app.dependencies.get_audit_store") as m_audit, \
         patch("app.dependencies.get_rate_limit_store") as m_rl, \
         patch("app.dependencies.get_mcp_client") as m_mcp, \
         patch("app.domain.a2a.dispatcher.get_redis_client") as m_redis_getter, \
         patch("app.domain.a2a.dispatcher.invoke_openai_compatible") as m_invoke, \
         patch("app.domain.routing.RoutingService.select_upstream") as m_routing:

         # Setup Mocks
         m_rl.return_value.check_limit = AsyncMock(return_value=MagicMock(allowed=True))
         m_mcp.return_value = MagicMock()
         m_audit.return_value.log_event = AsyncMock() # Fix async audit
         m_audit.return_value.append_event = MagicMock() # Sync audit store used in dispatcher? Wait, dispatcher uses audit_store.append_event which is sync in PostgresAuditStore?
         # Check Dispatcher: It calls self.audit_store.append_event(event). 
         # Protocol has append_event as sync (def append_event).
         
         m_redis = AsyncMock()
         m_redis_getter.return_value = m_redis

         m_routing.return_value = {
             "upstream": {"endpoint": "http://mock", "credentials_ref": "creds"},
             "model_name": "gpt-4o-mock"
         }
         m_invoke.return_value = {
             "choices": [{"message": {"content": "Hello Persistence"}}]
         }

         app.dependency_overrides[get_audit_store] = lambda: m_audit.return_value
         app.dependency_overrides[get_rate_limit_store] = lambda: m_rl.return_value
         app.dependency_overrides[get_mcp_client] = lambda: m_mcp.return_value

         # 1. SEND Request
         payload = {
            "jsonrpc": "2.0",
            "method": "tasks.send",
            "id": "req-1",
            "params": {
                "profile": {"profile_version": "0.1", "profile_id": "a2a-compat", "spec_source": "a2a-protocol"},
                "input": [{"role": "user", "content": [{"text": "Secret Input"}]}]
            }
         }
         
         response = client.post("/a2a/v1/", json=payload, headers=AUTH_HEADERS)
         assert response.status_code == 200
         data = response.json()
         assert "result" in data
         task_result = data["result"]
         task_id = task_result["task_id"]

         # 2. Verify Persistence (Status: Completed)
         db_task = db_session.query(A2ATask).filter(A2ATask.id == task_id).first()
         assert db_task is not None
         assert db_task.status == "completed"
         assert db_task.version >= 2 # created(1) -> running(2) -> completed(3)? Or 1->2->3.
         
         # 3. Verify Privacy (Redaction)
         # "Secret Input" must NOT be in request_meta
         assert "Secret Input" not in str(db_task.request_meta)
         
         # Full Forbidden List Check
         forbidden = ["messages", "prompt", "input", "tool_input", "headers", "authorization", "api_key", "secret", "cookie"]
         for key in forbidden:
             assert key not in db_task.request_meta
         assert db_task.request_meta["method"] == "tasks.send"
         assert db_task.request_meta["model_group_id"] == "gpt-4o"
         assert db_task.request_meta["origin_surface"] == "a2a"
         assert db_task.input_redacted is None # Default off

         # 4. GET Request (Access Control)
         # Should succeed for same team
         payload_get = {
            "jsonrpc": "2.0",
            "method": "tasks.get",
            "id": "req-2",
            "params": {"task_id": task_id}
         }
         response_get = client.post("/a2a/v1/", json=payload_get, headers=AUTH_HEADERS)
         assert response_get.status_code == 200
         data_get = response_get.json()
         assert data_get["result"]["task_id"] == task_id
         assert data_get["result"]["profile"]["profile_version"] == "0.1"
         
         # 5. GET Request (Cross Team)
         # Mock a different team
         other_auth = AuthContext(
             key_id="other", team_id="team-beta", org_id="org-1", 
             scopes=["a2a.invoke"], allowed_model_groups=[], allowed_mcp_servers=[]
         )
         app.dependency_overrides[get_auth_context_or_none] = lambda: other_auth
         
         response_deny = client.post("/a2a/v1/", json=payload_get, headers=AUTH_HEADERS)
         data_deny = response_deny.json()
         assert data_deny["error"]["code"] == -32000
         assert data_deny["error"]["data"]["talos_code"] == "NOT_FOUND"

         # 6. Verify Audit Logging
         audit_mock = m_audit.return_value.append_event
         assert audit_mock.called
         # Check content of one event
         call_args = audit_mock.call_args_list[0]
         event = call_args[0][0] # First arg is event object
         assert event["context"] == "a2a"
         assert event["team_id"] == "team-alpha"
         assert event["request_id"] == "req-1"

         # 7. CAS Conflict Test
         # Reset Auth Override
         app.dependency_overrides[get_auth_context_or_none] = lambda: mock_auth_context
         
         # Create a task manually via store
         t3_id = "task-conflict"
         task_store.create_task({
             "id": t3_id, "team_id": "team-alpha", "key_id": "key-123",
             "status": "queued", "version": 1, "method": "tasks.send"
         })
         
         # Simulate race: Someone else updates it to version 2 in DB
         with db_session.info.get("test_session", db_session) as session: # Just use db_session directly
             t3 = session.query(A2ATask).filter(A2ATask.id == t3_id).first()
             t3.version = 2
             t3.status = "running"
             session.commit()
             
         # Now try to update from version 1 (which store thinks is current if we didn't fetch?)
         # Actually store.update_task_status requires expected_version.
         
         # Note: update_task_status raises ValueError currently in PostgresTaskStore
         # Check implementation: "raise ValueError(f'Version conflict...')"
         
         with pytest.raises(ValueError, match="Version conflict"):
             task_store.update_task_status(
                 task_id=t3_id, status="completed", expected_version=1
             )

@pytest.mark.asyncio
async def test_retention_deletion(db_session):
    # Removed local imports to avoid shadowing
    
    task_store = PostgresTaskStore(db_session)
    
    # Created a year ago
    old_date = datetime.now(timezone.utc) - timedelta(days=365)
    
    t_id = "task-old"
    task = A2ATask(
        id=t_id, team_id="team-1", key_id="k1", status="completed",
        version=1, request_meta={}, request_id="req-old",
        created_at=old_date
    )
    db_session.add(task)
    db_session.commit()
    
    # Verify it exists
    assert task_store.get_task(t_id, "team-1") is not None
    
    # Delete expired (cut off 30 days ago)
    # Using run_in_threadpool if strictly following pattern but direct call ok for sync store method
    cutoff = datetime.now(timezone.utc) - timedelta(days=30)
    deleted = task_store.delete_expired_tasks(cutoff)
    
    assert t_id in deleted
    assert task_store.get_task(t_id, "team-1") is None
