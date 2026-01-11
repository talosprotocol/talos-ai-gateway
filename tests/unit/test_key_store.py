"""Tests for KeyStore and PostgresKeyStore."""
import pytest
from unittest.mock import MagicMock
import json
from datetime import datetime, timezone

from app.adapters.postgres.key_store import PostgresKeyStore, KeyData
from app.adapters.postgres.models import VirtualKey, Team


@pytest.fixture
def mock_db():
    return MagicMock()


@pytest.fixture
def mock_redis():
    return MagicMock()


@pytest.fixture
def store(mock_db, mock_redis):
    return PostgresKeyStore(
        db=mock_db,
        pepper="test-pepper",
        pepper_id="p1",
        redis_client=mock_redis
    )


def test_hash_key(store):
    """Test that keys are hashed correctly with pepper."""
    raw_key = "sk-test-123"
    hashed = store.hash_key(raw_key)
    
    # Format should be pepper_id:hash
    assert hashed.startswith("p1:")
    _, h = hashed.split(":", 1)
    
    # Verify it matches expected HMAC-SHA256
    import hmac
    import hashlib
    expected = hmac.new(b"test-pepper", raw_key.encode(), hashlib.sha256).hexdigest()
    assert h == expected


def test_lookup_by_hash_cache_hit(store, mock_redis, mock_db):
    """Test lookup returns cached data if available."""
    key_hash = "p1:hash123"
    key_data = {
        "id": "key-1",
        "team_id": "team-1",
        "org_id": "org-1",
        "scopes": ["read"],
        "allowed_model_groups": ["*"],
        "allowed_mcp_servers": ["*"],
        "revoked": False,
        "expires_at": None
    }
    
    mock_redis.get.return_value = json.dumps(key_data).encode()
    
    res = store.lookup_by_hash(key_hash)
    
    assert res.id == "key-1"
    assert res.team_id == "team-1"
    mock_redis.get.assert_called_with(f"key:{key_hash}")
    # Should NOT call DB
    mock_db.query.assert_not_called()


def test_lookup_by_hash_db_fallback_and_cache_set(store, mock_db, mock_redis):
    """Test lookup falls back to DB and sets cache."""
    key_hash = "p1:hash123"
    
    # Setup mock VK
    mock_vk = MagicMock(spec=VirtualKey)
    mock_vk.id = "key-1"
    mock_vk.team_id = "team-1"
    mock_vk.scopes = ["read"]
    mock_vk.allowed_model_groups = ["*"]
    mock_vk.allowed_mcp_servers = ["*"]
    mock_vk.revoked = False
    mock_vk.expires_at = None
    mock_vk.team = MagicMock(spec=Team)
    mock_vk.team.org_id = "org-1"
    
    mock_redis.get.return_value = None
    mock_db.query.return_value.filter.return_value.first.return_value = mock_vk
    
    res = store.lookup_by_hash(key_hash)
    
    assert res.id == "key-1"
    assert res.org_id == "org-1"
    
    # Verify cache was set
    mock_redis.setex.assert_called()
    called_args = mock_redis.setex.call_args[0]
    assert called_args[0] == f"key:{key_hash}"
    assert json.loads(called_args[2])["id"] == "key-1"


def test_lookup_by_hash_not_found_negative_cache(store, mock_db, mock_redis):
    """Test negative caching when key not found in DB."""
    key_hash = "p1:missing"
    
    mock_redis.get.return_value = None
    mock_db.query.return_value.filter.return_value.first.return_value = None
    
    res = store.lookup_by_hash(key_hash)
    
    assert res is None
    # Verify negative cache was set
    mock_redis.setex.assert_called_with(f"key:{key_hash}", 30, "__NEGATIVE__")


def test_lookup_by_hash_revocation_check(store, mock_redis, mock_db):
    """Test that revoked status is correctly returned."""
    key_hash = "p1:revoked"
    key_data = {
        "id": "key-1",
        "team_id": "team-1",
        "org_id": "org-1",
        "scopes": ["read"],
        "allowed_model_groups": ["*"],
        "allowed_mcp_servers": ["*"],
        "revoked": True, # REVOKED
        "expires_at": None
    }
    
    mock_redis.get.return_value = json.dumps(key_data).encode()
    
    res = store.lookup_by_hash(key_hash)
    
    assert res.revoked is True


def test_invalidate_cache(store, mock_redis):
    """Test cache invalidation."""
    key_hash = "p1:hash123"
    store.invalidate_cache(key_hash)
    mock_redis.delete.assert_called_with(f"key:{key_hash}")
