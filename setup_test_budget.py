import os
import sys
import uuid
import hmac
import hashlib
from decimal import Decimal
from datetime import datetime, timezone
import sqlalchemy as sa
from sqlalchemy.orm import sessionmaker, declarative_base
import redis as redis_lib

# Add app to path
sys.path.append(os.path.join(os.path.dirname(__file__), "app"))
try:
    from app.utils.id import uuid7
except ImportError:
    # Fallback for local run
    pass

# Database Setup
DATABASE_URL = os.getenv("DATABASE_WRITE_URL", "postgresql://talos:talos@localhost:5452/talos")
engine = sa.create_engine(DATABASE_URL)
Session = sessionmaker(bind=engine)
session = Session()

# Models (Minimal for setup)
Base = declarative_base()

class Org(Base):
    __tablename__ = "orgs"
    id = sa.Column(sa.String, primary_key=True)
    name = sa.Column(sa.String)

class Team(Base):
    __tablename__ = "teams"
    id = sa.Column(sa.String, primary_key=True)
    org_id = sa.Column(sa.String)
    name = sa.Column(sa.String)
    budget = sa.Column(sa.JSON)
    budget_mode = sa.Column(sa.String, default="off")
    overdraft_usd = sa.Column(sa.Numeric, default=0)

class VirtualKey(Base):
    __tablename__ = "virtual_keys"
    id = sa.Column(sa.String, primary_key=True)
    team_id = sa.Column(sa.String)
    key_hash = sa.Column(sa.String)
    scopes = sa.Column(sa.JSON)
    allowed_model_groups = sa.Column(sa.JSON)
    budget = sa.Column(sa.JSON)
    budget_mode = sa.Column(sa.String, default="off")
    overdraft_usd = sa.Column(sa.Numeric, default=0)
    revoked = sa.Column(sa.Boolean, default=False)

class Principal(Base):
    __tablename__ = "principals"
    id = sa.Column(sa.String, primary_key=True)
    type = sa.Column(sa.String)
    display_name = sa.Column(sa.String)

class Role(Base):
    __tablename__ = "roles"
    id = sa.Column(sa.String, primary_key=True)
    name = sa.Column(sa.String)
    permissions = sa.Column(sa.JSON)
    built_in = sa.Column(sa.Boolean, default=False)

class RoleBinding(Base):
    __tablename__ = "role_bindings"
    id = sa.Column(sa.String, primary_key=True)
    principal_id = sa.Column(sa.String)
    role_id = sa.Column(sa.String)
    scope_type = sa.Column(sa.String)

class BudgetScope(Base):
    __tablename__ = "budget_scopes"
    id = sa.Column(sa.String, primary_key=True)
    scope_type = sa.Column(sa.String)
    scope_id = sa.Column(sa.String)
    period_start = sa.Column(sa.Date)
    limit_usd = sa.Column(sa.Numeric)
    used_usd = sa.Column(sa.Numeric, default=0)
    reserved_usd = sa.Column(sa.Numeric, default=0)

def setup_scope(scope_type, scope_id, period_start, limit_usd):
    # Unique constraint is on (scope_type, scope_id, period_start)
    # id is usually a uuid7 or similar, let's use a stable one for tests
    f"{scope_type}:{scope_id}:{period_start.strftime('%Y-%m')}"
    scope = session.query(BudgetScope).filter(BudgetScope.scope_type == scope_type, BudgetScope.scope_id == scope_id, BudgetScope.period_start == period_start).first()
    if scope:
        scope.limit_usd = Decimal(str(limit_usd))
        scope.used_usd = Decimal("0")
        scope.reserved_usd = Decimal("0")
    else:
        scope = BudgetScope(
            id=str(uuid.uuid4()),
            scope_type=scope_type,
            scope_id=scope_id,
            period_start=period_start,
            limit_usd=Decimal(str(limit_usd)),
            used_usd=Decimal("0"),
            reserved_usd=Decimal("0")
        )
        session.add(scope)
    print(f"Scope setup: {scope_type}:{scope_id} with limit {limit_usd}")
def setup_redis_scope(r, scope_type, scope_id, used_usd, reserved_usd=0):
    # Use UTC to match application logic
    period = datetime.now(timezone.utc).strftime("%Y-%m")
    base = f"budget:{period}:{scope_type}:{scope_id}"
    r.set(f"{base}:used", str(used_usd))
    r.set(f"{base}:reserved", str(reserved_usd))

def hash_key(raw_key: str) -> str:
    pepper = (os.getenv("TALOS_KEY_PEPPER") or "dev-pepper-change-in-prod").encode()
    pepper_id = os.getenv("TALOS_PEPPER_ID", "p1")
    h = hmac.new(pepper, raw_key.encode(), hashlib.sha256)
    return f"{pepper_id}:{h.hexdigest()}"

