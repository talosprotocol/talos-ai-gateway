"""Setup test budgets for Phase 15 verification."""
import os
import sys
from datetime import datetime
from decimal import Decimal
from sqlalchemy.orm import Session
from sqlalchemy import create_engine, text

# Add app to path
sys.path.append(os.getcwd())

from app.adapters.postgres.models import (
    Org, Team, VirtualKey, LlmUpstream, ModelGroup, Deployment, 
    BudgetScope, BudgetReservation, UsageEvent
)
from app.adapters.postgres.key_store import get_key_store
from app.utils.id import uuid7

def setup():
    db_url = os.getenv("DATABASE_WRITE_URL")
    if not db_url:
        print("DATABASE_WRITE_URL not set")
        sys.exit(1)
        
    engine = create_engine(db_url)
    
    # 0. Clear existing test data for determinism
    with engine.connect() as conn:
        conn.execute(text("TRUNCATE budget_reservations, budget_scopes, usage_events CASCADE;"))
        conn.commit()
        
    session = Session(engine)
    
    # 1. Setup Org
    org_id = "test-org"
    org = session.query(Org).filter(Org.id == org_id).first()
    if not org:
        org = Org(id=org_id, name="Test Org")
        session.add(org)
    
    # 2. Setup Teams
    def setup_team(team_id, name, budget_mode, limit_usd, overdraft_usd="0"):
        team = session.query(Team).filter(Team.id == team_id).first()
        if not team:
            team = Team(
                id=team_id,
                org_id=org_id,
                name=name,
                budget_mode=budget_mode,
                overdraft_usd=Decimal(overdraft_usd),
                budget={"limit_usd": limit_usd}
            )
            session.add(team)
        else:
            team.budget_mode = budget_mode
            team.overdraft_usd = Decimal(overdraft_usd)
            team.budget = {"limit_usd": limit_usd}
        return team

    team_hard = setup_team("team-hard", "Team Hard", "hard", "0.05", "0.02")
    team_concurrency = setup_team("team-concurrency", "Team Concurrency", "hard", "1.00")
    team_precedence = setup_team("team-precedence", "Team Precedence", "hard", "0.05")
    team_streaming = setup_team("team-streaming", "Team Streaming", "hard", "0.00")
    team_warn = setup_team("team-warn", "Team Warn", "warn", "0.01")
    
    # 4. Setup Mock Routing (gpt-4)
    up_id = "mock-openai"
    up = session.query(LlmUpstream).filter(LlmUpstream.id == up_id).first()
    if not up:
        up = LlmUpstream(
            id=up_id,
            provider="openai",
            endpoint="http://localhost:9999/v1", # Mock endpoint
            enabled=True
        )
        session.add(up)
    
    mg_id = "gpt-4"
    mg = session.query(ModelGroup).filter(ModelGroup.id == mg_id).first()
    if not mg:
        mg = ModelGroup(id=mg_id, name="GPT-4 Group", enabled=True)
        session.add(mg)
        session.flush()
        
        dep = Deployment(
            id=str(uuid7()),
            model_group_id=mg_id,
            upstream_id=up_id,
            model_name="gpt-4",
            weight=100
        )
        session.add(dep)

    session.flush()
    
    # Setup Key Store for hashing
    ks = get_key_store(db=session)
    
    # helper to upsert key
    def upsert_key(key_id, raw_key, team_id, mode, limit_usd):
        key_hash = ks.hash_key(raw_key)
        vk = session.query(VirtualKey).filter(VirtualKey.id == key_id).first()
        if vk:
            vk.team_id = team_id
            vk.key_hash = key_hash
            vk.budget_mode = mode
            vk.budget = {"limit_usd": str(limit_usd)}
            vk.scopes = ["llm.invoke", "mcp.invoke", "mcp.read", "llm.read"]
            vk.allowed_model_groups = ["*"]
        else:
            vk = VirtualKey(
                id=key_id,
                team_id=team_id,
                key_hash=key_hash,
                budget_mode=mode,
                budget={"limit_usd": str(limit_usd)},
                scopes=["llm.invoke", "mcp.invoke", "mcp.read", "llm.read"],
                allowed_model_groups=["*"]
            )
            session.add(vk)
        print(f"Key {key_id} configured with mode={mode}, limit={limit_usd}, models=*")

    # Key 1: Hard Key ($0.03 limit)
    upsert_key("key-hard-5c", "test-key-hard", "team-hard", "hard", Decimal("0.03"))
    
    # Key 2: Warn Key
    upsert_key("key-warn", "test-key-warn", "team-warn", "warn", Decimal("0.01"))

    # Key 3: Precedence Test Key ($100 limit, but Team Precedence has $0.05 budget)
    upsert_key("key-precedence", "test-key-precedence", "team-precedence", "hard", Decimal("100.00"))

    # Key 4: Concurrency Key ($0.03 limit)
    upsert_key("key-concurrency", "test-key-concurrency", "team-concurrency", "hard", Decimal("0.03"))

    # Key 5: Streaming Key ($0.00 limit)
    upsert_key("key-streaming", "test-key-streaming", "team-streaming", "hard", Decimal("0.00"))

    session.commit()
    # Manually create/set Team Precedence scope to 0.04 to force block
    period_start = datetime.utcnow().replace(day=1, hour=0, minute=0, second=0, microsecond=0).date()
    tp_scope = session.query(BudgetScope).filter(
        BudgetScope.scope_type == "team",
        BudgetScope.scope_id == "team-precedence",
        BudgetScope.period_start == period_start
    ).first()
    if not tp_scope:
        tp_scope = BudgetScope(
            id=str(uuid7()),
            scope_type="team",
            scope_id="team-precedence",
            period_start=period_start,
            limit_usd=Decimal("0.05"),
            used_usd=Decimal("0.04")
        )
        session.add(tp_scope)
    else:
        tp_scope.used_usd = Decimal("0.04")
        tp_scope.limit_usd = Decimal("0.05")
    
    session.commit()
    print("Team Precedence scope initialized with 0.04 used / 0.05 limit")
    print("Setup complete.")

if __name__ == "__main__":
    setup()
