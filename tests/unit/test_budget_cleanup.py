import pytest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

from sqlalchemy.orm import Session
from app.domain.budgets.service import BudgetService
from app.adapters.postgres.models import BudgetScope, BudgetReservation

@pytest.fixture
def db_session():
    return MagicMock(spec=Session)

@pytest.fixture
def redis_client():
    redis = MagicMock()
    redis.incrbyfloat = AsyncMock()
    redis.set = AsyncMock()
    return redis

@pytest.fixture
def budget_service(redis_client):
    return BudgetService(redis_client)

@pytest.mark.asyncio
async def test_release_expired_reservations(budget_service, db_session):
    # Setup: One expired reservation, one active
    now = datetime.now(timezone.utc)
    expired_res = BudgetReservation(
        id="res1",
        request_id="req1",
        scope_team_id="team1",
        scope_key_id="key1",
        reserved_usd=Decimal("0.10"),
        status="ACTIVE",
        expires_at=now - timedelta(minutes=1),
        created_at=now - timedelta(minutes=16)
    )
    BudgetReservation(
        id="res2",
        request_id="req2",
        scope_team_id="team1",
        scope_key_id="key1",
        reserved_usd=Decimal("0.05"),
        status="ACTIVE",
        expires_at=now + timedelta(minutes=14),
        created_at=now - timedelta(minutes=1)
    )
    
    db_session.query().filter().limit().all.return_value = [expired_res]
    
    count = await budget_service.release_expired_reservations(db_session)
    
    assert count == 1
    assert expired_res.status == "EXPIRED"
    db_session.commit.assert_called_once()

@pytest.mark.asyncio
async def test_reconcile_drift(budget_service, db_session):
    now = datetime.now(timezone.utc)
    period_start = now.replace(day=1).date()
    
    scope = BudgetScope(
        id="scope1",
        scope_type="team",
        scope_id="team1",
        period_start=period_start,
        reserved_usd=Decimal("0.20")  # Drifting from actual 0.10
    )
    
    db_session.query().filter().all.return_value = [scope]
    db_session.scalar.return_value = Decimal("0.10")
    
    errors = await budget_service.reconcile_drift(db_session, fix_drift=True)
    
    assert errors == 1
    assert scope.reserved_usd == Decimal("0.10")
    db_session.commit.assert_called_once()
