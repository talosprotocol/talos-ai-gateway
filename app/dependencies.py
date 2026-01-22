"""Dependency Injection Module."""
import os
import logging
import time
import threading
from typing import Optional, Generator
from dataclasses import dataclass, field

from fastapi import Depends, Request, Response, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError, ProgrammingError

from app.core.config import settings

logger = logging.getLogger(__name__)

# --- Phase 12: Multi-Region DB Routing (Hardened) ---

# Engine creation with proper timeouts
_connect_args = {
    "connect_timeout": getattr(settings, "DATABASE_CONNECT_TIMEOUT_MS", 3000) // 1000,
    "options": f"-c statement_timeout={getattr(settings, 'DATABASE_READ_TIMEOUT_MS', 1000)}"
}

_write_engine = create_engine(
    str(settings.DATABASE_WRITE_URL), 
    pool_pre_ping=True, 
    pool_size=10,
    pool_timeout=5
)

_read_engine = None
_read_engine_is_distinct = False

if settings.DATABASE_READ_URL and str(settings.DATABASE_READ_URL) != str(settings.DATABASE_WRITE_URL):
    try:
        _read_engine = create_engine(
            str(settings.DATABASE_READ_URL), 
            pool_pre_ping=True, 
            pool_size=10,
            pool_timeout=5,
            connect_args=_connect_args
        )
        _read_engine_is_distinct = True
        logger.info(f"Initialized distinct Read DB: {settings.DATABASE_READ_URL}")
    except Exception as e:
        logger.error(f"Failed to initialize Read DB engine: {e}")
        _read_engine = _write_engine
else:
    _read_engine = _write_engine


# --- Circuit Breaker State (Concurrency-Safe) ---

@dataclass
class CircuitBreakerState:
    """Thread-safe circuit breaker state."""
    failures: int = 0
    circuit_open_until: float = 0.0
    lock: threading.Lock = field(default_factory=threading.Lock)
    
    failure_threshold: int = field(default_factory=lambda: getattr(settings, "READ_FAILURE_THRESHOLD", 3))
    open_duration: float = field(default_factory=lambda: getattr(settings, "CIRCUIT_OPEN_DURATION_SECONDS", 30.0))

    def record_failure(self) -> bool:
        """Record a failure, return True if circuit just opened."""
        with self.lock:
            self.failures += 1
            if self.failures >= self.failure_threshold:
                self.circuit_open_until = time.monotonic() + self.open_duration
                logger.warning(f"Circuit breaker OPEN until {self.open_duration}s from now")
                return True
            return False
    
    def record_success(self):
        """Record success, reset failure count."""
        with self.lock:
            self.failures = 0
    
    def is_open(self) -> bool:
        """Check if circuit is currently open."""
        with self.lock:
            if self.circuit_open_until > 0 and time.monotonic() < self.circuit_open_until:
                return True
            # Circuit closed, reset
            if self.circuit_open_until > 0 and time.monotonic() >= self.circuit_open_until:
                self.circuit_open_until = 0
                self.failures = 0
            return False

# Module-level singleton (will be attached to app.state in lifespan for multi-worker safety)
_circuit_breaker = CircuitBreakerState()


# --- Allowlist for Replica Reads ---
# Only these dependency functions are permitted to use replica reads.
# Any endpoint not using these explicitly will use primary by default.
REPLICA_READ_ALLOWLIST = frozenset([
    "get_read_mcp_store",
    "get_read_usage_store", 
    "get_read_audit_store",
    # Health readiness check - safe for replica
    "readiness",
])


# --- Backward Compatibility Aliases ---
# These exports allow existing code to import from dependencies without changes
from sqlalchemy.orm import sessionmaker

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=_write_engine)
engine = _write_engine  # Alias for backward compatibility


# --- Database Dependencies ---

def get_db(request: Request = None) -> Generator[Session, None, None]:
    """Legacy dependency yielding a Write DB session."""
    with Session(_write_engine) as session:
        yield session


def get_write_db() -> Generator[Optional[Session], None, None]:
    """Yields a session strictly from the Primary Write DB."""
    session = None
    try:
        session = Session(_write_engine)
        yield session
    except Exception as e:
        # If this is a logic error thrown FROM the endpoint, re-raise it
        # If it's a connection error during start, we may handle it in DEV_MODE
        if session and session.is_active:
             # Exception likely came from the endpoint
             raise
        
        if not (settings.MODE == "dev" or os.getenv("DEV_MODE", "false").lower() == "true"):
            logger.error(f"Failed to connect to Primary DB: {e}")
            raise
        logger.warning(f"DB not available in DEV_MODE: {e}")
        # Only yield None if we haven't yielded yet
        # But FastAPI expects exactly one yield for dependencies
        # Actually, if we're here and haven't yielded, it's okay to yield None once.
        # But if we ALREADY yielded and an exception was thrown IN, we must not yield again.
        return 
    finally:
        if session:
            session.close()


