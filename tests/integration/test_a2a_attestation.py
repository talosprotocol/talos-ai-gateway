import pytest
import pytest_asyncio
import asyncio
import time
import uuid
import base64
import hashlib
from httpx import AsyncClient, ASGITransport
from cryptography.hazmat.primitives.asymmetric import ed25519
from cryptography.hazmat.primitives import serialization
from app.main import app
from app.domain.a2a.canonical import canonical_json_bytes
from app.dependencies import get_key_store, get_principal_store, get_surface_registry

# Generate a test keypair for Phase A5 tests
TEST_PRIVATE_KEY = ed25519.Ed25519PrivateKey.generate()
TEST_PUBLIC_KEY = TEST_PRIVATE_KEY.public_key().public_bytes(
    encoding=serialization.Encoding.Raw,
    format=serialization.PublicFormat.Raw
)
TEST_KEY_ID = TEST_PUBLIC_KEY.hex()

# Register the test public key in mocked store
TEST_KEY_HASH = hashlib.sha256(TEST_PUBLIC_KEY).hexdigest()
TEST_KEY_RAW = TEST_PUBLIC_KEY

def sign_request(body: dict, private_key: ed25519.Ed25519PrivateKey, key_id: str):
    nonce = str(uuid.uuid4())
    timestamp = str(int(time.time()))
    
    canonical_body = canonical_json_bytes(body)
    payload = f"{nonce}|{timestamp}|".encode() + canonical_body
    signature = private_key.sign(payload)
    
    # Base64url encode signature
    signature_b64 = base64.urlsafe_b64encode(signature).decode().rstrip("=")
    
    return {
        "Authorization": f"Bearer test-key-value", # Real value doesn't matter for mock hash lookup
        "X-Talos-Key-Id": key_id,
        "X-Talos-Signature": signature_b64,
        "X-Talos-Nonce": nonce,
        "X-Talos-Timestamp": timestamp
    }

