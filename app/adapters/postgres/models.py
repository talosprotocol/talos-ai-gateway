"""SQLAlchemy Models for Control Plane."""
from sqlalchemy import Column, String, Boolean, Integer, Float, DateTime, ForeignKey, JSON, Text
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import relationship
from datetime import datetime

Base = declarative_base()


class Org(Base):
    """Organization entity."""
    __tablename__ = "orgs"
    
    id = Column(String(36), primary_key=True)
    name = Column(String(255), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    teams = relationship("Team", back_populates="org")


class Team(Base):
    """Team entity."""
    __tablename__ = "teams"
    
    id = Column(String(36), primary_key=True)
    org_id = Column(String(36), ForeignKey("orgs.id"), nullable=False)
    name = Column(String(255), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    org = relationship("Org", back_populates="teams")
    keys = relationship("VirtualKey", back_populates="team")


class VirtualKey(Base):
    """Virtual Key entity (data plane auth)."""
    __tablename__ = "virtual_keys"
    
    id = Column(String(36), primary_key=True)
    team_id = Column(String(36), ForeignKey("teams.id"), nullable=False)
    key_hash = Column(String(64), nullable=False, unique=True, index=True)
    scopes = Column(JSON, default=list)
    allowed_model_groups = Column(JSON, default=list)
    allowed_mcp_servers = Column(JSON, default=list)
    rate_limits = Column(JSON, default=dict)
    budget = Column(JSON, default=dict)
    expires_at = Column(DateTime, nullable=True)
    revoked = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    last_used_at = Column(DateTime, nullable=True)
    
    team = relationship("Team", back_populates="keys")


class LlmUpstream(Base):
    """LLM Upstream configuration."""
    __tablename__ = "llm_upstreams"
    
    id = Column(String(36), primary_key=True)
    provider = Column(String(50), nullable=False)
    endpoint = Column(String(512), nullable=False)
    credentials_ref = Column(String(255))  # Encrypted reference
    tags = Column(JSON, default=dict)
    enabled = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class ModelGroup(Base):
    """Model Group configuration."""
    __tablename__ = "model_groups"
    
    id = Column(String(36), primary_key=True)
    name = Column(String(255), nullable=False)
    deployments = Column(JSON, default=list)  # [{upstream_id, model_name, weight}]
    fallback_groups = Column(JSON, default=list)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class RoutingPolicy(Base):
    """Routing Policy (versioned, immutable per version)."""
    __tablename__ = "routing_policies"
    
    id = Column(String(36), primary_key=True)
    version = Column(Integer, nullable=False)
    strategy = Column(String(50), default="weighted_hash")
    retries = Column(JSON, default=dict)
    timeout_ms = Column(Integer, default=30000)
    cooldown = Column(JSON, default=dict)
    created_at = Column(DateTime, default=datetime.utcnow)
    
    __table_args__ = (
        # Unique constraint on id + version
    )


class McpServer(Base):
    """MCP Server registry."""
    __tablename__ = "mcp_servers"
    
    id = Column(String(36), primary_key=True)
    name = Column(String(255), nullable=False)
    transport = Column(String(50), nullable=False)  # stdio, http, talos_tunnel
    endpoint = Column(String(512))
    command = Column(String(512))
    args = Column(JSON, default=list)
    env = Column(JSON, default=dict)
    tags = Column(JSON, default=dict)
    enabled = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class McpPolicy(Base):
    """MCP Team Policy (allowlists)."""
    __tablename__ = "mcp_policies"
    
    id = Column(String(36), primary_key=True)
    team_id = Column(String(36), ForeignKey("teams.id"), nullable=False)
    allowed_servers = Column(JSON, default=list)
    allowed_tools = Column(JSON, default=dict)
    deny_by_default = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class Principal(Base):
    """RBAC Principal (user or service account)."""
    __tablename__ = "principals"
    
    id = Column(String(36), primary_key=True)
    type = Column(String(20), nullable=False)  # user, service_account
    email = Column(String(255), unique=True, index=True)
    org_id = Column(String(36), ForeignKey("orgs.id"), nullable=True)
    display_name = Column(String(255))
    created_at = Column(DateTime, default=datetime.utcnow)


class Role(Base):
    """RBAC Role."""
    __tablename__ = "roles"
    
    id = Column(String(36), primary_key=True)
    name = Column(String(100), nullable=False, unique=True)
    permissions = Column(JSON, default=list)
    built_in = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)


class RoleBinding(Base):
    """RBAC Role Binding."""
    __tablename__ = "role_bindings"
    
    id = Column(String(36), primary_key=True)
    principal_id = Column(String(36), ForeignKey("principals.id"), nullable=False)
    role_id = Column(String(36), ForeignKey("roles.id"), nullable=False)
    scope_type = Column(String(20), nullable=False)  # platform, org, team
    scope_org_id = Column(String(36), nullable=True)
    scope_team_id = Column(String(36), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class UsageEvent(Base):
    """Usage event for tracking consumption."""
    __tablename__ = "usage_events"
    
    id = Column(String(36), primary_key=True)
    timestamp = Column(DateTime, default=datetime.utcnow, index=True)
    key_id = Column(String(36), index=True)
    team_id = Column(String(36), index=True)
    org_id = Column(String(36), index=True)
    surface = Column(String(10))  # llm, mcp
    target = Column(String(100))  # model_group_id or mcp_server_id
    input_tokens = Column(Integer, default=0)
    output_tokens = Column(Integer, default=0)
    cost_usd = Column(Float, default=0.0)
    latency_ms = Column(Integer, default=0)
    status = Column(String(20))  # success, denied, error