class ReadOnlyViolationError(Exception):
    """Raised when a write is attempted on a read-only session."""
    pass


def get_read_db(request: Request, response: Response) -> Generator[Session, None, None]:
    """Yields a session from Read DB (Replica), with circuit breaker and read-only enforcement."""
    has_yielded = False
    
    # Pre-check: Is this endpoint allowed to use the replica?
    # We check the URL path. In a more advanced version, we could use endpoint tags.
    path = request.url.path
    is_allowed = any(path.startswith(allowed) for allowed in settings.REPLICA_READ_ALLOWLIST)
    
    if not is_allowed:
        response.headers["X-Talos-DB-Role"] = "primary"
        response.headers["X-Talos-Read-Fallback"] = "0"
        response.headers["X-Talos-Read-Reason"] = "not_allowlisted"
        with Session(_write_engine) as session:
            yield session
        return
    
    # Case 1: No distinct replica configured
    if not _read_engine_is_distinct:
        response.headers["X-Talos-DB-Role"] = "primary"
        response.headers["X-Talos-Read-Fallback"] = "0"
        with Session(_write_engine) as session:
            yield session
        return

    # Case 2: Circuit breaker is open
    if _circuit_breaker.is_open():
        response.headers["X-Talos-DB-Role"] = "primary"
        response.headers["X-Talos-Read-Fallback"] = "1"
        response.headers["X-Talos-Read-Reason"] = "circuit_open"
        logger.info("read_db_fallback", extra={"reason": "circuit_open", "db_role": "primary"})
        with Session(_write_engine) as session:
            yield session
        return

    # Case 3: Attempt replica
    try:
        # Test connection first
        conn = _read_engine.connect()
        conn.close()
        
        with Session(_read_engine) as session:
            # Enforce read-only transaction
            try:
                session.execute(text("SET TRANSACTION READ ONLY"))
            except Exception as e:
                logger.warning(f"Could not set read-only transaction: {e}")
            
            response.headers["X-Talos-DB-Role"] = "replica"
            response.headers["X-Talos-Read-Fallback"] = "0"
            
            try:
                has_yielded = True
                yield session
                _circuit_breaker.record_success()
            except ProgrammingError as e:
                # Check for read-only violation (misclassification)
                error_str = str(e).lower()
                if "read-only" in error_str or "cannot execute" in error_str:
                    logger.error(f"MISCLASSIFIED_ENDPOINT: Write attempted on read-only session: {e}")
                    raise HTTPException(
                        status_code=500,
                        detail={
                            "error": {
                                "code": "MISCLASSIFIED_ENDPOINT",
                                "message": "Write operation attempted on read-only database path. This is a bug."
                            }
                        }
                    )
                raise
            except Exception:
                raise
                
    except (OperationalError, TimeoutError, ConnectionError, OSError) as e:
        if has_yielded:
            raise
            
        # Availability errors - fallback is appropriate
        opened = _circuit_breaker.record_failure()
        
        if settings.READ_FALLBACK_ENABLED:
            reason = "connect_error"
            if "timeout" in str(e).lower():
                reason = "timeout"
            elif "pool" in str(e).lower():
                reason = "pool_exhausted"
            
            logger.warning(f"read_db_fallback", extra={"reason": reason, "error": str(e), "db_role": "primary"})
            response.headers["X-Talos-DB-Role"] = "primary"
            response.headers["X-Talos-Read-Fallback"] = "1"
            response.headers["X-Talos-Read-Reason"] = reason
            
            with Session(_write_engine) as session:
                yield session
        else:
            logger.error(f"Read DB unreachable and fallback DISABLED: {e}")
            raise HTTPException(status_code=503, detail="Database currently unavailable")


# --- Original Dependencies (Updated mappings) ---

from app.domain.interfaces import (
    UpstreamStore, ModelGroupStore, SecretStore, McpStore, AuditStore, 
    RoutingPolicyStore, RotationOperationStore
)
from app.adapters.json_store.stores import (
    UpstreamJsonStore, ModelGroupJsonStore, SecretJsonStore, McpJsonStore, AuditJsonStore, RoutingPolicyJsonStore
)
from app.adapters.postgres.stores import (
    PostgresUpstreamStore, PostgresModelGroupStore, PostgresMcpStore, 
    PostgresAuditStore, PostgresRoutingPolicyStore, PostgresRotationStore
)

