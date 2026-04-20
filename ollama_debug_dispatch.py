import os
import sys
import asyncio
import json
from unittest.mock import MagicMock, AsyncMock
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

# Add app to path
sys.path.append(os.path.join(os.getcwd(), "services/ai-gateway"))

from app.domain.a2a.dispatcher import A2ADispatcher
from app.middleware.auth_public import AuthContext
from app.domain.routing import RoutingService
from app.adapters.postgres.stores import PostgresUpstreamStore, PostgresModelGroupStore, PostgresRoutingPolicyStore, PostgresAuditStore
from app.adapters.postgres.task_store import PostgresTaskStore
from app.dependencies import get_health_state

DATABASE_URL = "postgresql://talos:talos_dev_password@localhost:5433/talos"

async def run_debug():
    engine = create_engine(DATABASE_URL)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    db = SessionLocal()

    auth = AuthContext(
        key_id="compat-dev-key",
        team_id="compat-dev-team",
        org_id="compat-dev-org",
        scopes=["a2a.send", "llm.invoke"],
        allowed_model_groups=["*"],
        allowed_mcp_servers=["*"],
        principal_id="compat-dev-principal",
    )
    
    u_store = PostgresUpstreamStore(db)
    mg_store = PostgresModelGroupStore(db)
    rp_store = PostgresRoutingPolicyStore(db)
    audit_store = PostgresAuditStore(db)
    task_store = PostgresTaskStore(db)
    
    routing = RoutingService(u_store, mg_store, rp_store, get_health_state())
    
    # Mock RL
    rl = AsyncMock()
    rl.check_limit.return_value = MagicMock(allowed=True)
    usage = MagicMock()
    mcp_client = MagicMock()
    
    dispatcher = A2ADispatcher(
        auth=auth,
        routing_service=routing,
        audit_store=audit_store,
        rl_store=rl,
        usage_store=usage,
        task_store=task_store,
        mcp_client=mcp_client
    )
    
    # Disable Redis for this script
    dispatcher._get_redis = AsyncMock(return_value=None)
    
    params = {
        "messages": [{"role": "user", "content": "What is the capital of Japan?"}],
        "model_group_id": "ollama-group"
    }
    
    print("Directly calling dispatcher.handle_send...")
    try:
        result = await dispatcher.handle_send(params, "debug-request-id")
        print("\nSuccess Result:")
        print(json.dumps(result, indent=2))
    except Exception as e:
        print(f"\nCaught Error: {e}")
        import traceback
        traceback.print_exc()
    finally:
        db.close()

if __name__ == "__main__":
    asyncio.run(run_debug())
