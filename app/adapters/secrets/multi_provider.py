import os
import base64
import logging
import re
from typing import Dict, List, Optional
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from app.domain.secrets.ports import KekProvider
from app.domain.secrets.models import EncryptedEnvelope

logger = logging.getLogger(__name__)

class MultiKekProvider(KekProvider):
    """Production-grade Multi-KEK provider.
    
    Loads keys from TALOS_KEK_<ID> environment variables.
    Enforces AES-256 (32 bytes) and Base64URL (no padding) encoding.
    """

    def __init__(self, current_kek_id: Optional[str] = None):
        """Initialize with multiple keys.
        
        Args:
            current_kek_id: The ID of the primary key for new encryptions.
        """
        self._keys: Dict[str, AESGCM] = {}
        self._current_kek_id = current_kek_id or os.getenv("TALOS_CURRENT_KEK_ID")
        self._load_keys()
        self._validate_startup()

    def _load_keys(self):
        """Load all keys from the environment."""
        # 1. Load from TALOS_KEK_<ID>
        # Pattern ensures ID contains only alphanumeric characters, underscores, and hyphens.
        pattern = re.compile(r"^TALOS_KEK_([a-z0-9][a-z0-9_-]{0,31})$")
        for key, value in os.environ.items():
            match = pattern.match(key)
            if match:
                kek_id = match.group(1)
                try:
                    key_bytes = self._b64u_decode(value)
                    if len(key_bytes) != 32:
                        raise ValueError(f"KEK {kek_id} must be 32 bytes (AES-256). Got {len(key_bytes)}")
                    self._keys[kek_id] = AESGCM(key_bytes)
                    logger.info(f"Loaded KEK: {kek_id}")
                except Exception as e:
                    logger.error(f"Failed to load KEK {kek_id}: {e}")
                    if os.getenv("DEV_MODE", "false").lower() not in ("true", "1", "yes"):
                        raise RuntimeError(f"CRITICAL: Failed to load KEK {kek_id}") from e

        # 2. Legacy fallback for dev mode
        if "legacy" not in self._keys:
            master_key = os.getenv("TALOS_MASTER_KEY") or os.getenv("MASTER_KEY")
            if master_key:
                import hashlib
                key_bytes = hashlib.sha256(master_key.encode()).digest()
                self._keys["legacy"] = AESGCM(key_bytes)
                logger.warning("Loaded legacy KEK from TALOS_MASTER_KEY")

    def _validate_startup(self):
        """Ensure the provider is in a valid state for operation."""
        is_dev = os.getenv("DEV_MODE", "false").lower() in ("true", "1", "yes")
        
        if not self._current_kek_id:
            if not is_dev:
                raise RuntimeError("CRITICAL: TALOS_CURRENT_KEK_ID must be set in production.")
            # Dev fallback
            if "legacy" in self._keys:
                self._current_kek_id = "legacy"
            elif self._keys:
                self._current_kek_id = list(self._keys.keys())[0]
            else:
                # Absolute minimal fallback for dev if nothing is set
                import hashlib
                key_bytes = hashlib.sha256(b"dev-only").digest()
                self._keys["dev-insecure"] = AESGCM(key_bytes)
                self._current_kek_id = "dev-insecure"
                logger.warning("Using insecure DEV KEK. DO NOT USE IN PRODUCTION.")

        if self._current_kek_id not in self._keys:
            if not is_dev:
                raise RuntimeError(f"CRITICAL: Current KEK ID '{self._current_kek_id}' is not loaded.")
            else:
                logger.error(f"Current KEK ID '{self._current_kek_id}' not found in loaded keys.")

    def encrypt(self, plaintext: bytes, aad: Optional[bytes] = None) -> EncryptedEnvelope:
        """Encrypt plaintext using the current primary KEK."""
        if not self._current_kek_id or self._current_kek_id not in self._keys:
            raise RuntimeError(f"KEK_NOT_LOADED: Current KEK '{self._current_kek_id}' is unavailable.")
        
        kek = self._keys[self._current_kek_id]
        nonce = os.urandom(12)
        
        # AES-GCM encryption
        ct_and_tag = kek.encrypt(nonce, plaintext, aad)
        
        ciphertext = ct_and_tag[:-16]
        tag = ct_and_tag[-16:]

        return EncryptedEnvelope(
            kek_id=self._current_kek_id,
            nonce_b64u=self._b64u_encode(nonce),
            ciphertext_b64u=self._b64u_encode(ciphertext),
            tag_b64u=self._b64u_encode(tag),
            aad_b64u=self._b64u_encode(aad) if aad else None
        )

    def decrypt(self, envelope: EncryptedEnvelope, aad: Optional[bytes] = None) -> bytes:
        """Decrypt envelope using the specified KEK ID."""
        if envelope.kek_id not in self._keys:
            raise ValueError(f"KEK_NOT_LOADED: Envelope uses unknown KEK '{envelope.kek_id}'")
        
        kek = self._keys[envelope.kek_id]
        
        try:
            nonce = self._b64u_decode(envelope.nonce_b64u)
            ciphertext = self._b64u_decode(envelope.ciphertext_b64u)
            tag = self._b64u_decode(envelope.tag_b64u)
        except Exception as e:
            raise ValueError(f"ENVELOPE_INVALID: Failed to decode binary fields: {e}")
        
        # Verify AAD binding if present in envelope
        if envelope.aad_b64u:
            try:
                env_aad = self._b64u_decode(envelope.aad_b64u)
            except Exception as e:
                raise ValueError(f"ENVELOPE_INVALID: Failed to decode AAD: {e}")
                
            if aad is not None and env_aad != aad:
                 raise ValueError("DECRYPT_FAILED: AAD mismatch (binding violation)")
            
            # Use the AAD from envelope if none provided, but validation above ensures consistency
            current_aad = aad if aad is not None else env_aad
        else:
            current_aad = aad

        try:
            return kek.decrypt(nonce, ciphertext + tag, current_aad)
        except Exception as e:
            # Mask internal error to avoid leaking details, but log for debugging
            logger.debug(f"Decryption failed for KEK {envelope.kek_id}: {e}")
            raise ValueError("DECRYPT_FAILED: Authentication tag mismatch or key mismatch.")

    @property
    def current_kek_id(self) -> str:
        return self._current_kek_id

    @property
    def loaded_kek_ids(self) -> List[str]:
        return sorted(list(self._keys.keys()))

    def _b64u_encode(self, data: bytes) -> str:
        return base64.urlsafe_b64encode(data).decode('ascii').rstrip('=')

    def _b64u_decode(self, s: str) -> bytes:
        # Add padding back if necessary
        missing_padding = len(s) % 4
        if missing_padding:
            s += '=' * (4 - missing_padding)
        return base64.urlsafe_b64decode(s)
