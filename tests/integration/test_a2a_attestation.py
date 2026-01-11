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
from app.middleware.auth_public import MOCK_KEYS



# Generate a test keypair for Phase A5 tests
TEST_PRIVATE_KEY = ed25519.Ed25519PrivateKey.generate()
TEST_PUBLIC_KEY = TEST_PRIVATE_KEY.public_key().public_bytes(
    encoding=serialization.Encoding.Raw,
    format=serialization.PublicFormat.Raw
)
TEST_KEY_ID = TEST_PUBLIC_KEY.hex()

# Register the test public key in MOCK_KEYS
TEST_KEY_HASH = hashlib.sha256(TEST_PUBLIC_KEY).hexdigest()
MOCK_KEYS[TEST_KEY_HASH] = {
    "id": "test-attest-key",
    "team_id": "team-1",
    "org_id": "org-1",
    "scopes": ["llm.invoke", "mcp.invoke", "a2a.invoke", "a2a.stream"],
    "allowed_model_groups": ["*"],
    "allowed_mcp_servers": ["*"],
    "revoked": False
}

def sign_request(body: dict, private_key: ed25519.Ed25519PrivateKey, key_id: str):
    nonce = str(uuid.uuid4())
    timestamp = str(int(time.time()))
    
    canonical_body = canonical_json_bytes(body)
    payload = f"{nonce}|{timestamp}|".encode() + canonical_body
    signature = private_key.sign(payload)
    
    # Base64url encode signature
    signature_b64 = base64.urlsafe_b64encode(signature).decode().rstrip("=")
    
    return {
        "X-Talos-Key-Id": key_id,
        "X-Talos-Signature": signature_b64,
        "X-Talos-Nonce": nonce,
        "X-Talos-Timestamp": timestamp
    }

@pytest_asyncio.fixture
async def async_client():
    from unittest.mock import patch, AsyncMock
    # Mock redis to avoid event loop closure issues in tests
    with patch("app.middleware.attestation.get_redis_client", new_callable=AsyncMock) as mock_get_redis:
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
        
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            yield ac

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
