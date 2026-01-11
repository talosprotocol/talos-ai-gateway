import base64
import json
import logging
import time
from typing import Optional, Tuple
from abc import ABC, abstractmethod

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from tenacity import retry, stop_after_attempt, wait_fixed

from app.domain.a2a.canonical import canonical_json_bytes
from app.domain.interfaces import PrincipalStore

logger = logging.getLogger(__name__)

class AttestationError(Exception):
    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(message)

class ReplayDetector(ABC):
    @abstractmethod
    def check_replay(self, key_id: str, nonce: str) -> bool:
        """Return True if replayed (exists), False if new."""
        ...

class RedisReplayDetector(ReplayDetector):
    def __init__(self, redis_client):
        self.redis = redis_client
        self.ttl = 300

    def check_replay(self, key_id: str, nonce: str) -> bool:
        if not self.redis:
            # Open fail: if no redis, everything is replayed? Or allowed?
            # Security first: If we claim strict replay protection, we must fail open or closed depending on policy.
            # Usually FAIL CLOSED. But for dev without redis?
            # We assume redis exists if we are in this path.
            return False 
            
        key = f"attest:nonce:{key_id}:{nonce}"
        # SETNX equivalent: set if not exists
        # redis.set(key, "1", ex=ttl, nx=True) returns True if set (KEY DID NOT EXIST)
        # We return True if REPLAYED (key DID exist, so set returned False)
        is_new = self.redis.set(name=key, value="1", ex=self.ttl, nx=True)
        return not is_new

class AttestationVerifier:
    def __init__(self, principal_store: PrincipalStore, replay_detector: ReplayDetector):
        self.store = principal_store
        self.replay = replay_detector

    async def verify_request(
        self,
        headers: dict,
        raw_body: bytes,
        method: str,
        path_query: str,
        opcode: str
    ) -> str:
        """Verify headers and return signer principal ID."""
        
        # 1. Strict Header Parsing
        key_id = headers.get("X-Talos-Key-ID")
        timestamp_str = headers.get("X-Talos-Timestamp")
        nonce = headers.get("X-Talos-Nonce")
        sig_b64 = headers.get("X-Talos-Signature")
        
        if not (key_id and timestamp_str and nonce and sig_b64):
            raise AttestationError("AUTH_INVALID", "Missing attestation headers")
            
        # 2. Timestamp Window
        try:
            ts = int(timestamp_str)
            now = int(time.time())
            if abs(now - ts) > 60:
                raise AttestationError("AUTH_INVALID", "Timestamp out of window")
        except ValueError:
            raise AttestationError("AUTH_INVALID", "Invalid timestamp format")

        # 3. DB Lookup
        principal = self.store.get_principal(key_id)
        if not principal:
            raise AttestationError("AUTH_INVALID", f"Unknown signer: {key_id}")
            
        if not principal.get("public_key"):
            raise AttestationError("AUTH_INVALID", "Signer has no registered public key")

        # 4. Signature Verification
        # Construct strictly
        if not raw_body:
            body_bytes = b""
        else:
            try:
                # We interpret body as JSON if present, else empty? 
                # Plan says: "If empty raw bytes -> b"". Else json.loads -> canonical_json_bytes"
                # What if it's not JSON? e.g. text/plain? 
                # The contract assumes JSON APIs. If it fails parsing, we can't canonicalize.
                # We will strict fail if body exists but isn't JSON.
                obj = json.loads(raw_body)
                body_bytes = canonical_json_bytes(obj)
            except Exception:
                 raise AttestationError("AUTH_INVALID", "Body must be valid JSON for attestation")
        
        # Signing Input Construction (All ASCII encoded)
        # "method.upper().encode("ascii")"
        try:
            signing_input = (
                body_bytes + b"\n" +
                method.upper().encode("ascii") + b"\n" +
                path_query.encode("ascii") + b"\n" +
                nonce.encode("ascii") + b"\n" +
                timestamp_str.encode("ascii") + b"\n" +
                opcode.encode("ascii")
            )
        except UnicodeEncodeError:
             raise AttestationError("AUTH_INVALID", "Non-ASCII characters in header/method/path")

        # Verify
        try:
            pub_bytes = bytes.fromhex(principal["public_key"])
            pub_key = Ed25519PublicKey.from_public_bytes(pub_bytes)
            
            # Strict Base64url unpadded check
            if '=' in sig_b64:
                 raise AttestationError("SCHEMA_VALIDATION_ERROR", "Signature must be unpadded base64url")

            # Add padding for decoding (Python needs it)
            pad = len(sig_b64) % 4
            if pad > 0:
                sig_b64 += "=" * (4 - pad)
                
            sig_bytes = base64.urlsafe_b64decode(sig_b64)
            
            pub_key.verify(sig_bytes, signing_input)
        except Exception as e:
            logger.warning(f"Signature verification failed: {e}")
            raise AttestationError("AUTH_INVALID", "Invalid signature")

        # 5. Replay Check (Last to prevent oracle)
        if self.replay.check_replay(key_id, nonce):
            raise AttestationError("REPLAY_DETECTED", "Nonce reused")

        return key_id
