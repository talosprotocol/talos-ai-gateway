"""add_secret_id_for_aad

Revision ID: b96a687eb62e
Revises: 003_a2a_phase10
Create Date: 2026-01-19 14:02:16.288621

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b96a687eb62e'
down_revision: Union[str, None] = '003_a2a_phase10'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


import uuid
from uuid6 import uuid7

def upgrade() -> None:
    # 1. Add id column as nullable first
    op.add_column('secrets', sa.Column('id', sa.String(36), nullable=True))
    
    # 2. Populate existing secrets with stable IDs
    bind = op.get_bind()
    # Batch select names
    res = bind.execute(sa.text("SELECT name FROM secrets"))
    for row in res:
        new_id = str(uuid7())
        # Update each row
        bind.execute(
            sa.text("UPDATE secrets SET id = :id WHERE name = :name"),
            {"id": new_id, "name": row[0]}
        )
    
    # 3. Make id NON NULL and re-setup PK
    op.alter_column('secrets', 'id', nullable=False)
    
    # Drop old constraint (primary key on name)
    # In Postgres, the default name is 'secrets_pkey'
    try:
        op.drop_constraint('secrets_pkey', 'secrets', type_='primary')
    except Exception:
        # Fallback for other DBs or different names
        pass
        
    op.create_primary_key('secrets_pkey', 'secrets', ['id'])
    op.create_unique_constraint('uq_secrets_name', 'secrets', ['name'])
    op.create_index('idx_secrets_name', 'secrets', ['name'])

def downgrade() -> None:
    op.drop_index('idx_secrets_name', table_name='secrets')
    op.drop_constraint('uq_secrets_name', 'secrets', type_='unique')
    op.drop_constraint('secrets_pkey', 'secrets', type_='primary')
    op.create_primary_key('secrets_pkey', 'secrets', ['name'])
    op.drop_column('secrets', 'id')
