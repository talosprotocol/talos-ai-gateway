"""Dependency Injection Module."""
import os
import logging
import time
from typing import Optional, Generator

from fastapi import Depends, Request, Response
from sqlalchemy.orm import Session
from sqlalchemy import create_engine

from app.core.config import settings

logger = logging.getLogger(__name__)

# --- Database Connection Logic (Phase 12) ---

# We use create_engine for global pool management
_write_engine = create_engine(str(settings.DATABASE_WRITE_URL), pool_pre_ping=True, pool_size=10)
_read_engine = None

if settings.DATABASE_READ_URL and settings.DATABASE_READ_URL != settings.DATABASE_WRITE_URL:
    try:
        _read_engine = create_engine(str(settings.DATABASE_READ_URL), pool_pre_ping=True, pool_size=10)
        logger.info(f"Initialized Read DB: {settings.DATABASE_READ_URL}")
    except Exception as e:
        logger.error(f"Failed to initialize Read DB engine: {e}")
else:
    _read_engine = _write_engine # Default to write (Primary)

# Helper for standard get_db (Deprecated/Legacy)
# We map get_db to get_write_db for backward compatibility with older stores
def get_db(request: Request = None) -> Generator[Session, None, None]:
    """Legacy dependency yielding a Write DB session."""
    with Session(_write_engine) as session:
        yield session

# Phase 12: Split Dependencies
def get_write_db() -> Generator[Optional[Session], None, None]:
    """Yields a session strictly from the Primary Write DB."""
    try:
        with Session(_write_engine) as session:
            yield session
    except Exception as e:
        if not (settings.MODE == "dev" or os.getenv("DEV_MODE", "false").lower() == "true"):
            logger.error(f"Failed to connect to Primary DB: {e}")
            raise
        logger.warning(f"DB not available in DEV_MODE, yielding None: {e}")
        yield None

def get_read_db(response: Response) -> Generator[Session, None, None]:
    """Yields a session from Read DB (Replica), falling back to Write DB if needed.
    
    Normative Behavior:
    - Attempts Read DB first.
    - If configured and reachable, stamp 'X-Talos-Read-Source: replica'.
    - If Unreachable & Fallback Enabled -> 'X-Talos-Read-Source: primary_fallback'.
    - If Same as Write -> 'X-Talos-Read-Source: primary'.
    """
    
    # Check if Read is distinct
    if _read_engine == _write_engine:
        response.headers["X-Talos-Read-Source"] = "primary"
        with Session(_write_engine) as session:
            yield session
        return

    # Attempt Replica
    try:
        # Pinging logic or just try connect
        # NOTE: SQLAlchemy engine is lazy. We must try to connect to know.
        conn = _read_engine.connect()
        conn.close()
        
        response.headers["X-Talos-Read-Source"] = "replica"
        with Session(_read_engine) as session:
            yield session
            
    except Exception as e:
        if settings.READ_FALLBACK_ENABLED:
            logger.warning(f"Read DB unreachable, falling back to Primary. Error: {e}")
            response.headers["X-Talos-Read-Source"] = "primary_fallback"
            
            # Metric increment logic here (mock)
            # metrics.inc("read_db_fallback")
            
            with Session(_write_engine) as session:
                yield session
        else:
            logger.error("Read DB unreachable and Fallback DISABLED.")
            raise e

# --- Original Dependencies (Updated mappings) ---

from app.domain.interfaces import UpstreamStore, ModelGroupStore, SecretStore, McpStore, AuditStore, RoutingPolicyStore
from app.adapters.json_store.stores import (
    UpstreamJsonStore, ModelGroupJsonStore, SecretJsonStore, McpJsonStore, AuditJsonStore, RoutingPolicyJsonStore
)
from app.adapters.postgres.stores import (
    PostgresUpstreamStore, PostgresModelGroupStore, PostgresMcpStore, PostgresAuditStore, PostgresRoutingPolicyStore
)

DEV_MODE = settings.MODE == "dev"

def get_upstream_store(db: Session = Depends(get_write_db)) -> UpstreamStore:
    if DEV_MODE: return UpstreamJsonStore()
    return PostgresUpstreamStore(db)

def get_model_group_store(db: Session = Depends(get_write_db)) -> ModelGroupStore:
    if DEV_MODE: return ModelGroupJsonStore()
    return PostgresModelGroupStore(db)

