"""Add A2A Phase 10 tables with constraints

Revision ID: 003_a2a_phase10
Revises: 002_a2a_tasks
Create Date: 2026-01-16

Tables:
- a2a_sessions: Session state between two agents
- a2a_session_events: Append-only session lifecycle events
- a2a_frames: Encrypted messages with replay detection
- a2a_groups: Group state for membership coordination
- a2a_group_events: Append-only group membership events

Constraints enforced:
- UNIQUE(session_id, seq) on session events
- UNIQUE(group_id, seq) on group events
- UNIQUE(session_id, sender_id, sender_seq) on frames (replay detection)
- CHECK for 64-char lowercase hex on all digest fields
- Cursor pagination indexes
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = '003_a2a_phase10'
down_revision: Union[str, None] = '002_a2a_tasks'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Digest CHECK constraint pattern (64 lowercase hex chars)
DIGEST_CHECK = "digest ~ '^[a-f0-9]{64}$'"


def upgrade() -> None:
    # ==========================================================================
    # A2A Sessions
    # ==========================================================================
    op.create_table(
        'a2a_sessions',
        sa.Column('session_id', sa.String(36), primary_key=True),
        sa.Column('state', sa.String(20), nullable=False),
        sa.Column('initiator_id', sa.String(255), nullable=False),
        sa.Column('responder_id', sa.String(255), nullable=False),
        sa.Column('ratchet_state_blob', sa.Text(), nullable=True),
        sa.Column('ratchet_state_digest', sa.String(64), nullable=True),
        sa.Column('expires_at', sa.DateTime(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.CheckConstraint(
            "state IN ('pending', 'active', 'closed')",
            name='check_session_state_enum'
        ),
        sa.CheckConstraint(
            "ratchet_state_digest IS NULL OR ratchet_state_digest ~ '^[a-f0-9]{64}$'",
            name='check_session_ratchet_digest_hex'
        ),
    )
    op.create_index('idx_a2a_sessions_initiator', 'a2a_sessions', ['initiator_id'])
    op.create_index('idx_a2a_sessions_responder', 'a2a_sessions', ['responder_id'])

    # ==========================================================================
    # A2A Session Events (Append-Only Hash Chain)
    # ==========================================================================
    op.create_table(
        'a2a_session_events',
        sa.Column('session_id', sa.String(36), nullable=False),
        sa.Column('seq', sa.Integer(), nullable=False),
        sa.Column('prev_digest', sa.String(64), nullable=True),
        sa.Column('digest', sa.String(64), nullable=False),
        sa.Column('event_json', sa.JSON(), nullable=False),
        sa.Column('ts', sa.DateTime(), nullable=True),
        sa.Column('actor_id', sa.String(255), nullable=False),
        sa.Column('target_id', sa.String(255), nullable=True),
        sa.PrimaryKeyConstraint('session_id', 'seq'),
        sa.CheckConstraint(
            "digest ~ '^[a-f0-9]{64}$'",
            name='check_session_event_digest_hex'
        ),
        sa.CheckConstraint(
            "prev_digest IS NULL OR prev_digest ~ '^[a-f0-9]{64}$'",
            name='check_session_event_prev_digest_hex'
        ),
    )
    # Explicit unique constraint for clarity (PK already enforces this)
    op.create_unique_constraint(
        'uq_session_event_seq', 'a2a_session_events', ['session_id', 'seq']
    )

    # ==========================================================================
    # A2A Frames (Encrypted Messages with Replay Detection)
    # ==========================================================================
    op.create_table(
        'a2a_frames',
        sa.Column('session_id', sa.String(36), nullable=False),
        sa.Column('sender_id', sa.String(255), nullable=False),
        sa.Column('sender_seq', sa.Integer(), nullable=False),
        sa.Column('recipient_id', sa.String(255), nullable=False),
        sa.Column('frame_digest', sa.String(64), nullable=False),
        sa.Column('ciphertext_hash', sa.String(64), nullable=False),
        sa.Column('header_b64u', sa.Text(), nullable=False),
        sa.Column('ciphertext_b64u', sa.Text(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('session_id', 'sender_id', 'sender_seq'),
        sa.CheckConstraint(
            "frame_digest ~ '^[a-f0-9]{64}$'",
            name='check_frame_digest_hex'
        ),
        sa.CheckConstraint(
            "ciphertext_hash ~ '^[a-f0-9]{64}$'",
            name='check_frame_ciphertext_hash_hex'
        ),
    )
    # Replay detection: unique constraint on (session_id, sender_id, sender_seq)
    op.create_unique_constraint(
        'uq_frame_replay', 'a2a_frames', ['session_id', 'sender_id', 'sender_seq']
    )
    # Cursor pagination index
    op.create_index(
        'idx_a2a_frames_created_at', 'a2a_frames',
        ['session_id', 'created_at', 'sender_id', 'sender_seq']
    )
    op.create_index('idx_a2a_frames_recipient', 'a2a_frames', ['recipient_id'])

    # ==========================================================================
    # A2A Groups
    # ==========================================================================
    op.create_table(
        'a2a_groups',
        sa.Column('group_id', sa.String(36), primary_key=True),
        sa.Column('owner_id', sa.String(255), nullable=False),
        sa.Column('state', sa.String(20), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.CheckConstraint(
            "state IN ('active', 'closed')",
            name='check_group_state_enum'
        ),
    )
    op.create_index('idx_a2a_groups_owner', 'a2a_groups', ['owner_id'])

    # ==========================================================================
    # A2A Group Events (Append-Only Hash Chain)
    # ==========================================================================
    op.create_table(
        'a2a_group_events',
        sa.Column('group_id', sa.String(36), nullable=False),
        sa.Column('seq', sa.Integer(), nullable=False),
        sa.Column('prev_digest', sa.String(64), nullable=True),
        sa.Column('digest', sa.String(64), nullable=False),
        sa.Column('event_json', sa.JSON(), nullable=False),
        sa.Column('ts', sa.DateTime(), nullable=True),
        sa.Column('actor_id', sa.String(255), nullable=False),
        sa.Column('target_id', sa.String(255), nullable=True),
        sa.PrimaryKeyConstraint('group_id', 'seq'),
        sa.CheckConstraint(
            "digest ~ '^[a-f0-9]{64}$'",
            name='check_group_event_digest_hex'
        ),
        sa.CheckConstraint(
            "prev_digest IS NULL OR prev_digest ~ '^[a-f0-9]{64}$'",
            name='check_group_event_prev_digest_hex'
        ),
    )
    # Explicit unique constraint
    op.create_unique_constraint(
        'uq_group_event_seq', 'a2a_group_events', ['group_id', 'seq']
    )


def downgrade() -> None:
    # Drop in reverse order
    op.drop_constraint('uq_group_event_seq', 'a2a_group_events', type_='unique')
    op.drop_table('a2a_group_events')

    op.drop_index('idx_a2a_groups_owner', table_name='a2a_groups')
    op.drop_table('a2a_groups')

    op.drop_index('idx_a2a_frames_recipient', table_name='a2a_frames')
    op.drop_index('idx_a2a_frames_created_at', table_name='a2a_frames')
    op.drop_constraint('uq_frame_replay', 'a2a_frames', type_='unique')
    op.drop_table('a2a_frames')

    op.drop_constraint('uq_session_event_seq', 'a2a_session_events', type_='unique')
    op.drop_table('a2a_session_events')

    op.drop_index('idx_a2a_sessions_responder', table_name='a2a_sessions')
    op.drop_index('idx_a2a_sessions_initiator', table_name='a2a_sessions')
    op.drop_table('a2a_sessions')
