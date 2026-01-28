"""phase9_3_tga

Revision ID: d1e2f3a4b5c6
Revises: c7e3f8a1d2b4
Create Date: 2026-01-27 12:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = 'd1e2f3a4b5c6'
down_revision = 'c7e3f8a1d2b4'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # --- tga_traces table ---
    op.create_table(
        'tga_traces',
        sa.Column('trace_id', sa.String(), nullable=False),
        sa.Column('schema_id', sa.String(), nullable=True),
        sa.Column('schema_version', sa.String(), nullable=True),
        sa.Column('plan_id', sa.String(), nullable=True),
        sa.Column('current_state', sa.String(), nullable=True),
        sa.Column('last_sequence_number', sa.Integer(), nullable=True),
        sa.Column('last_entry_digest', sa.String(), nullable=True),
        sa.Column('state_digest', sa.String(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('trace_id')
    )
    
    # --- tga_logs table ---
    op.create_table(
        'tga_logs',
        sa.Column('trace_id', sa.String(), nullable=False),
        sa.Column('sequence_number', sa.Integer(), nullable=False),
        sa.Column('entry_digest', sa.String(), nullable=False),
        sa.Column('prev_entry_digest', sa.String(), nullable=False),
        sa.Column('ts', sa.String(), nullable=False), # String to preserve exact format for hashing
        sa.Column('from_state', sa.String(), nullable=False),
        sa.Column('to_state', sa.String(), nullable=False),
        sa.Column('artifact_type', sa.String(), nullable=False),
        sa.Column('artifact_id', sa.String(), nullable=False),
        sa.Column('artifact_digest', sa.String(), nullable=False),
        sa.Column('tool_call_id', sa.String(), nullable=True),
        sa.Column('idempotency_key', sa.String(), nullable=True),
        sa.Column('artifact_payload', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('schema_id', sa.String(), nullable=False),
        sa.Column('schema_version', sa.String(), nullable=False),
        sa.PrimaryKeyConstraint('trace_id', 'sequence_number'),
        sa.ForeignKeyConstraint(['trace_id'], ['tga_traces.trace_id'], )
    )


def downgrade() -> None:
    op.drop_table('tga_logs')
    op.drop_table('tga_traces')
