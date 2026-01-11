import time
import base64
import hashlib
import json
from typing import Optional, Dict, Any
from fastapi import Request, Header, HTTPException, Depends
from app.domain.a2a.canonical import canonical_json_bytes

from app.domain.a2a.canonical import canonical_json_bytes
from app.middleware.auth_public import AuthContext
from app.adapters.redis.client import get_redis_client
from app.dependencies import get_key_store
from app.adapters.postgres.key_store import KeyStore
import redis.asyncio as redis

# Replay protection window
NONCE_TTL = 300 # 5 minutes
MAX_CLOCK_SKEW = 60 # 60 seconds

async def get_attestation_auth(
    request: Request,
    x_talos_key_id: Optional[str] = Header(None, alias="X-Talos-Key-Id"),
    x_talos_signature: Optional[str] = Header(None, alias="X-Talos-Signature"),
    x_talos_nonce: Optional[str] = Header(None, alias="X-Talos-Nonce"),
    x_talos_timestamp: Optional[str] = Header(None, alias="X-Talos-Timestamp"),
    key_store: KeyStore = Depends(get_key_store)
) -> Optional[AuthContext]:
    """
    Verifies Talos A2A Attestation headers.
    Returns AuthContext if valid, or None if headers are missing (to allow fallback).
    Raises HTTPException for invalid attestation attempts.
    """

    
    if not all([x_talos_key_id, x_talos_signature, x_talos_nonce, x_talos_timestamp]):
        return None

    # 1. Freshness Check
    try:
        ts = int(x_talos_timestamp)
        now = int(time.time())
        if abs(now - ts) > MAX_CLOCK_SKEW:
            raise HTTPException(status_code=401, detail={"error": {"talos_code": "ATTESTATION_EXPIRED", "message": "Request timestamp too old or too far in future"}})
    except ValueError:
        raise HTTPException(status_code=401, detail={"error": {"talos_code": "INVALID_HEADERS", "message": "Invalid timestamp format"}})

    # 2. Key Lookup
    # Identifier check
    # Or just use the hex key id directly if we add it to mock store.
    # The plan says X-Talos-Key-Id is the hex-encoded public key.
    try:
        public_key_bytes = bytes.fromhex(x_talos_key_id)
        if len(public_key_bytes) != 32:
             raise ValueError("Key must be 32 bytes")
    except ValueError:
        raise HTTPException(status_code=401, detail={"error": {"talos_code": "INVALID_KEY", "message": "Invalid Key-Id format"}})

    # Check if key is known and not revoked
    key_hash = hashlib.sha256(public_key_bytes).hexdigest()
    # Note: For attestation, we might need a specific hashing rule, 
    # but the KeyStore's lookup_by_hash expects f"{pepper_id}:{hash}".
    # We should use the key_store's lookup mechanism.
    key_data = key_store.lookup_by_hash(key_hash)
    
    if not key_data:
        raise HTTPException(status_code=401, detail={"error": {"talos_code": "AUTH_INVALID", "message": "Unknown Key-Id"}})

    if key_data.revoked:
        raise HTTPException(status_code=401, detail={"error": {"talos_code": "AUTH_REVOKED", "message": "Key revoked"}})

    # 3. Replay Protection
    redis_client = await get_redis_client()
    if redis_client:
        nonce_key = f"a2a:nonce:{x_talos_key_id}:{x_talos_nonce}"
        # Use SETNX (set if not exists)
        is_new = await redis_client.set(nonce_key, "1", ex=NONCE_TTL, nx=True)
        if not is_new:
            raise HTTPException(status_code=401, detail={"error": {"talos_code": "REPLAY_ATTACK", "message": "Nonce already used"}})

    # 4. Signature Verification
    try:
        # Base64url padding check
        sig_b64 = x_talos_signature.replace("-", "+").replace("_", "/")
        padding = (4 - len(sig_b64) % 4) % 4
        sig_bytes = base64.b64decode(sig_b64 + "=" * padding)
    except Exception:
        raise HTTPException(status_code=401, detail={"error": {"talos_code": "INVALID_SIGNATURE", "message": "Invalid base64url signature"}})

    # Construct Payload: nonce | timestamp | canonical_json(body)
    raw_body = await request.body()
    try:
        body = json.loads(raw_body) if raw_body else {}
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail={"error": {"talos_code": "INVALID_JSON", "message": "Invalid JSON body"}})
        
    canonical_body = canonical_json_bytes(body)
    payload = f"{x_talos_nonce}|{x_talos_timestamp}|".encode() + canonical_body

    from talos_core_rs import Wallet
    if not Wallet.verify(payload, sig_bytes, public_key_bytes):
        raise HTTPException(status_code=401, detail={"error": {"talos_code": "INVALID_SIGNATURE", "message": "Signature verification failed"}})

    return AuthContext(
        key_id=key_data.id,
        team_id=key_data.team_id,
        org_id=key_data.org_id,
        scopes=key_data.scopes,
        allowed_model_groups=key_data.allowed_model_groups,
        allowed_mcp_servers=key_data.allowed_mcp_servers
    )
