"""KeyStore Interface and PostgresKeyStore Implementation.

This module provides the production-grade key store that:
1. Stores hashed keys with HMAC-SHA256 using peppered hashing
2. Supports tenant-aware lookups (team_id bound)
3. Implements Redis caching with short TTL
4. Handles revocation with immediate cache invalidation
"""
import hashlib
import hmac
import json
import os
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any, Dict, NamedTuple, Optional, Union

from sqlalchemy.orm import Session

from .models import VirtualKey


class KeyData(NamedTuple):
    """Key lookup result."""
    id: str
    team_id: str
    org_id: str
    scopes: list[Any]
    allowed_model_groups: list[Any]
    allowed_mcp_servers: list[Any]
    revoked: bool
    expires_at: Optional[datetime]
    # Phase 15: Budget & Policy
    budget_mode: str
    overdraft_usd: str  # Decimal as string for JSON safety
    max_tokens_default: Optional[int]
    budget: Dict[str, Any]  # Budget Metadata
    team_budget_mode: str
    team_overdraft_usd: str
    team_max_tokens_default: Optional[int]
    team_budget: Dict[str, Any]


class KeyStore(ABC):
    """Abstract interface for key storage and lookup."""

    @abstractmethod
    def lookup_by_hash(self, key_hash: str) -> Optional[KeyData]:
        """Lookup a key by its hash."""

    @abstractmethod
    def hash_key(self, raw_key: str) -> str:
        """Hash a key for storage/lookup."""