@pytest_asyncio.fixture
async def async_client():
    from unittest.mock import patch, AsyncMock, Mock
    # Mock redis to avoid event loop closure issues in tests
    with patch("app.adapters.redis.client.get_redis_client", new_callable=AsyncMock) as mock_get_redis:
        mock_redis = AsyncMock()
        mock_get_redis.return_value = mock_redis
        
        # Side effect to handle replay protection logic
        used_nonces = set()
        async def mock_set(key, val, ex=None, nx=False):
            if nx:
                if key in used_nonces:
                    return False
                used_nonces.add(key)
                return True
            return True
            
        mock_redis.set.side_effect = mock_set

        # Mock KeyStore
        mock_keystore = Mock()
        
        # Test Key Setup
        TEST_IV = "test_iv"
        # Mock Key Object (Mimics PostgresKeyStore return)
        mock_key_data = Mock()
        mock_key_data.id = TEST_KEY_ID
        mock_key_data.team_id = "team-1"
        mock_key_data.org_id = "org-1"
        mock_key_data.scopes = ["llm.invoke", "mcp.invoke", "a2a.invoke", "a2a.stream"]
        mock_key_data.allowed_model_groups = ["*"]
        mock_key_data.allowed_mcp_servers = ["*"]
        mock_key_data.principal_id = "p-1"
        mock_key_data.revoked = False
        mock_key_data.public_key_bytes = TEST_PUBLIC_KEY # For verifier

        def mock_hash_key(key):
             # Map string token "test-key-value" to TEST_KEY_HASH
             if key == "test-key-value":
                 return TEST_KEY_HASH
             # Otherwise return a unique hash
             return "hashed_" + key

        mock_keystore.hash_key.side_effect = mock_hash_key
        
        def mock_lookup_by_hash(h):
            # Strict check: Only return for TEST_KEY_HASH
            if h == TEST_KEY_HASH:
                return mock_key_data
            return None
            
        mock_keystore.lookup_by_hash.side_effect = mock_lookup_by_hash
        
        # Override get_virtual_key used by attestation verifier/context
        async def mock_get_virtual_key(kid):
            if kid == TEST_KEY_ID:
                return mock_key_data
            return None
        mock_keystore.get_virtual_key = mock_get_virtual_key
        
        app.dependency_overrides[get_key_store] = lambda: mock_keystore

        # Principal Store - Strict Check
        mock_p_store = Mock()
        async def mock_get_principal(pid):
            if pid == TEST_KEY_ID:
                 return {"id": "p-1", "team_id": "team-1", "is_active": True, "public_key": TEST_PUBLIC_KEY.hex()}
            return None
        mock_p_store.get_principal = mock_get_principal
        app.dependency_overrides[get_principal_store] = lambda: mock_p_store
        
        # Ensure Registry is loaded
        real_registry = get_surface_registry() 
        # (Assuming it loads from file OK, valid_inventory.py passed so it should)
        
        
        from unittest.mock import patch, AsyncMock, Mock

        # Override Attestation Verifier (still overrides get_attestation_verifier dependency, which helps get_auth_context)
        from app.middleware.attestation_http import AttestationVerifier
        
        # Mock Replay Detector
        # This detector is used by `AttestationVerifier` in `get_auth_context`.
        mock_replay = Mock()
        mock_replay.check_replay.return_value = False # Default no replay
        
        used_nonces_detector = set()
        def stateful_check_replay(kid, nonce):
            key = f"{kid}:{nonce}"
            if key in used_nonces_detector:
                return True
            used_nonces_detector.add(key)
            return False
        mock_replay.check_replay.side_effect = stateful_check_replay

        # Mock Verifier
        real_verifier = AttestationVerifier(mock_p_store, mock_replay)
        
        from app.dependencies import get_attestation_verifier
        app.dependency_overrides[get_attestation_verifier] = lambda: real_verifier

        # Mock other stores
        from app.dependencies import (
            get_audit_store, get_rate_limit_store, get_usage_store, 
            get_task_store, get_routing_service, get_mcp_client
        )
        
        app.dependency_overrides[get_audit_store] = lambda: Mock()
        
        mock_rl_limits = Mock()
        # check_limit is awaited, so it must be AsyncMock or return awaitable.
        mock_rl_limits.check_limit = AsyncMock()
        mock_rl_limits.check_limit.return_value = Mock(allowed=True)
        app.dependency_overrides[get_rate_limit_store] = lambda: mock_rl_limits
        
        app.dependency_overrides[get_usage_store] = lambda: Mock()
        
        mock_ts = Mock()
        mock_ts.update_task_status.return_value = 1
        app.dependency_overrides[get_task_store] = lambda: mock_ts
        
        # Routing Service Mock needs to return Dict
        mock_routing = Mock()
        mock_routing.select_upstream.return_value = {
            "upstream": {"endpoint": "http://mock-llm", "credentials_ref": "env:mock_key"},
            "model_name": "mock-model"
        }
        app.dependency_overrides[get_routing_service] = lambda: mock_routing
        
        app.dependency_overrides[get_mcp_client] = lambda: Mock()
        
        # Patch Redis Client globally - Stateful Mock!
        with patch("redis.asyncio.Redis", new_callable=Mock) as mock_redis_cls, \
             patch("redis.asyncio.from_url", new_callable=Mock) as mock_from_url, \
             patch("app.domain.a2a.dispatcher.invoke_openai_compatible", new_callable=AsyncMock) as mock_invoke:
            
            # Setup LLM Mock
            mock_invoke.return_value = {
                "choices": [{"message": {"role": "assistant", "content": "Mocked Response"}}] 
            }
            # Or formatted result structure expected by dispatcher?
            # Dispatcher expects `invoke_openai_compatible` to return raw provider response.
            # Then map_llm_response_to_task uses it.
            
            # Create a shared mock instance
            mock_redis = AsyncMock()
            mock_redis_cls.return_value = mock_redis
            mock_redis_cls.from_url.return_value = mock_redis
            mock_from_url.return_value = mock_redis
            
            # Stateful SET logic
            redis_store = {}
            async def mock_set(name, value, ex=None, px=None, nx=False, xx=False, keepttl=False):
                if nx:
                    if name in redis_store:
                        return None 
                    redis_store[name] = value
                    return True 
                redis_store[name] = value
                return True
            mock_redis.set.side_effect = mock_set
            
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                yield ac
        
        app.dependency_overrides = {}

@pytest.mark.asyncio
async def test_attestation_success(async_client):
    payload = {
        "jsonrpc": "2.0",
        "method": "tasks.send",
        "params": {
            "profile": {
                "profile_id": "a2a-compat",
                "profile_version": "0.1",
                "spec_source": "a2a-protocol"
            },
            "input": [{"role": "user", "content": [{"text": "Hello world"}]}]
        },
        "id": 1
    }
    headers = sign_request(payload, TEST_PRIVATE_KEY, TEST_KEY_ID)
    
    # Try without trailing slash if trailing slash is the issue
    response = await async_client.post("/a2a/v1/", json=payload, headers=headers)
        
    if response.status_code != 200:
        print(f"DEBUG: Success test failed with {response.status_code}: {response.text}")
        
    assert response.status_code == 200
    # Authorized means it either succeeded (result) or reached dispatcher (UPSTREAM_UNAVAILABLE)
    res_json = response.json()
    assert "result" in res_json or (
        "error" in res_json and res_json["error"].get("data", {}).get("talos_code") == "UPSTREAM_UNAVAILABLE"
    )