# Determine Storage Backend
# Phase 15: Default to Postgres for core services to support Budgeting/Multi-Region.
# JSON fallback is ONLY for isolated local development without Postgres.
USE_JSON_STORES = os.getenv("USE_JSON_STORES", "false").lower() == "true"

def get_upstream_store(db: Session = Depends(get_write_db)) -> UpstreamStore:
    if USE_JSON_STORES: return UpstreamJsonStore()
    return PostgresUpstreamStore(db)

def get_model_group_store(db: Session = Depends(get_write_db)) -> ModelGroupStore:
    if USE_JSON_STORES: return ModelGroupJsonStore()
    return PostgresModelGroupStore(db)

def get_routing_policy_store(db: Session = Depends(get_write_db)) -> RoutingPolicyStore:
    if USE_JSON_STORES: return RoutingPolicyJsonStore()
    return PostgresRoutingPolicyStore(db)

from app.adapters.secrets.multi_provider import MultiKekProvider
from app.adapters.postgres.secret_store import PostgresSecretStore
from app.domain.secrets.ports import KekProvider, SecretStore

_kek_provider: Optional[MultiKekProvider] = None

def get_kek_provider() -> KekProvider:
    """Factory for KEK provider."""
    global _kek_provider
    if _kek_provider is None:
        _kek_provider = MultiKekProvider()
    return _kek_provider

def get_secret_store(
    db: Session = Depends(get_write_db),
    kek: KekProvider = Depends(get_kek_provider)
) -> SecretStore:
    if USE_JSON_STORES: return SecretJsonStore()
    return PostgresSecretStore(db, kek)

def get_read_secret_store(
    db: Session = Depends(get_read_db),
    kek: KekProvider = Depends(get_kek_provider)
) -> SecretStore:
    if USE_JSON_STORES: return SecretJsonStore()
    return PostgresSecretStore(db, kek)

def get_rotation_store(db: Session = Depends(get_write_db)) -> RotationOperationStore:
    if USE_JSON_STORES: 
        # For dev mode, we could use a JSON store or just persistent memory if dev-mode-json is used
        # For now, let's assume Postgres for rotation tracking as it's complex
        return PostgresRotationStore(db)
    return PostgresRotationStore(db)

from app.domain.secrets.manager import SecretsManager
def get_secrets_manager(
    store: SecretStore = Depends(get_secret_store),
    kek: KekProvider = Depends(get_kek_provider)
) -> SecretsManager:
    return SecretsManager(kek, store)

def get_mcp_store(db: Session = Depends(get_write_db)) -> McpStore:
    if USE_JSON_STORES: return McpJsonStore()
    return PostgresMcpStore(db)

def get_read_mcp_store(db: Session = Depends(get_read_db)) -> McpStore:
    if USE_JSON_STORES: return McpJsonStore()
    return PostgresMcpStore(db)

def get_audit_store(db: Session = Depends(get_write_db)) -> AuditStore:
    if USE_JSON_STORES: return AuditJsonStore()
    return PostgresAuditStore(db)

def get_read_audit_store(db: Session = Depends(get_read_db)) -> AuditStore:
    if USE_JSON_STORES: return AuditJsonStore()
    return PostgresAuditStore(db)

from app.domain.health import get_health_state
from app.domain.routing import RoutingService
from app.domain.interfaces import UsageStore, RateLimitStore
from app.adapters.postgres.stores import PostgresUsageStore
from app.adapters.json_store.stores import UsageJsonStore

def get_usage_store(db: Session = Depends(get_write_db)) -> UsageStore:
    if USE_JSON_STORES: return UsageJsonStore()
    return PostgresUsageStore(db)

def get_read_usage_store(db: Session = Depends(get_read_db)) -> UsageStore:
    if USE_JSON_STORES: return UsageJsonStore()
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
    if USE_JSON_STORES: return MemoryTaskStore()
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
    if USE_JSON_STORES: return MockPrincipalStore()
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

    if not key: return CapabilityValidator(supervisor_public_key="dev-placeholder")
    return CapabilityValidator(supervisor_public_key=key)

# --- Phase 15: Budget & Usage ---
from app.domain.budgets.service import BudgetService
from app.domain.usage.manager import UsageManager

def get_budget_service(db: Session = Depends(get_write_db)) -> BudgetService:
    return BudgetService(db)

def get_usage_manager(db: Session = Depends(get_write_db)) -> UsageManager:
    return UsageManager(db)