def get_routing_policy_store(db: Session = Depends(get_write_db)) -> RoutingPolicyStore:
    if DEV_MODE: return RoutingPolicyJsonStore()
    return PostgresRoutingPolicyStore(db)

from app.adapters.secrets.local_provider import LocalKekProvider
from app.adapters.postgres.secret_store import PostgresSecretStore
from app.domain.secrets.ports import KekProvider, SecretStore

def get_kek_provider() -> KekProvider:
    """Factory for KEK provider."""
    master_key = os.getenv("TALOS_MASTER_KEY") or settings.MASTER_KEY
    key_id = os.getenv("TALOS_KEK_ID", "v1")
    return LocalKekProvider(master_key, key_id)

def get_secret_store(
    db: Session = Depends(get_write_db),
    kek: KekProvider = Depends(get_kek_provider)
) -> SecretStore:
    if DEV_MODE: return SecretJsonStore()
    return PostgresSecretStore(db, kek)

def get_read_secret_store(
    db: Session = Depends(get_read_db),
    kek: KekProvider = Depends(get_kek_provider)
) -> SecretStore:
    if DEV_MODE: return SecretJsonStore()
    return PostgresSecretStore(db, kek)

from app.domain.secrets.manager import SecretsManager
def get_secrets_manager(
    store: SecretStore = Depends(get_secret_store),
    kek: KekProvider = Depends(get_kek_provider)
) -> SecretsManager:
    return SecretsManager(kek, store)

def get_mcp_store(db: Session = Depends(get_write_db)) -> McpStore:
    if DEV_MODE: return McpJsonStore()
    return PostgresMcpStore(db)

def get_read_mcp_store(db: Session = Depends(get_read_db)) -> McpStore:
    if DEV_MODE: return McpJsonStore()
    return PostgresMcpStore(db)

def get_audit_store(db: Session = Depends(get_write_db)) -> AuditStore:
    if DEV_MODE: return AuditJsonStore()
    return PostgresAuditStore(db)

def get_read_audit_store(db: Session = Depends(get_read_db)) -> AuditStore:
    if DEV_MODE: return AuditJsonStore()
    return PostgresAuditStore(db)

from app.domain.health import get_health_state
from app.domain.routing import RoutingService
from app.domain.interfaces import UsageStore, RateLimitStore
from app.adapters.postgres.stores import PostgresUsageStore
from app.adapters.json_store.stores import UsageJsonStore

def get_usage_store(db: Session = Depends(get_write_db)) -> UsageStore:
    if DEV_MODE: return UsageJsonStore()
    return PostgresUsageStore(db)

def get_read_usage_store(db: Session = Depends(get_read_db)) -> UsageStore:
    if DEV_MODE: return UsageJsonStore()
    return PostgresUsageStore(db)

# ... Rate Limit / MCP Client (Unchanged) ...
from app.core.rate_limiter import RateLimiter, MemoryRateLimitStorage, RedisRateLimitStorage


def get_routing_service(
    u_store: UpstreamStore = Depends(get_upstream_store),
    mg_store: ModelGroupStore = Depends(get_model_group_store),
    rp_store: RoutingPolicyStore = Depends(get_routing_policy_store)
) -> RoutingService:
    return RoutingService(u_store, mg_store, rp_store, get_health_state())

_rate_limiter_instance: Optional[RateLimiter] = None
async def get_rate_limiter() -> RateLimiter:
    global _rate_limiter_instance
    if _rate_limiter_instance is None:
        if os.getenv("REDIS_URL"):
            # Import here to avoid circular
            from app.adapters.redis.client import get_redis_client
            redis_client = await get_redis_client()
            storage = RedisRateLimitStorage(redis_client)
        else:
            storage = MemoryRateLimitStorage()
        _rate_limiter_instance = RateLimiter(storage)
    return _rate_limiter_instance

def get_rate_limit_store() -> RateLimitStore:
    from app.adapters.memory_store.stores import MemoryRateLimitStore
    from app.adapters.redis.stores import RedisRateLimitStore
    if os.getenv("REDIS_URL"): return RedisRateLimitStore()
    return MemoryRateLimitStore()

from app.adapters.mcp.client import McpClient
def get_mcp_client() -> McpClient: return McpClient()

