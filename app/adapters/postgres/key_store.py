"""KeyStore Interface and PostgresKeyStore Implementation.

This module provides the production-grade key store that:
1. Stores hashed keys with HMAC-SHA256 using peppered hashing
2. Supports tenant-aware lookups (team_id bound)
3. Implements Redis caching with short TTL
4. Handles revocation with immediate cache invalidation
"""
import hashlib
import hmac
import os
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Optional, NamedTuple

from sqlalchemy.orm import Session


class KeyData(NamedTuple):
    """Key lookup result."""
    id: str
    team_id: str
    org_id: str
    scopes: list
    allowed_model_groups: list
    allowed_mcp_servers: list
    revoked: bool
    expires_at: Optional[datetime]


class KeyStore(ABC):
    """Abstract interface for key storage and lookup."""

    @abstractmethod
    def lookup_by_hash(self, key_hash: str) -> Optional[KeyData]:
        """Look up key data by hash."""
        ...

    @abstractmethod
    def hash_key(self, raw_key: str) -> str:
        """Hash a raw key for lookup."""
        ...


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
        redis_client=None,
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
        self._pepper = (pepper or os.getenv("TALOS_KEY_PEPPER", "dev-pepper-change-in-prod")).encode()
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
            if os.getenv("DEV_MODE", "false").lower() not in ("true", "1", "yes"):
                raise RuntimeError("Production KeyStore MUST have a unique, secure pepper configured.")

        h = hmac.new(self._pepper, raw_key.encode(), hashlib.sha256)
        return f"{self._pepper_id}:{h.hexdigest()}"

    def lookup_by_hash(self, key_hash: str) -> Optional[KeyData]:
        """Look up key data by hash.
        
        First checks Redis cache, then falls back to database.
        """
        # Try cache first
        if self._redis:
            cached = self._try_cache_get(key_hash)
            if cached is not None:
                return cached if cached else None  # False = negative cache

        # Database lookup
        from ..postgres.models import VirtualKey
        
        # Extract hash from {pepper_id}:{hash} format
        parts = key_hash.split(":", 1)
        if len(parts) == 2:
            _, hash_only = parts
        else:
            hash_only = key_hash

        vk = self._db.query(VirtualKey).filter(VirtualKey.key_hash == hash_only).first()
        
        if not vk:
            if self._redis:
                self._cache_negative(key_hash)
            return None

        key_data = KeyData(
            id=vk.id,
            team_id=vk.team_id,
            org_id=vk.team.org_id if vk.team else None,
            scopes=vk.scopes or [],
            allowed_model_groups=vk.allowed_model_groups or [],
            allowed_mcp_servers=vk.allowed_mcp_servers or [],
            revoked=vk.revoked,
            expires_at=vk.expires_at,
        )

        if self._redis:
            self._cache_set(key_hash, key_data)

        return key_data

    def _try_cache_get(self, key_hash: str) -> Optional[KeyData | bool]:
        """Try to get from cache. Returns None if not in cache."""
        try:
            import json
            data = self._redis.get(f"key:{key_hash}")
            if data is None:
                return None
            if data == b"__NEGATIVE__":
                return False
            d = json.loads(data)
            return KeyData(**d)
        except Exception:
            return None

    def _cache_set(self, key_hash: str, key_data: KeyData) -> None:
        """Cache key data."""
        try:
            import json
            data = json.dumps({
                "id": key_data.id,
                "team_id": key_data.team_id,
                "org_id": key_data.org_id,
                "scopes": key_data.scopes,
                "allowed_model_groups": key_data.allowed_model_groups,
                "allowed_mcp_servers": key_data.allowed_mcp_servers,
                "revoked": key_data.revoked,
                "expires_at": key_data.expires_at.isoformat() if key_data.expires_at else None,
            })
            self._redis.setex(f"key:{key_hash}", self._cache_ttl, data)
        except Exception:
            pass

    def _cache_negative(self, key_hash: str) -> None:
        """Cache negative result for short duration."""
        try:
            self._redis.setex(f"key:{key_hash}", 30, "__NEGATIVE__")
        except Exception:
            pass

    def invalidate_cache(self, key_hash: str) -> None:
        """Invalidate cache entry for a key (call on revocation)."""
        if self._redis:
            try:
                self._redis.delete(f"key:{key_hash}")
            except Exception:
                pass


def get_key_store(db: Session = None, redis_client=None) -> KeyStore:
    """Factory function to get appropriate key store.
    
    In production, returns PostgresKeyStore (requires db session).
    """
    dev_mode = os.getenv("DEV_MODE", "false").lower() in ("true", "1", "yes")
    pepper = os.getenv("TALOS_KEY_PEPPER")
    pepper_id = os.getenv("TALOS_PEPPER_ID", "v1")
    
    if not dev_mode:
        if not pepper:
            raise RuntimeError("CRITICAL: TALOS_KEY_PEPPER environment variable is missing in production mode.")
        if pepper == "dev-pepper-change-in-prod":
            raise RuntimeError("CRITICAL: Default pepper detected in production mode. Security breach risk.")
        
        if db is None:
            raise RuntimeError("Database session required for production KeyStore")
    else:
        # Use a stable dev pepper if none provided
        pepper = pepper or "dev-pepper-change-in-prod"
        if db is None:
            # Fallback for dev if no DB provided (though usually DB is present)
            # In Phase 0/1 we decided to remove MockKeyStore from prod imports.
            # If we need a Mock for local dev without Postgres, it must be in a 
            # test-only or dev-only file. For now, we expect Postgres even in dev
            # unless we explicitly implement a JsonKeyStore.
            pass

    return PostgresKeyStore(
        db=db, 
        pepper=pepper, 
        pepper_id=pepper_id, 
        redis_client=redis_client
    )
