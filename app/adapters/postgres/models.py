"""SQLAlchemy Models for Control Plane."""
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    Column,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    desc,
    func,
)
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    mapped_column,
    relationship,
)  # type: ignore
from sqlalchemy.dialects import postgresql


class Base(DeclarativeBase):
    pass


class Org(Base):
    """Organization entity."""
    __tablename__ = "orgs"
    id = Column(String(255), primary_key=True)
    name = Column(String(255), nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )
    teams = relationship("Team", back_populates="org")


class Team(Base):
    """Team entity."""
    __tablename__ = "teams"
    id = Column(String(255), primary_key=True)
    org_id = Column(String(255), ForeignKey("orgs.id"), nullable=False)
    name = Column(String(255), nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )
    # Phase 15: Budget & Metadata
    budget_mode = Column(String(20), default="off", nullable=False)
    overdraft_usd = Column(Numeric(18, 8), default=0, nullable=False)
    max_tokens_default = Column(Integer, nullable=True)
    budget = Column(JSON, default=dict)  # Config Metadata
    org = relationship("Org", back_populates="teams")
    keys = relationship("VirtualKey", back_populates="team")


class VirtualKey(Base):
    """Virtual Key entity (data plane auth)."""
    __tablename__ = "virtual_keys"
    id = Column(String(255), primary_key=True)
    team_id = Column(String(255), ForeignKey("teams.id"), nullable=False)
    key_hash = Column(String(128), nullable=False, unique=True, index=True)
    scopes = Column(JSON, default=list)
    allowed_model_groups = Column(JSON, default=list)
    allowed_mcp_servers = Column(JSON, default=list)
    rate_limits = Column(JSON, default=dict)
    # Phase 15: Budget
    budget_mode = Column(String(20), default="off", nullable=False)
    overdraft_usd = Column(Numeric(18, 8), default=0, nullable=False)
    max_tokens_default = Column(Integer, nullable=True)
    budget = Column(JSON, default=dict)  # Config Metadata
    expires_at = Column(DateTime, nullable=True)
    revoked = Column(Boolean, default=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    last_used_at = Column(DateTime, nullable=True)
    team = relationship("Team", back_populates="keys")


class LlmUpstream(Base):
    """LLM Upstream configuration."""
    __tablename__ = "llm_upstreams"
    id = Column(String(255), primary_key=True)  # Slug
    provider = Column(String(50), nullable=False)
    endpoint = Column(String(512), nullable=False)
    credentials_ref = Column(String(255))  # Encrypted reference
    tags = Column(JSON, default=dict)
    enabled = Column(Boolean, default=True)
    version = Column(Integer, default=1, nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    __table_args__ = (Index("idx_upstream_enabled", "enabled"),)


class ModelGroup(Base):
    """Model Group configuration."""
    __tablename__ = "model_groups"
    id = Column(String(255), primary_key=True)  # Slug
    name = Column(String(255), nullable=False)
    deployments = Column(JSON, default=list)  # Legacy cache
    fallback_groups = Column(JSON, default=list)
    enabled = Column(Boolean, default=True)
    version = Column(Integer, default=1, nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    deployment_rows = relationship(
        "Deployment",
        back_populates="model_group",
        cascade="all, delete-orphan",
    )

    __table_args__ = (Index("idx_model_group_enabled", "enabled"),)


class RoutingPolicy(Base):
    """Routing Policy (versioned, immutable per version)."""
    __tablename__ = "routing_policies"
    policy_id = Column(String(255), primary_key=True)
    version = Column(Integer, primary_key=True)
    strategy = Column(String(50), default="weighted_hash")
    retries = Column(JSON, default=dict)
    timeout_ms = Column(Integer, default=30000)
    cooldown = Column(JSON, default=dict)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    # Composite PK enforces uniqueness


class McpServer(Base):
    """MCP Server registry."""
    __tablename__ = "mcp_servers"
    id = Column(String(255), primary_key=True)  # Slug
    name = Column(String(255), nullable=False)
    transport = Column(String(50), nullable=False)  # stdio, http, talos_tunnel
    endpoint = Column(String(512))
    command = Column(String(512))
    args = Column(JSON, default=list)
    env = Column(JSON, default=dict)
    tags = Column(JSON, default=dict)
    enabled = Column(Boolean, default=True)
    version = Column(Integer, default=1, nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )
    __table_args__ = (Index("idx_mcp_server_enabled", "enabled"),)


class McpPolicy(Base):
    """MCP Team Policy (allowlists)."""
    __tablename__ = "mcp_policies"
    id = Column(String(36), primary_key=True)
    team_id = Column(String(255), ForeignKey("teams.id"), nullable=False)
    allowed_servers = Column(JSON, default=list)
    allowed_tools = Column(JSON, default=dict)
    deny_by_default = Column(Boolean, default=True)
    version = Column(Integer, default=1, nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )
    __table_args__ = (Index("idx_mcp_policy_team", "team_id"),)


class Principal(Base):
    """RBAC Principal (user or service account)."""
    __tablename__ = "principals"
    id = Column(String(255), primary_key=True)
    type = Column(String(20), nullable=False)  # user, service_account
    email = Column(String(255), unique=True, index=True)
    org_id = Column(String(255), ForeignKey("orgs.id"), nullable=True)
    display_name = Column(String(255))
    public_key = Column(
        String(64), nullable=True, index=True
    )  # Ed25519 public key (hex)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class Role(Base):
    """RBAC Role."""
    __tablename__ = "roles"
    id = Column(String(255), primary_key=True)
    name = Column(String(100), nullable=False, unique=True)
    permissions = Column(JSON, default=list)
    built_in = Column(Boolean, default=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class RoleBinding(Base):
    """RBAC Role Binding."""
    __tablename__ = "role_bindings"
    id = Column(String(36), primary_key=True)
    principal_id = Column(
        String(255), ForeignKey("principals.id"), nullable=False
    )
    role_id = Column(String(255), ForeignKey("roles.id"), nullable=False)
    scope_type = Column(String(20), nullable=False)  # platform, org, team
    scope_org_id = Column(String(255), nullable=True)
    scope_team_id = Column(String(255), nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class UsageEvent(Base):
    """Usage event for tracking consumption."""
    __tablename__ = "usage_events"
    id = Column(String(36), primary_key=True)
    request_id = Column(
        String(36), index=True, unique=True, nullable=True
    )  # Added Phase 15
    timestamp = Column(
        DateTime, default=lambda: datetime.now(timezone.utc), index=True
    )
    key_id = Column(String(255), index=True)
    team_id = Column(String(255), index=True)
    org_id = Column(String(255), index=True)
    surface = Column(String(10))  # llm, mcp
    target = Column(String(100))  # model_group_id or mcp_server_id
    input_tokens = Column(Integer, default=0)
    output_tokens = Column(Integer, default=0)
    cost_usd = Column(Numeric(18, 8), default=0.0)  # Phase 15: Numeric
    pricing_version = Column(String(36))
    token_count_source = Column(
        String(20)
    )  # provider_reported, estimated, unknown
    latency_ms = Column(Integer, default=0)
    status = Column(String(20))  # success, denied, error


class Secret(Base):
    """Secret storage with AES-GCM envelope encryption."""
    __tablename__ = "secrets"
    id = Column(String(36), primary_key=True)  # UUID7 stable identifier
    name = Column(String(255), nullable=False, unique=True, index=True)
    ciphertext = Column(Text, nullable=False)  # Base64URL-encoded (v1)
    nonce = Column(String(64), nullable=False)  # Base64URL-encoded (v1)
    tag = Column(String(64), nullable=False)    # Base64URL-encoded (v1)
    aad = Column(Text, nullable=True)           # Optional bound context (b64u)
    key_id = Column(
        String(64), nullable=False, index=True
    )  # KEK version identifier
    version = Column(Integer, default=1, nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    rotated_at = Column(DateTime, nullable=True)
    updated_at = Column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )


class RotationOperation(Base):
    """Tracks status of bulk secret rotation jobs."""
    __tablename__ = "rotation_operations"
    id = Column(String(36), primary_key=True)  # UUID7
    status = Column(String(20), nullable=False)  # running, completed, failed
    target_kek_id = Column(String(64), nullable=False)
    cursor = Column(
        String(255), nullable=True
    )  # Last successfully rotated secret name
    stats = Column(JSON, default=dict)          # {scanned, rotated, failed}
    started_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    completed_at = Column(DateTime, nullable=True)
    last_error = Column(Text, nullable=True)


class AuditEvent(Base):
    """Admin mutation audit log."""
    __tablename__ = "audit_events"
    
    event_id = Column(String(36), primary_key=True)
    timestamp = Column(
        DateTime, default=lambda: datetime.now(timezone.utc), index=True
    )
    principal_id = Column(String(255), index=True)
    action = Column(String(50), nullable=False)  # create, update, delete
    resource_type = Column(String(50), nullable=False)
    resource_id = Column(String(255))
    request_id = Column(
        String(36), index=True, unique=True
    )  # Phase 15: Unique
    schema_id = Column(String(100), default="talos.audit.v1")
    schema_version = Column(Integer, default=1)
    details = Column(JSON, default=dict)
    status = Column(String(20))  # success, error
    event_hash = Column(String(64), index=True)  # Deterministic SHA-256

    __table_args__ = (
        Index("idx_audit_timestamp", desc("timestamp")),
        Index("idx_audit_resource", "resource_type", "resource_id"),
    )


class Deployment(Base):
    """Model Group Deployment."""
    __tablename__ = "deployments"
    
    id = Column(String(36), primary_key=True)
    model_group_id = Column(
        String(255), ForeignKey("model_groups.id"), nullable=False
    )
    upstream_id = Column(
        String(255), ForeignKey("llm_upstreams.id"), nullable=False
    )
    model_name = Column(String(255), nullable=False)
    weight = Column(Integer, default=100)
    
    model_group = relationship("ModelGroup", back_populates="deployment_rows")
    upstream = relationship("LlmUpstream")

    __table_args__ = (
        UniqueConstraint(
            "model_group_id",
            "upstream_id",
            "model_name",
            name="uq_deployment_target",
        ),
        Index("idx_deployment_model_group", "model_group_id"),
    )


class ConfigVersion(Base):
    """Configuration snapshot versioning."""
    __tablename__ = "config_versions"
    
    id = Column(String(36), primary_key=True)
    version = Column(Integer, nullable=False, unique=True)
    hash = Column(String(64), nullable=False)
    content = Column(JSON, nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    applied_by = Column(String(255))


class A2ATask(Base):
    """A2A Task Persistence."""
    __tablename__ = "a2a_tasks"
    id = Column(String(64), primary_key=True)  # Text ID
    # Tenancy & Origin
    team_id = Column(
        String(255), ForeignKey("teams.id"), nullable=False, index=True
    )
    key_id = Column(
        String(255), ForeignKey("virtual_keys.id"), nullable=False, index=True
    )
    org_id = Column(String(255), index=True)  # Optional, denormalized

    # Metadata
    request_id = Column(
        String(64), index=True
    )  # Indexed, NOT unique globally
    origin_surface = Column(String(20), default="a2a")
    method = Column(String(50))  # tasks.send, etc.

    # State
    # Status: queued, running, completed, failed, canceled
    status = Column(
        String(20), nullable=False, default="queued"
    )
    version = Column(Integer, default=1, nullable=False)  # Optimistic locking

    # Data (Privacy Preserving)
    # Safe metadata ONLY (method, tool_name, model, profile_ver)
    request_meta = Column(JSON, default=dict)
    # Redacted input if configured
    input_redacted = Column(JSON, nullable=True)
    
    # Results
    result = Column(JSON, nullable=True)  # The 'task' object
    error = Column(JSON, nullable=True)  # Error details
    
    created_at = Column(
        DateTime, default=lambda: datetime.now(timezone.utc), index=True
    )
    updated_at = Column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('queued','running','completed','failed','canceled')",
            name="check_a2a_status_enum",
        ),
    )


Index("idx_a2a_tasks_team_created", A2ATask.team_id, A2ATask.created_at.desc())


class A2ASession(Base):
    __tablename__ = "a2a_sessions"
    
    session_id = Column(String(36), primary_key=True)
    state = Column(String(20), nullable=False)  # pending, active, closed
    initiator_id = Column(String(255), nullable=False)
    responder_id = Column(String(255), nullable=False)
    ratchet_state_blob = Column(Text, nullable=True)  # base64url
    ratchet_state_digest = Column(String(64), nullable=True)
    expires_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )


class A2ASessionEvent(Base):
    __tablename__ = "a2a_session_events"
    
    session_id = Column(String(36), primary_key=True)
    seq = Column(Integer, primary_key=True)
    prev_digest = Column(String(64), nullable=True)
    digest = Column(String(64), nullable=False)
    event_json = Column(JSON, nullable=False)
    ts = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    actor_id = Column(String(255), nullable=False)
    target_id = Column(String(255), nullable=True)
    
    __table_args__ = (
        UniqueConstraint("session_id", "seq", name="uq_session_event_seq"),
    )


class A2AFrame(Base):
    __tablename__ = "a2a_frames"
    
    session_id = Column(String(36), primary_key=True)
    sender_id = Column(String(255), primary_key=True)
    sender_seq = Column(Integer, primary_key=True)
    
    recipient_id = Column(String(255), nullable=False, index=True)
    frame_digest = Column(String(64), nullable=False)
    ciphertext_hash = Column(String(64), nullable=False)
    header_b64u = Column(Text, nullable=False)
    ciphertext_b64u = Column(Text, nullable=False)
    created_at = Column(
        DateTime, default=lambda: datetime.now(timezone.utc), index=True
    )


class A2AGroup(Base):
    __tablename__ = "a2a_groups"
    
    group_id = Column(String(36), primary_key=True)
    owner_id = Column(String(255), nullable=False)
    state = Column(String(20), nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class A2AGroupEvent(Base):
    __tablename__ = "a2a_group_events"
    
    group_id = Column(String(36), primary_key=True)
    seq = Column(Integer, primary_key=True)
    prev_digest = Column(String(64), nullable=True)
    digest = Column(String(64), nullable=False)
    event_json = Column(JSON, nullable=False)
    ts = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    actor_id = Column(String(255), nullable=False)
    target_id = Column(String(255), nullable=True)
    
    __table_args__ = (
        UniqueConstraint("group_id", "seq", name="uq_group_event_seq"),
    )


class SecretsKeyring(Base):
    """Key Encryption Key (KEK) Registry for Secrets."""
    __tablename__ = "secrets_keyring"
    # Typically "master" or tenant_id
    id = Column(String(36), primary_key=True)
    active_kek_id = Column(String(64), nullable=False)
    version = Column(Integer, nullable=False, default=1)
    updated_at = Column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )
    
    # but usually this table WOULD contain the actual KEK (wrapped).
    # Since LocalKekProvider uses env var MASTER_KEY, we will just track
    # metadata here.


# Phase 15: Budget Ledger Models

class BudgetScope(Base):
    """Budget Scope Ledger (Team or VirtualKey)."""
    __tablename__ = "budget_scopes"
    
    id = Column(String(36), primary_key=True)
    scope_type = Column(String(20), nullable=False)  # team, virtual_key
    scope_id = Column(String(255), nullable=False)
    period_start = Column(Date, nullable=False)
    
    limit_usd = Column(Numeric(18, 8), default=0, nullable=False)
    used_usd = Column(Numeric(18, 8), default=0, nullable=False)
    reserved_usd = Column(Numeric(18, 8), default=0, nullable=False)
    overdraft_usd = Column(Numeric(18, 8), default=0, nullable=False)
    last_alert_at = Column(DateTime, nullable=True)
    
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    __table_args__ = (
        UniqueConstraint(
            "scope_type",
            "scope_id",
            "period_start",
            name="uq_budget_scope_period",
        ),
        CheckConstraint("reserved_usd >= 0", name="check_reserved_usd_pos"),
        CheckConstraint("used_usd >= 0", name="check_used_usd_pos"),
        CheckConstraint("limit_usd >= 0", name="check_limit_usd_pos"),
        CheckConstraint("overdraft_usd >= 0", name="check_overdraft_usd_pos"),
        Index("idx_budget_scope_lookup", "scope_type", "scope_id"),
    )


class BudgetReservation(Base):
    """Active budget reservation."""
    __tablename__ = "budget_reservations"
    
    id = Column(String(36), primary_key=True)
    request_id = Column(String(36), unique=True, nullable=False)
    scope_team_id = Column(String(255), nullable=False)
    scope_key_id = Column(String(255), nullable=False)
    reserved_usd = Column(Numeric(18, 8), nullable=False)
    status = Column(
        String(20), nullable=False
    )  # ACTIVE, SETTLED, RELEASED, EXPIRED
    expires_at = Column(DateTime, nullable=False, index=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        CheckConstraint(
            "status IN ('ACTIVE', 'SETTLED', 'RELEASED', 'EXPIRED')",
            name="check_reservation_status_enum"
        ),
    )


class UsageRollupDaily(Base):
    """Daily usage rollup for reporting and cache warming."""
    __tablename__ = "usage_rollups_daily"
    
    id = Column(String(36), primary_key=True)
    day = Column(Date, nullable=False)
    team_id = Column(String(255), nullable=False, index=True)
    key_id = Column(String(255), nullable=False, index=True)
    
    used_usd = Column(Numeric(18, 8), default=0, nullable=False)
    input_tokens = Column(Integer, default=0, nullable=False)
    output_tokens = Column(Integer, default=0, nullable=False)
    request_count = Column(Integer, default=0, nullable=False)
    
    updated_at = Column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    __table_args__ = (
        UniqueConstraint(
            "day", "team_id", "key_id", name="uq_usage_rollup_day"
        ),
    )


class TgaTrace(Base):
    """TGA Execution Trace State."""
    __tablename__ = "tga_traces"
    
    trace_id: Mapped[str] = mapped_column(String, primary_key=True)
    schema_id: Mapped[str] = mapped_column(String)
    schema_version: Mapped[str] = mapped_column(String)
    plan_id: Mapped[str] = mapped_column(String)
    # ExecutionStateEnum
    current_state: Mapped[str] = mapped_column(String)
    last_sequence_number: Mapped[int] = mapped_column(Integer)
    last_entry_digest: Mapped[str] = mapped_column(String)
    state_digest: Mapped[str] = mapped_column(String)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        # pylint: disable=not-callable
        server_default=func.now(),  # type: ignore
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        # pylint: disable=not-callable
        server_default=func.now(),  # type: ignore
        # pylint: disable=not-callable
        onupdate=func.now(),  # type: ignore
        nullable=False,
    )


class TgaLog(Base):
    """TGA Execution Log Entry."""
    __tablename__ = "tga_logs"
    
    trace_id: Mapped[str] = mapped_column(
        String, ForeignKey("tga_traces.trace_id"), primary_key=True
    )
    sequence_number: Mapped[int] = mapped_column(Integer, primary_key=True)
    entry_digest: Mapped[str] = mapped_column(String, nullable=False)
    prev_entry_digest: Mapped[str] = mapped_column(String, nullable=False)
    ts: Mapped[str] = mapped_column(String, nullable=False)
    from_state: Mapped[str] = mapped_column(String, nullable=False)
    to_state: Mapped[str] = mapped_column(String, nullable=False)
    artifact_type: Mapped[str] = mapped_column(String, nullable=False)
    artifact_id: Mapped[str] = mapped_column(String, nullable=False)
    artifact_digest: Mapped[str] = mapped_column(String, nullable=False)
    tool_call_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    idempotency_key: Mapped[Optional[str]] = mapped_column(
        String, nullable=True
    )
    artifact_payload: Mapped[Optional[dict[str, Any]]] = mapped_column(
        postgresql.JSONB, nullable=True
    )
    schema_id: Mapped[str] = mapped_column(String, nullable=False)
    schema_version: Mapped[str] = mapped_column(String, nullable=False)
