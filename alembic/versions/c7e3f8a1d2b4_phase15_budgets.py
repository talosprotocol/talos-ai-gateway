"""phase15_budgets

Revision ID: c7e3f8a1d2b4
Revises: b96a687eb62e
Create Date: 2026-01-19 16:35:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c7e3f8a1d2b4'
down_revision: Union[str, None] = 'b96a687eb62e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Teams
    op.add_column('teams', sa.Column('budget_mode', sa.String(20), nullable=False, server_default='off'))
    op.add_column('teams', sa.Column('overdraft_usd', sa.Numeric(18, 8), nullable=False, server_default='0'))
    op.add_column('teams', sa.Column('max_tokens_default', sa.Integer(), nullable=True))
    op.add_column('teams', sa.Column('budget', sa.JSON(), nullable=True)) # Metadata

    # 2. Virtual Keys
    op.add_column('virtual_keys', sa.Column('budget_mode', sa.String(20), nullable=False, server_default='off'))
    op.add_column('virtual_keys', sa.Column('overdraft_usd', sa.Numeric(18, 8), nullable=False, server_default='0'))
    op.add_column('virtual_keys', sa.Column('max_tokens_default', sa.Integer(), nullable=True))
    # budget already exists in models but might be missing in some DBs if old model didn't have it? 
    # models.py had it before Phase 15, so likely fine. If not, safe add via fallback.

    # 3. Usage Events
    # Alter cost_usd to Numeric. 
    connection = op.get_bind()
    dialect = connection.dialect.name
    
    if dialect == 'postgresql':
        op.alter_column('usage_events', 'cost_usd', type_=sa.Numeric(18, 8), postgresql_using='cost_usd::numeric')
    else:
        # Simple alter for standard SQL
        try:
             op.alter_column('usage_events', 'cost_usd', type_=sa.Numeric(18, 8))
        except:
             pass

    op.add_column('usage_events', sa.Column('pricing_version', sa.String(36), nullable=True))
    op.add_column('usage_events', sa.Column('token_count_source', sa.String(20), nullable=True))
    
    # Request ID Unique
    try:
        op.create_unique_constraint('uq_usage_event_request', 'usage_events', ['request_id'])
    except Exception:
        pass 

    # 4. Budget Tables
    op.create_table('budget_scopes',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('scope_type', sa.String(20), nullable=False),
        sa.Column('scope_id', sa.String(255), nullable=False),
        sa.Column('period_start', sa.Date(), nullable=False),
        sa.Column('limit_usd', sa.Numeric(18, 8), nullable=False, server_default='0'),
        sa.Column('used_usd', sa.Numeric(18, 8), nullable=False, server_default='0'),
        sa.Column('reserved_usd', sa.Numeric(18, 8), nullable=False, server_default='0'),
        sa.Column('overdraft_usd', sa.Numeric(18, 8), nullable=False, server_default='0'),
        sa.Column('last_alert_at', sa.DateTime(), nullable=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.func.now()),
        sa.UniqueConstraint('scope_type', 'scope_id', 'period_start', name='uq_budget_scope_period'),
        sa.CheckConstraint('reserved_usd >= 0', name='check_reserved_usd_pos'),
        sa.CheckConstraint('used_usd >= 0', name='check_used_usd_pos'),
        sa.CheckConstraint('limit_usd >= 0', name='check_limit_usd_pos'),
        sa.CheckConstraint('overdraft_usd >= 0', name='check_overdraft_usd_pos')
    )
    op.create_index('idx_budget_scope_lookup', 'budget_scopes', ['scope_type', 'scope_id'])

    op.create_table('budget_reservations',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('request_id', sa.String(36), nullable=False),
        sa.Column('scope_team_id', sa.String(255), nullable=False),
        sa.Column('scope_key_id', sa.String(255), nullable=False),
        sa.Column('reserved_usd', sa.Numeric(18, 8), nullable=False),
        sa.Column('status', sa.String(20), nullable=False),
        sa.Column('expires_at', sa.DateTime(), nullable=False),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now()),
        sa.UniqueConstraint('request_id', name='uq_budget_reservation_req'),
    )
    op.create_index('idx_reservations_expires', 'budget_reservations', ['expires_at'])

    op.create_table('usage_rollups_daily',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('day', sa.Date(), nullable=False),
        sa.Column('team_id', sa.String(255), nullable=False),
        sa.Column('key_id', sa.String(255), nullable=False),
        sa.Column('used_usd', sa.Numeric(18, 8), nullable=False, server_default='0'),
        sa.Column('input_tokens', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('output_tokens', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('request_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.func.now()),
        sa.UniqueConstraint('day', 'team_id', 'key_id', name='uq_usage_rollup_day')
    )
    op.create_index('idx_usage_rollups_team', 'usage_rollups_daily', ['team_id'])
    op.create_index('idx_usage_rollups_key', 'usage_rollups_daily', ['key_id'])


def downgrade() -> None:
    op.drop_table('usage_rollups_daily')
    op.drop_table('budget_reservations')
    op.drop_table('budget_scopes')
    
    op.drop_constraint('uq_usage_event_request', 'usage_events', type_='unique')
    op.drop_column('usage_events', 'token_count_source')
    op.drop_column('usage_events', 'pricing_version')
    # Revert cost_usd is hard if values have decimal part, leaving formatted as numeric
    
    op.drop_column('virtual_keys', 'max_tokens_default')
    op.drop_column('virtual_keys', 'overdraft_usd')
    op.drop_column('virtual_keys', 'budget_mode')
    
    op.drop_column('teams', 'budget')
    op.drop_column('teams', 'max_tokens_default')
    op.drop_column('teams', 'overdraft_usd')
    op.drop_column('teams', 'budget_mode')
