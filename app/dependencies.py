"""Dependency Injection Module."""
import os
import logging

from fastapi import Depends
from sqlalchemy.orm import Session

from app.domain.interfaces import UpstreamStore, ModelGroupStore, SecretStore, McpStore, AuditStore, RoutingPolicyStore
from app.adapters.json_store.stores import (
    UpstreamJsonStore, ModelGroupJsonStore, SecretJsonStore, McpJsonStore, AuditJsonStore, RoutingPolicyJsonStore
)
from app.adapters.postgres.session import get_db
from app.adapters.postgres.stores import (
    PostgresUpstreamStore, PostgresModelGroupStore, PostgresMcpStore, PostgresAuditStore, PostgresRoutingPolicyStore
)

logger = logging.getLogger(__name__)

DEV_MODE = os.getenv("DEV_MODE", "false").lower() == "true"

def get_upstream_store(db: Session = Depends(get_db)) -> UpstreamStore:
    if DEV_MODE:
        return UpstreamJsonStore()
    return PostgresUpstreamStore(db)

def get_model_group_store(db: Session = Depends(get_db)) -> ModelGroupStore:
    if DEV_MODE:
        return ModelGroupJsonStore()
    return PostgresModelGroupStore(db)

def get_routing_policy_store(db: Session = Depends(get_db)) -> RoutingPolicyStore:
    if DEV_MODE:
        return RoutingPolicyJsonStore()
    return PostgresRoutingPolicyStore(db)

from app.adapters.secrets.local_provider import LocalKekProvider
from app.adapters.postgres.secret_store import PostgresSecretStore
from app.domain.secrets.ports import KekProvider, SecretStore

def get_kek_provider() -> KekProvider:
    """Factory for KEK provider."""
    master_key = os.getenv("TALOS_MASTER_KEY")
    key_id = os.getenv("TALOS_KEK_ID", "v1")

    if not master_key:
        master_key = os.getenv("MASTER_KEY")
        if not master_key:
            if DEV_MODE:
                master_key = "dev-master-key-change-in-prod"
            else:
                raise RuntimeError("CRITICAL: TALOS_MASTER_KEY missing in production.")

    return LocalKekProvider(master_key, key_id)

def get_secret_store(
    db: Session = Depends(get_db),
    kek: KekProvider = Depends(get_kek_provider)
) -> SecretStore:
    if DEV_MODE:
        return SecretJsonStore()
    return PostgresSecretStore(db, kek)

from app.domain.secrets.manager import SecretsManager

def get_secrets_manager(
    store: SecretStore = Depends(get_secret_store),
    kek: KekProvider = Depends(get_kek_provider)
) -> SecretsManager:
    return SecretsManager(kek, store)

def get_mcp_store(db: Session = Depends(get_db)) -> McpStore:
    if DEV_MODE:
        return McpJsonStore()
    return PostgresMcpStore(db)

def get_audit_store(db: Session = Depends(get_db)) -> AuditStore:
    if DEV_MODE:
        return AuditJsonStore()
    return PostgresAuditStore(db)

from app.domain.health import get_health_state
from app.domain.routing import RoutingService
from app.domain.interfaces import UsageStore, RateLimitStore
from app.adapters.postgres.stores import PostgresUsageStore
from app.adapters.json_store.stores import UsageJsonStore

def get_usage_store(db: Session = Depends(get_db)) -> UsageStore:
    if DEV_MODE:
        return UsageJsonStore()
    return PostgresUsageStore(db)

from app.adapters.redis.stores import RedisRateLimitStore
from app.adapters.memory_store.stores import MemoryRateLimitStore

def get_routing_service(
    u_store: UpstreamStore = Depends(get_upstream_store),
    mg_store: ModelGroupStore = Depends(get_model_group_store),
    rp_store: RoutingPolicyStore = Depends(get_routing_policy_store)
) -> RoutingService:
    return RoutingService(u_store, mg_store, rp_store, get_health_state())

def get_rate_limit_store() -> RateLimitStore:
    if os.getenv("REDIS_URL"):
        return RedisRateLimitStore()
    return MemoryRateLimitStore()

from app.adapters.mcp.client import McpClient