class PostgresKeyStore(KeyStore):
    """Database-backed key store with HMAC-SHA256 hashing.
    
    Keys are hashed using HMAC-SHA256 with a pepper stored in KMS/env.
    The pepper_id is stored with the hash to support rotation.
    """

    def __init__(
        self,
        db: Session,
        pepper: Optional[str] = None,
        pepper_id: str = "p1",
        redis_client: Optional[Any] = None,
        cache_ttl_seconds: int = 30,
    ):
        """Initialize store.
        
        Args:
            db: SQLAlchemy database session
            pepper: Secret pepper for HMAC. If None, reads from env.
            pepper_id: Version identifier for the pepper
            redis_client: Optional Redis client for caching
            cache_ttl_seconds: Cache TTL (default: 30s, max 120s)
        """
        self._db = db
        self._pepper = (
            pepper
            or os.getenv("TALOS_KEY_PEPPER")
            or "dev-pepper-change-in-prod"
        ).encode()
        self._pepper_id = pepper_id
        self._redis = redis_client
        # Hard max TTL of 120s
        self._cache_ttl = min(cache_ttl_seconds, 120)

    def hash_key(self, raw_key: str) -> str:
        """Hash a raw API key using HMAC-SHA256 with pepper.
        
        Format: {pepper_id}:{hex_hash}
        """
        # Ensure we don't hash with a default pepper in production
        if not self._pepper or self._pepper == b"dev-pepper-change-in-prod":
            # This check is a safety net; the factory should have caught it.
            if os.getenv("DEV_MODE", "false").lower() not in (
                "true", "1", "yes"
            ):
                raise RuntimeError(
                    "Production KeyStore MUST have a unique, secure pepper "
                    "configured."
                )

        h = hmac.new(self._pepper, raw_key.encode(), hashlib.sha256)
        return f"{self._pepper_id}:{h.hexdigest()}"

    def lookup_by_hash(self, key_hash: str) -> Optional[KeyData]:
        """Look up key data by hash.
        
        First checks Redis cache, then falls back to database.
        """
        # Try cache first
        if self._redis:
            cached = self._try_cache_get(key_hash)
            if cached is False:
                return None
            if isinstance(cached, KeyData):
                return cached
        
        # Query with full hash format including pepper_id prefix
        vk = (
            self._db.query(VirtualKey)
            .filter(VirtualKey.key_hash == key_hash)
            .first()
        )
        
        if not vk:
            if self._redis:
                self._cache_negative(key_hash)
            return None

        # mypy thinks these are Columns because of legacy Declarative style.
        # Runtime is fine. We cast to silence mypy or ignore.
        # Prefer ignore for brevity over massive casting block.
        key_data = KeyData(
            # Explicit casts for SQLAlchemy Column types
            # Note: We use type: ignore because SQLAlchemy Column[T] vs
            # instance type inference is tricky for mypy in this context
            # without specific plugin support for the property access pattern
            id=str(vk.id),  # Explicit string conversion safer
            team_id=str(vk.team_id),
            # Handle optionality safety
            org_id=(
                str(vk.team.org_id)
                if vk.team and vk.team.org_id
                else "unknown"
            ),
            scopes=vk.scopes or [],  # type: ignore
            allowed_model_groups=vk.allowed_model_groups or [],  # type: ignore
            allowed_mcp_servers=vk.allowed_mcp_servers or [],  # type: ignore
            revoked=bool(vk.revoked),
            expires_at=vk.expires_at,  # type: ignore
            budget_mode=str(vk.budget_mode),
            overdraft_usd=str(vk.overdraft_usd),
            max_tokens_default=vk.max_tokens_default,  # type: ignore
            budget=vk.budget or {},  # type: ignore
            team_budget_mode=vk.team.budget_mode if vk.team else "off",
            team_overdraft_usd=str(vk.team.overdraft_usd) if vk.team else "0",
            team_max_tokens_default=(
                vk.team.max_tokens_default if vk.team else None
            ),
            team_budget=vk.team.budget if vk.team else {},
        )

        if self._redis:
            self._cache_set(key_hash, key_data)

        return key_data

    def _try_cache_get(self, key_hash: str) -> Optional[Union[KeyData, bool]]:
        """Try to get from cache. Returns None if not in cache."""
        if not self._redis:
            return None
        try:
            data = self._redis.get(f"key:{key_hash}")
            if data is None:
                return None
            if data == b"__NEGATIVE__":
                return False
            d = json.loads(data)
            return KeyData(**d)
        except Exception:  # pylint: disable=broad-exception-caught
            return None

    def _cache_set(self, key_hash: str, key_data: KeyData) -> None:
        """Cache key data."""
        if not self._redis:
            return
        try:
            data = json.dumps({
                "id": key_data.id,
                "team_id": key_data.team_id,
                "org_id": key_data.org_id,
                "scopes": key_data.scopes,
                "allowed_model_groups": key_data.allowed_model_groups,
                "allowed_mcp_servers": key_data.allowed_mcp_servers,
                "revoked": key_data.revoked,
                "expires_at": (
                    key_data.expires_at.isoformat()
                    if key_data.expires_at
                    else None
                ),
                "budget_mode": key_data.budget_mode,
                "overdraft_usd": key_data.overdraft_usd,
                "max_tokens_default": key_data.max_tokens_default,
                "budget": key_data.budget,
                "team_budget_mode": key_data.team_budget_mode,
                "team_overdraft_usd": key_data.team_overdraft_usd,
                "team_max_tokens_default": key_data.team_max_tokens_default,
                "team_budget": key_data.team_budget
            })
            self._redis.setex(f"key:{key_hash}", self._cache_ttl, data)
        except Exception:  # pylint: disable=broad-exception-caught
            pass

    def _cache_negative(self, key_hash: str) -> None:
        """Cache negative result for short duration."""
        if not self._redis:
            return
        try:
            self._redis.setex(f"key:{key_hash}", 30, "__NEGATIVE__")
        except Exception:  # pylint: disable=broad-exception-caught
            pass

    def invalidate_cache(self, key_hash: str) -> None:
        """Invalidate cache entry for a key (call on revocation)."""
        if self._redis:
            try:
                self._redis.delete(f"key:{key_hash}")
            except Exception:  # pylint: disable=broad-exception-caught
                pass


def get_key_store(
    db: Optional[Session] = None, redis_client: Any = None
) -> KeyStore:
    """Factory function to get appropriate key store.
    
    In production, returns PostgresKeyStore (requires db session).
    """
    pepper = os.getenv("TALOS_KEY_PEPPER")
    pepper_id = os.getenv("TALOS_PEPPER_ID", "p1")
    
    if not pepper:
        raise RuntimeError(
            "CRITICAL: TALOS_KEY_PEPPER environment variable is missing in "
            "production mode."
        )
    if pepper == "dev-pepper-change-in-prod":
        raise RuntimeError(
            "CRITICAL: Default pepper detected in production mode. "
            "Security breach risk."
        )
    
    if db is None:
        raise RuntimeError("Database session required for production KeyStore")
    return PostgresKeyStore(
        db=db, 
        pepper=pepper, 
        pepper_id=pepper_id, 
        redis_client=redis_client
    )