from app.domain.interfaces import SessionStore
from app.adapters.redis.stores import RedisSessionStore
from app.adapters.memory_store.stores import MemorySessionStore
def get_session_store() -> SessionStore:
    if os.getenv("REDIS_URL"): return RedisSessionStore()
    return MemorySessionStore()

from app.domain.interfaces import TaskStore
from app.adapters.postgres.task_store import PostgresTaskStore
from app.adapters.memory_store.stores import MemoryTaskStore
def get_task_store(db: Session = Depends(get_write_db)) -> TaskStore:
    if DEV_MODE: return MemoryTaskStore()
    return PostgresTaskStore(db)

# --- A2A Session Management (Split DB) ---

from app.domain.a2a.session_manager import A2ASessionManager
from app.domain.a2a.frame_store import A2AFrameStore
from app.domain.a2a.group_manager import A2AGroupManager

def get_a2a_session_manager(
    write_db: Session = Depends(get_write_db),
    read_db: Session = Depends(get_read_db)
) -> A2ASessionManager:
    return A2ASessionManager(write_db, read_db) # Updated Signature!

def get_a2a_frame_store(
    write_db: Session = Depends(get_write_db),
    read_db: Session = Depends(get_read_db)
) -> A2AFrameStore:
    return A2AFrameStore(write_db, read_db) # Updated Signature!

def get_a2a_group_manager(
    write_db: Session = Depends(get_write_db),
    read_db: Session = Depends(get_read_db)
) -> A2AGroupManager:
    return A2AGroupManager(write_db, read_db)

# --- Other deps ---
from app.adapters.postgres.key_store import get_key_store as get_ks_factory, KeyStore
from app.adapters.redis.client import get_redis_client
from app.domain.tga.validator import CapabilityValidator

async def get_key_store(db: Session = Depends(get_write_db)) -> KeyStore:
    redis_client = await get_redis_client()
    return get_ks_factory(db, redis_client=redis_client)

from app.domain.interfaces import PrincipalStore
from app.adapters.postgres.stores import PostgresPrincipalStore
class MockPrincipalStore(PrincipalStore):
    def get_principal(self, pid): return None

def get_principal_store(db: Session = Depends(get_write_db)) -> PrincipalStore:
    if DEV_MODE: return MockPrincipalStore()
    return PostgresPrincipalStore(db)

from app.middleware.attestation_http import AttestationVerifier, RedisReplayDetector
async def get_attestation_verifier(p_store: PrincipalStore = Depends(get_principal_store)) -> AttestationVerifier:
    redis_client = await get_redis_client()
    replay = RedisReplayDetector(redis_client)
    return AttestationVerifier(p_store, replay)

from app.domain.registry import SurfaceRegistry
_registry_instance = None
def get_surface_registry() -> SurfaceRegistry:
    global _registry_instance
    if _registry_instance is None:
        path = os.getenv("SURFACE_INVENTORY_PATH", "gateway_surface.json")
        _registry_instance = SurfaceRegistry(path)
    return _registry_instance

from app.domain.audit import AuditLogger
from app.domain.sink import AuditSink, StdOutSink, HttpSink
_audit_logger_instance = None
def get_audit_logger() -> AuditLogger:
    global _audit_logger_instance
    if _audit_logger_instance is None:
        sink_url = os.getenv("AUDIT_SINK_URL")
        sink: AuditSink = HttpSink(sink_url, os.getenv("AUDIT_SINK_API_KEY")) if sink_url else StdOutSink()
        _audit_logger_instance = AuditLogger(sink)
    return _audit_logger_instance

from app.policy import PolicyEngine, DeterministicPolicyEngine
_policy_engine_instance = None
def get_policy_engine() -> PolicyEngine:
    global _policy_engine_instance
    if _policy_engine_instance is None:
         # Simplified for brevity, same as legacy
         roles = {"role-admin": {"id": "role-admin", "permissions": ["*:*"]}, "role-public": {"id": "role-public", "permissions": ["public:*"]}}
         _policy_engine_instance = DeterministicPolicyEngine(roles, {})
    return _policy_engine_instance

def get_capability_validator() -> CapabilityValidator:
    key = settings.TGA_SUPERVISOR_PUBLIC_KEY
    if not key: return CapabilityValidator(supervisor_public_key="dev-placeholder")
    return CapabilityValidator(supervisor_public_key=key)
