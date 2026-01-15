"""Initial schema - Control plane tables

Revision ID: 001_initial
Revises: 
Create Date: 2026-01-10

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = '001_initial'
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Orgs
    op.create_table('orgs',
        sa.Column('id', sa.String(255), primary_key=True),
        sa.Column('name', sa.String(255), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
    )
    
    # Teams
    op.create_table('teams',
        sa.Column('id', sa.String(255), primary_key=True),
        sa.Column('org_id', sa.String(255), sa.ForeignKey('orgs.id'), nullable=False),
        sa.Column('name', sa.String(255), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
    )
    
    # Virtual Keys
    op.create_table('virtual_keys',
        sa.Column('id', sa.String(255), primary_key=True),
        sa.Column('team_id', sa.String(255), sa.ForeignKey('teams.id'), nullable=False),
        sa.Column('key_hash', sa.String(64), nullable=False, unique=True, index=True),
        sa.Column('scopes', sa.JSON(), nullable=True),
        sa.Column('allowed_model_groups', sa.JSON(), nullable=True),
        sa.Column('allowed_mcp_servers', sa.JSON(), nullable=True),
        sa.Column('rate_limits', sa.JSON(), nullable=True),
        sa.Column('budget', sa.JSON(), nullable=True),
        sa.Column('expires_at', sa.DateTime(), nullable=True),
        sa.Column('revoked', sa.Boolean(), default=False),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('last_used_at', sa.DateTime(), nullable=True),
    )
    
    # LLM Upstreams
    op.create_table('llm_upstreams',
        sa.Column('id', sa.String(255), primary_key=True),
        sa.Column('provider', sa.String(50), nullable=False),
        sa.Column('endpoint', sa.String(512), nullable=False),
        sa.Column('credentials_ref', sa.String(255), nullable=True),
        sa.Column('tags', sa.JSON(), nullable=True),
        sa.Column('enabled', sa.Boolean(), default=True),
        sa.Column('version', sa.Integer(), nullable=False, server_default='1'),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
    )
    
    # Model Groups
    op.create_table('model_groups',
        sa.Column('id', sa.String(255), primary_key=True),
        sa.Column('name', sa.String(255), nullable=False),
        sa.Column('deployments', sa.JSON(), nullable=True),
        sa.Column('fallback_groups', sa.JSON(), nullable=True),
        sa.Column('enabled', sa.Boolean(), default=True),
        sa.Column('version', sa.Integer(), nullable=False, server_default='1'),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
    )
    
    # Routing Policies (Composite PK id, version)
    op.create_table('routing_policies',
        sa.Column('policy_id', sa.String(255), nullable=False),
        sa.Column('version', sa.Integer(), nullable=False),
        sa.Column('strategy', sa.String(50), default='weighted_hash'),
        sa.Column('retries', sa.JSON(), nullable=True),
        sa.Column('timeout_ms', sa.Integer(), default=30000),
        sa.Column('cooldown', sa.JSON(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('policy_id', 'version')
    )
    
    # MCP Servers
    op.create_table('mcp_servers',
        sa.Column('id', sa.String(255), primary_key=True),
        sa.Column('name', sa.String(255), nullable=False),
        sa.Column('transport', sa.String(50), nullable=False),
        sa.Column('endpoint', sa.String(512), nullable=True),
        sa.Column('command', sa.String(512), nullable=True),
        sa.Column('args', sa.JSON(), nullable=True),
        sa.Column('env', sa.JSON(), nullable=True),
        sa.Column('tags', sa.JSON(), nullable=True),
        sa.Column('enabled', sa.Boolean(), default=True),
        sa.Column('version', sa.Integer(), nullable=False, server_default='1'),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
    )
    
    # MCP Policies
    op.create_table('mcp_policies',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('team_id', sa.String(255), sa.ForeignKey('teams.id'), nullable=False),
        sa.Column('allowed_servers', sa.JSON(), nullable=True),
        sa.Column('allowed_tools', sa.JSON(), nullable=True),
        sa.Column('deny_by_default', sa.Boolean(), default=True),
        sa.Column('version', sa.Integer(), nullable=False, server_default='1'),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
    )
    
    # Principals
    op.create_table('principals',
        sa.Column('id', sa.String(255), primary_key=True),
        sa.Column('type', sa.String(20), nullable=False),
        sa.Column('email', sa.String(255), unique=True, index=True),
        sa.Column('org_id', sa.String(255), sa.ForeignKey('orgs.id'), nullable=True),
        sa.Column('display_name', sa.String(255), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
    )
    
    # Roles
    op.create_table('roles',
        sa.Column('id', sa.String(255), primary_key=True),
        sa.Column('name', sa.String(100), nullable=False, unique=True),
        sa.Column('permissions', sa.JSON(), nullable=True),
        sa.Column('built_in', sa.Boolean(), default=False),
        sa.Column('created_at', sa.DateTime(), nullable=True),
    )
    
    # Role Bindings
    op.create_table('role_bindings',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('principal_id', sa.String(255), sa.ForeignKey('principals.id'), nullable=False),
        sa.Column('role_id', sa.String(255), sa.ForeignKey('roles.id'), nullable=False),
        sa.Column('scope_type', sa.String(20), nullable=False),
        sa.Column('scope_org_id', sa.String(255), nullable=True),
        sa.Column('scope_team_id', sa.String(255), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
    )
    
    # Usage Events
    op.create_table('usage_events',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('timestamp', sa.DateTime(), index=True),
        sa.Column('key_id', sa.String(255), index=True),
        sa.Column('team_id', sa.String(255), index=True),
        sa.Column('org_id', sa.String(255), index=True),
        sa.Column('surface', sa.String(10), nullable=True),
        sa.Column('target', sa.String(100), nullable=True),
        sa.Column('input_tokens', sa.Integer(), default=0),
        sa.Column('output_tokens', sa.Integer(), default=0),
        sa.Column('cost_usd', sa.Float(), default=0.0),
        sa.Column('latency_ms', sa.Integer(), default=0),
        sa.Column('status', sa.String(20), nullable=True),
    )

    # Secrets (name PK)
    op.create_table('secrets',
        sa.Column('name', sa.String(255), primary_key=True),
        sa.Column('encrypted_value', sa.Text(), nullable=False),
        sa.Column('version', sa.Integer(), nullable=False, server_default='1'),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
    )

    # Audit Events (event_id PK)
    op.create_table('audit_events',
        sa.Column('event_id', sa.String(36), primary_key=True),
        sa.Column('timestamp', sa.DateTime(), index=True),
        sa.Column('principal_id', sa.String(255), index=True),
        sa.Column('action', sa.String(50), nullable=False),
        sa.Column('resource_type', sa.String(50), nullable=False),
        sa.Column('resource_id', sa.String(255), nullable=True),
        sa.Column('request_id', sa.String(36), nullable=True),
        sa.Column('details', sa.JSON(), nullable=True),
        sa.Column('status', sa.String(20), nullable=True),
    )

    # Config Versions
    op.create_table('config_versions',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('version', sa.Integer(), nullable=False, unique=True),
        sa.Column('hash', sa.String(64), nullable=False),
        sa.Column('content', sa.JSON(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('applied_by', sa.String(255), nullable=True),
    )

    # Deployments
    op.create_table('deployments',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('model_group_id', sa.String(255), sa.ForeignKey('model_groups.id'), nullable=False),
        sa.Column('upstream_id', sa.String(255), sa.ForeignKey('llm_upstreams.id'), nullable=False),
        sa.Column('model_name', sa.String(255), nullable=False),
        sa.Column('weight', sa.Integer(), default=100),
    )

    # Indexes and Constraints
    op.create_index('idx_upstream_enabled', 'llm_upstreams', ['enabled'])
    op.create_index('idx_model_group_enabled', 'model_groups', ['enabled'])
    op.create_index('idx_mcp_server_enabled', 'mcp_servers', ['enabled'])
    op.create_index('idx_mcp_policy_team', 'mcp_policies', ['team_id'])
    op.create_index('idx_audit_timestamp', 'audit_events', [sa.text('timestamp DESC')])
    op.create_index('idx_audit_resource', 'audit_events', ['resource_type', 'resource_id'])
    op.create_unique_constraint('uq_deployment_target', 'deployments', ['model_group_id', 'upstream_id', 'model_name'])
    op.create_index('idx_deployment_model_group', 'deployments', ['model_group_id'])


def downgrade() -> None:
    op.drop_table('deployments')
    op.drop_table('config_versions')
    op.drop_table('audit_events')
    op.drop_table('secrets')
    op.drop_table('usage_events')
    op.drop_table('role_bindings')
    op.drop_table('roles')
    op.drop_table('principals')
    op.drop_table('mcp_policies')
    op.drop_table('mcp_servers')
    op.drop_table('routing_policies')
    op.drop_table('model_groups')
    op.drop_table('llm_upstreams')
    op.drop_table('virtual_keys')
    op.drop_table('teams')
    op.drop_table('orgs')
