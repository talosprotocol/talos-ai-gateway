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
        cid = current_kek_id or os.getenv("TALOS_CURRENT_KEK_ID")
        if not cid:
            # We must fail fast if we can't determine the current KEK ID
            # But to keep __init__ safe, we can defer to validate_startup, 
            # OR we enforce it here. Given "Production Grade", failing fast is better.
            # However, existing code called _validate_startup.
            # To satisfy mypy property 'str' return, we must ensure it is str.
            # If we allow it to be empty temporarily, property is unsafe.
            # Let's Initialize as empty string but validation will fail.
            self._current_kek_id: str = ""
        else:
            self._current_kek_id = cid
            
        self._load_keys()
        self._validate_startup()

    def _load_keys(self) -> None:
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
                    raise RuntimeError(f"CRITICAL: Failed to load KEK {kek_id}") from e



    def _validate_startup(self) -> None:
        """Ensure the provider is in a valid state for operation."""
        if not self._current_kek_id:
            raise RuntimeError("CRITICAL: TALOS_CURRENT_KEK_ID must be set in production.")

        if self._current_kek_id not in self._keys:
            raise RuntimeError(f"CRITICAL: Current KEK ID '{self._current_kek_id}' is not loaded.")

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
        
        current_aad: Optional[bytes] = None
        
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
