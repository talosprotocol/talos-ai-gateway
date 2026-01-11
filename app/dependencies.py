"""Dependency Injection Module."""
import os
import logging
from typing import Generator

from fastapi import Depends
from sqlalchemy.orm import Session

from app.domain.interfaces import UpstreamStore, ModelGroupStore, SecretStore, McpStore, AuditStore, RoutingPolicyStore
from app.adapters.json_store.stores import (
    UpstreamJsonStore, ModelGroupJsonStore, SecretJsonStore, McpJsonStore, AuditJsonStore, RoutingPolicyJsonStore
)
from app.adapters.postgres.session import get_db
from app.adapters.postgres.stores import (
    PostgresUpstreamStore, PostgresModelGroupStore, PostgresSecretStore, PostgresMcpStore, PostgresAuditStore, PostgresRoutingPolicyStore
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

def get_secret_store(db: Session = Depends(get_db)) -> SecretStore:
    if DEV_MODE:
        return SecretJsonStore()
    return PostgresSecretStore(db)

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