@pytest.mark.asyncio
async def test_attestation_invalid_signature(async_client):
    payload = {"jsonrpc": "2.0", "method": "tasks.get", "params": {"task_id": "any"}, "id": 1}
    headers = sign_request(payload, TEST_PRIVATE_KEY, TEST_KEY_ID)
    
    # Tamper with signature
    headers["X-Talos-Signature"] = "invalid" + headers["X-Talos-Signature"][7:]
    
    response = await async_client.post("/a2a/v1/", json=payload, headers=headers)
        
    assert response.status_code == 401
    assert response.json()["error"]["talos_code"] == "INVALID_SIGNATURE"

@pytest.mark.asyncio
async def test_attestation_body_tampering(async_client):
    payload = {"jsonrpc": "2.0", "method": "tasks.get", "params": {"task_id": "any"}, "id": 1}
    headers = sign_request(payload, TEST_PRIVATE_KEY, TEST_KEY_ID)
    
    # Tamper with body after signing
    payload["params"]["task_id"] = "different-id"
    
    response = await async_client.post("/a2a/v1/", json=payload, headers=headers)
        
    assert response.status_code == 401
    assert response.json()["error"]["talos_code"] == "INVALID_SIGNATURE"

@pytest.mark.asyncio
async def test_attestation_replay_protection(async_client):
    payload = {"jsonrpc": "2.0", "method": "tasks.get", "params": {"task_id": "any"}, "id": 1}
    headers = sign_request(payload, TEST_PRIVATE_KEY, TEST_KEY_ID)
    
    # First request
    response1 = await async_client.post("/a2a/v1/", json=payload, headers=headers)
    assert response1.status_code in [200, 404, 302] 
    
    # Second request with same nonce
    response2 = await async_client.post("/a2a/v1/", json=payload, headers=headers)
    assert response2.status_code == 401
    # Check if REPLAY_ATTACK or INVALID_SIGNATURE (if nonce check failed/passed but Redis was involved)
    assert response2.json()["error"]["talos_code"] == "REPLAY_ATTACK"

@pytest.mark.asyncio
async def test_attestation_clock_skew(async_client):
    payload = {"jsonrpc": "2.0", "method": "tasks.get", "params": {"task_id": "any"}, "id": 1}
    
    # Test skew (stale)
    stale_payload = payload
    stale_ts = str(int(time.time()) - 300)
    stale_headers = sign_request(stale_payload, TEST_PRIVATE_KEY, TEST_KEY_ID)
    stale_headers["X-Talos-Timestamp"] = stale_ts
    
    # Resign with stale timestamp
    canon_body = canonical_json_bytes(stale_payload)
    signing_payload = f"{stale_headers['X-Talos-Nonce']}|{stale_ts}|".encode() + canon_body
    stale_headers["X-Talos-Signature"] = base64.urlsafe_b64encode(TEST_PRIVATE_KEY.sign(signing_payload)).decode().rstrip("=")

    response = await async_client.post("/a2a/v1/", json=stale_payload, headers=stale_headers)
        
    assert response.status_code == 401
    assert response.json()["error"]["talos_code"] == "ATTESTATION_EXPIRED"

@pytest.mark.asyncio
async def test_attestation_unknown_key(async_client):
    # New unknown keypair
    ALT_PRIVATE_KEY = ed25519.Ed25519PrivateKey.generate()
    ALT_PUBLIC_KEY = ALT_PRIVATE_KEY.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw
    )
    ALT_KEY_ID = ALT_PUBLIC_KEY.hex()
    
    payload = {"jsonrpc": "2.0", "method": "tasks.get", "params": {"task_id": "any"}, "id": 1}
    headers = sign_request(payload, ALT_PRIVATE_KEY, ALT_KEY_ID)
    
    response = await async_client.post("/a2a/v1/", json=payload, headers=headers)
        
    assert response.status_code == 401
    assert response.json()["error"]["talos_code"] == "AUTH_INVALID"
