"""Create a2a_tasks table

Revision ID: 002_a2a_tasks
Revises: 001_initial
Create Date: 2026-01-10

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = '002_a2a_tasks'
down_revision: Union[str, None] = '001_initial'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('a2a_tasks',
        sa.Column('id', sa.String(64), primary_key=True),
        sa.Column('team_id', sa.String(255), sa.ForeignKey('teams.id'), nullable=False),
        sa.Column('key_id', sa.String(255), sa.ForeignKey('virtual_keys.id'), nullable=False),
        sa.Column('org_id', sa.String(255), nullable=True),
        sa.Column('request_id', sa.String(64), nullable=True),
        sa.Column('origin_surface', sa.String(20), server_default='a2a'),
        sa.Column('method', sa.String(50), nullable=True),
        sa.Column('status', sa.String(20), nullable=False, server_default='queued'),
        sa.Column('version', sa.Integer(), nullable=False, server_default='1'),
        sa.Column('request_meta', sa.JSON(), nullable=True),
        sa.Column('input_redacted', sa.JSON(), nullable=True),
        sa.Column('result', sa.JSON(), nullable=True),
        sa.Column('error', sa.JSON(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.CheckConstraint("status IN ('queued','running','completed','failed','canceled')", name='check_a2a_status_enum')
    )
    
    op.create_index('idx_a2a_tasks_team_created', 'a2a_tasks', ['team_id', sa.text('created_at DESC')])
    op.create_index('idx_a2a_tasks_key_id', 'a2a_tasks', ['key_id'])
    op.create_index('idx_a2a_tasks_request_id', 'a2a_tasks', ['request_id'])
    op.create_index('idx_a2a_tasks_org_id', 'a2a_tasks', ['org_id'])


def downgrade() -> None:
    op.drop_index('idx_a2a_tasks_org_id', table_name='a2a_tasks')
    op.drop_index('idx_a2a_tasks_request_id', table_name='a2a_tasks')
    op.drop_index('idx_a2a_tasks_key_id', table_name='a2a_tasks')
    op.drop_index('idx_a2a_tasks_team_created', table_name='a2a_tasks')
    op.drop_table('a2a_tasks')