def main():
    print("Starting test budget setup...")
    
    # 0. Redis Setup
    redis_url = os.getenv("REDIS_URL", "redis://localhost:6380/0")
    r = redis_lib.from_url(redis_url, decode_responses=True)
    
    # Flush existing budget keys
    for key in r.scan_iter("budget:*"):
        r.delete(key)
    print("Redis budget keys flushed.")

    # 1. Truncate all tables for fresh start
    session.execute(sa.text("TRUNCATE orgs, teams, virtual_keys, budget_scopes, budget_reservations, usage_events, principals, roles, role_bindings CASCADE"))
    session.commit()
    print("Database tables truncated.")

    # Create Org
    org = Org(id="org-test", name="Test Org")
    session.add(org)
    session.commit()

    period_date = datetime.utcnow().date().replace(day=1)
    
    # 1. HARD Enforcement Test Data
    # Team: $0.10 limit
    # Key: $0.03 limit
    # First req (estimate $0.0075) should pass.
    # Second req (estimate $0.0075) should pass.
    # Third req (estimate $0.0075) should pass.
    # Fourth req (estimate $0.0075) should fail if it hits exactly 0.03? 
    # Wait, 4 * 0.0075 = 0.03. So it might pass. 5th should fail.
    # verify_budgets.py says: Req 1 status 200, Req 2 status 402.
    # This means limit is very tight or cost estimation is higher.
    
    t_hard = Team(id="team-hard", org_id="org-test", name="Hard Team", budget={"limit_usd": "0.10"}, budget_mode="hard", overdraft_usd=0)
    vk_hard = VirtualKey(id="test-key-hard", team_id="team-hard", key_hash=hash_key("test-key-hard"), scopes=["llm.*"], allowed_model_groups=["*"], budget={"limit_usd": "0.03"}, budget_mode="hard", overdraft_usd=0)
    session.add_all([t_hard, vk_hard])
    setup_scope("team", "team-hard", period_date, 0.10)
    setup_scope("virtual_key", "test-key-hard", period_date, 0.03)
    
    setup_redis_scope(r, "team", "team-hard", 0)
    setup_redis_scope(r, "virtual_key", "test-key-hard", 0)

    # 2. WARN Enforcement Test Data
    t_warn = Team(id="team-warn", org_id="org-test", name="Warn Team", budget={"limit_usd": "0.10"}, budget_mode="warn", overdraft_usd=0)
    vk_warn = VirtualKey(id="test-key-warn", team_id="team-warn", key_hash=hash_key("test-key-warn"), scopes=["llm.*"], allowed_model_groups=["*"], budget={"limit_usd": "0.01"}, budget_mode="warn", overdraft_usd=0)
    session.add_all([t_warn, vk_warn])
    setup_scope("team", "team-warn", period_date, 0.10)
    setup_scope("virtual_key", "test-key-warn", period_date, 0.01)
    
    setup_redis_scope(r, "team", "team-warn", 0)
    setup_redis_scope(r, "virtual_key", "test-key-warn", 0)

    # 3. Concurrency Test Data
    t_conc = Team(id="team-concurrency", org_id="org-test", name="Concurrency Team", budget={"limit_usd": "0.10"}, budget_mode="hard", overdraft_usd=0)
    vk_conc = VirtualKey(id="test-key-concurrency", team_id="team-concurrency", key_hash=hash_key("test-key-concurrency"), scopes=["llm.*"], allowed_model_groups=["*"], budget={"limit_usd": "0.03"}, budget_mode="hard", overdraft_usd=0)
    session.add_all([t_conc, vk_conc])
    setup_scope("team", "team-concurrency", period_date, 0.10)
    setup_scope("virtual_key", "test-key-concurrency", period_date, 0.03)
    
    setup_redis_scope(r, "team", "team-concurrency", 0)
    setup_redis_scope(r, "virtual_key", "test-key-concurrency", 0)

    # 4. Precedence Test Data
    t_prec = Team(id="team-precedence", org_id="org-test", name="Precedence Team", budget={"limit_usd": "0.05"}, budget_mode="hard", overdraft_usd=0)
    vk_prec = VirtualKey(id="test-key-precedence", team_id="team-precedence", key_hash=hash_key("test-key-precedence"), scopes=["llm.*"], allowed_model_groups=["*"], budget={"limit_usd": "1.00"}, budget_mode="hard", overdraft_usd=0)
    session.add_all([t_prec, vk_prec])
    # Setup team usage to 0.04. Capacity 0.05.
    setup_scope("team", "team-precedence", period_date, 0.04)
    setup_scope("virtual_key", "test-key-precedence", period_date, 0.00)
    
    setup_redis_scope(r, "team", "team-precedence", 0.04)
    setup_redis_scope(r, "virtual_key", "test-key-precedence", 0.00)

    # 5. Streaming Test Data
    t_stream = Team(id="team-streaming", org_id="org-test", name="Streaming Team", budget={"limit_usd": "10.00"}, budget_mode="hard", overdraft_usd=0)
    # SET LIMIT TO 0.00 to force failure in non-streaming mode
    vk_stream = VirtualKey(id="test-key-streaming", team_id="team-streaming", key_hash=hash_key("test-key-streaming"), scopes=["llm.*"], allowed_model_groups=["*"], budget={"limit_usd": "0.00"}, budget_mode="hard", overdraft_usd=0)
    session.add_all([t_stream, vk_stream])
    setup_scope("team", "team-streaming", period_date, 0.00)
    setup_scope("virtual_key", "test-key-streaming", period_date, 0.00)
    
    setup_redis_scope(r, "team", "team-streaming", 0)
    setup_redis_scope(r, "virtual_key", "test-key-streaming", 0)

    # 6. Admin RBAC (needed for verify_budgets.py)
    admin_p = Principal(id="admin", type="user", display_name="Admin")
    admin_r = Role(id="role-admin", name="Admin", permissions=["*"], built_in=True)
    admin_b = RoleBinding(id=str(uuid.uuid4()), principal_id="admin", role_id="role-admin", scope_type="global")
    session.add_all([admin_p, admin_r, admin_b])

    session.commit()
    print("Database seeding complete.")

    # Redis Setup already done at start of main
    pass

    # Admin RBAC already seeded

    # Note: verify_budgets.py uses hardcoded key IDs.
    # We already added them to virtual_keys table.
    
    print("Setup complete.")

if __name__ == "__main__":
    main()