def get_mcp_client() -> McpClient:
    return McpClient()

from app.domain.interfaces import SessionStore
from app.adapters.redis.stores import RedisSessionStore
from app.adapters.memory_store.stores import MemorySessionStore

def get_session_store() -> SessionStore:
    if os.getenv("REDIS_URL"):
        return RedisSessionStore()
    return MemorySessionStore()

from app.domain.interfaces import TaskStore
from app.adapters.postgres.task_store import PostgresTaskStore
from app.adapters.memory_store.stores import MemoryTaskStore

def get_task_store(db: Session = Depends(get_db)) -> TaskStore:
    if DEV_MODE:
        return MemoryTaskStore()
    return PostgresTaskStore(db)

from app.adapters.postgres.key_store import get_key_store as get_ks_factory, KeyStore
from app.adapters.redis.client import get_redis_client
from app.domain.tga.validator import CapabilityValidator
from app.settings import settings

async def get_key_store(db: Session = Depends(get_db)) -> KeyStore:
    redis_client = await get_redis_client()
    return get_ks_factory(db, redis_client=redis_client)

from app.domain.interfaces import PrincipalStore
from app.adapters.postgres.stores import PostgresPrincipalStore
# We need a MockPrincipalStore/JsonPrincipalStore for DEV_MODE if needed, or fallback.
class MockPrincipalStore(PrincipalStore):
    def get_principal(self, principal_id: str): return None

def get_principal_store(db: Session = Depends(get_db)) -> PrincipalStore:
    if DEV_MODE:
        return MockPrincipalStore()
    return PostgresPrincipalStore(db)

from app.middleware.attestation_http import AttestationVerifier, RedisReplayDetector

async def get_attestation_verifier(
    p_store: PrincipalStore = Depends(get_principal_store)
) -> AttestationVerifier:
    redis_client = await get_redis_client()
    replay = RedisReplayDetector(redis_client)
    return AttestationVerifier(p_store, replay)

from app.domain.registry import SurfaceRegistry

_registry_instance = None

def get_surface_registry() -> SurfaceRegistry:
    global _registry_instance
    if _registry_instance is None:
        # Assume path is relative to CWD (usually root of repo in docker)
        # Or configured via env.
        path = os.getenv("SURFACE_INVENTORY_PATH", "deploy/repos/talos-contracts/inventory/gateway_surface.json")
        _registry_instance = SurfaceRegistry(path)
    return _registry_instance

from app.domain.audit import AuditLogger
from app.domain.sink import AuditSink, StdOutSink, HttpSink

_audit_logger_instance = None

def get_audit_logger() -> AuditLogger:
    global _audit_logger_instance
    if _audit_logger_instance is None:
        # Determine sink type
        sink_url = os.getenv("AUDIT_SINK_URL")
        sink: AuditSink
        if sink_url:
            api_key = os.getenv("AUDIT_SINK_API_KEY")
            sink = HttpSink(sink_url, api_key)
        else:
            sink = StdOutSink()
            
        _audit_logger_instance = AuditLogger(sink)
    return _audit_logger_instance

from app.policy import PolicyEngine, DeterministicPolicyEngine

_policy_engine_instance = None

def get_policy_engine() -> PolicyEngine:
    global _policy_engine_instance
    if _policy_engine_instance is None:
        # TODO: Load from real DB or Config
        # For Phase 7.1, we initialize with empty dicts or basic system roles
        roles_db = {
            "role-admin": {"id": "role-admin", "permissions": ["*:*"]},
            "role-public": {"id": "role-public", "permissions": ["public:*"]}
        }
        bindings_db = {}
        _policy_engine_instance = DeterministicPolicyEngine(roles_db, bindings_db)
    return _policy_engine_instance

def get_capability_validator() -> CapabilityValidator:
    """Provides the TGA capability validator."""
    if not settings.supervisor_public_key:
        # In dev/testing we might not have a key, but for TGA calls it will be required.
        # We return a validator with a dummy key that will fail on real signatures.
        return CapabilityValidator(supervisor_public_key="dev-placeholder")
    return CapabilityValidator(supervisor_public_key=settings.supervisor_public_key)
