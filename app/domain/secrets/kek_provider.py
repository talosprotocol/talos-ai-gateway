"""Key Encryption Key (KEK) Provider Interface and Implementations.

This module provides production-grade envelope encryption primitives using AES-256-GCM.
"""
import os
import binascii
import base64
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional, Dict, Any

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

ALGORITHM_AES_256_GCM = "aes-256-gcm"
SCHEMA_ID_ENVELOPE = "talos.secrets.envelope"
SCHEMA_VERSION_V1 = "v1"

@dataclass
class EncryptedEnvelope:
    """
    Encrypted data envelope (Draft 2020-12 / Normative).
    
    Ensures structural integrity and metadata compliance for secrets-at-rest.
    All binary fields are stored as lowercase hex strings.
    """
    kek_id: str
    iv: str        # 24 hex char (12 bytes)
    ciphertext: str # Hex
    tag: str       # 32 hex char (16 bytes)
    aad: Optional[str] = None # Hex
    alg: str = ALGORITHM_AES_256_GCM
    schema_id: str = SCHEMA_ID_ENVELOPE
    schema_version: str = SCHEMA_VERSION_V1
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z")

    def __post_init__(self):
        self.validate()

    def validate(self):
        """Validate structure against normative rules."""
        if not re.match(r"^[0-9a-f]{24}$", self.iv):
            raise ValueError(f"Invalid IV: must be 24 hex characters. Got len={len(self.iv)}")
        if not re.match(r"^[0-9a-f]{32}$", self.tag):
            raise ValueError(f"Invalid Tag: must be 32 hex characters. Got len={len(self.tag)}")
        if not re.match(r"^[0-9a-f]+$", self.ciphertext):
            raise ValueError("Invalid Ciphertext: must be a hex string.")
        if self.alg != ALGORITHM_AES_256_GCM:
            raise ValueError(f"Unsupported algorithm: {self.alg}")

    @staticmethod
    def _hex_to_b64u(hex_str: Optional[str]) -> Optional[str]:
        if not hex_str: return None
        return base64.urlsafe_b64encode(binascii.unhexlify(hex_str)).decode('ascii').rstrip('=')

    @property
    def ciphertext_b64u(self) -> str:
        return self._hex_to_b64u(self.ciphertext)  # type: ignore

    @property
    def nonce_b64u(self) -> str:
        return self._hex_to_b64u(self.iv)  # type: ignore
    
    @property
    def iv_b64u(self) -> str:
        return self._hex_to_b64u(self.iv)  # type: ignore

    @property
    def tag_b64u(self) -> str:
        return self._hex_to_b64u(self.tag)  # type: ignore

    @property
    def aad_b64u(self) -> Optional[str]:
        return self._hex_to_b64u(self.aad)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_id": self.schema_id,
            "schema_version": self.schema_version,
            "kek_id": self.kek_id,
            "iv": self.iv,
            "ciphertext": self.ciphertext,
            "tag": self.tag,
            "aad": self.aad,
            "alg": self.alg,
            "created_at": self.created_at
        }

    @staticmethod
    def from_dict(data: Dict[str, Any]) -> 'EncryptedEnvelope':
        return EncryptedEnvelope(
            kek_id=data["kek_id"],
            iv=data["iv"],
            ciphertext=data["ciphertext"],
            tag=data["tag"],
            alg=data.get("alg", ALGORITHM_AES_256_GCM),
            schema_id=data.get("schema_id", SCHEMA_ID_ENVELOPE),
            schema_version=data.get("schema_version", SCHEMA_VERSION_V1),
            created_at=data.get("created_at") or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
        )

class KekProvider(ABC):
    """Abstract interface for Key Encryption Key providers."""

    @property
    @abstractmethod
    def key_id(self) -> str:
        """Return the ID of the active key."""
        ...

    @abstractmethod
    def encrypt(self, plaintext: bytes, aad: Optional[bytes] = None) -> EncryptedEnvelope:
        """Encrypt plaintext using AES-GCM."""
        ...

    @abstractmethod
    def decrypt(self, envelope: EncryptedEnvelope, aad: Optional[bytes] = None) -> bytes:
        """Decrypt envelope using AES-GCM."""
        ...


class LocalKekProvider(KekProvider):
    """Production-ready KEK provider using a local master key.
    
    This provider derives a 256-bit AES key from the master secret.
    """

    def __init__(self, master_key: str, key_id: str = "v1"):
        """Initialize with master key.
        
        If master_key is 64 hex chars, it's used directly.
        Otherwise, it's hashed into a 32-byte key.
        """
        if len(master_key) == 64 and all(c in "0123456789abcdef" for c in master_key.lower()):
            self._key = binascii.unhexlify(master_key)
        else:
            import hashlib
            self._key = hashlib.sha256(master_key.encode()).digest()
        
        self._key_id = key_id
        self._aesgcm = AESGCM(self._key)

    @property
    def key_id(self) -> str:
        return self._key_id

    def encrypt(self, plaintext: bytes, aad: Optional[bytes] = None) -> EncryptedEnvelope:
        iv = os.urandom(12)
        ct_and_tag = self._aesgcm.encrypt(iv, plaintext, aad)
        
        ciphertext = ct_and_tag[:-16]
        tag = ct_and_tag[-16:]

        return EncryptedEnvelope(
            kek_id=self.key_id,
            iv=binascii.hexlify(iv).decode('ascii'),
            ciphertext=binascii.hexlify(ciphertext).decode('ascii'),
            tag=binascii.hexlify(tag).decode('ascii'),
            aad=binascii.hexlify(aad).decode('ascii') if aad else None
        )

    def decrypt(self, envelope: EncryptedEnvelope, aad: Optional[bytes] = None) -> bytes:
        if envelope.kek_id != self.key_id:
            raise ValueError(f"Key mismatch: Envelope uses {envelope.kek_id}, Provider has {self.key_id}")
        
        iv = binascii.unhexlify(envelope.iv)
        tag = binascii.unhexlify(envelope.tag)
        ciphertext = binascii.unhexlify(envelope.ciphertext)
        
        return self._aesgcm.decrypt(iv, ciphertext + tag, aad)


class MultiKekProvider(KekProvider):
    """Facade for supporting key rotation with multiple active keys."""
    
    def __init__(self, primary: KekProvider, secondaries: Dict[str, KekProvider]):
        self.primary = primary
        self.secondaries = secondaries

    @property
    def key_id(self) -> str:
        return self.primary.key_id

    def encrypt(self, plaintext: bytes, aad: Optional[bytes] = None) -> EncryptedEnvelope:
        # Always encrypt with primary (latest) version
        return self.primary.encrypt(plaintext, aad)

    def decrypt(self, envelope: EncryptedEnvelope, aad: Optional[bytes] = None) -> bytes:
        # Try primary first
        if envelope.kek_id == self.primary.key_id:
            return self.primary.decrypt(envelope, aad)
        
        # Try secondaries
        if provider := self.secondaries.get(envelope.kek_id):
            return provider.decrypt(envelope, aad)
            
        raise ValueError(f"No provider found for KEK ID {envelope.kek_id}")


def get_kek_provider() -> KekProvider:
    """Factory function to get the active KEK provider."""
    # Production preference: Load master key from secure env
    master_key = os.getenv("TALOS_MASTER_KEY")
    key_id = os.getenv("TALOS_KEK_ID", "v1")

    # In a real scenario, we would load secondary keys from env vars like TALOS_MASTER_KEY_V1, etc.
    # For now, we assume simple single-key operation or manual rotation config.
    # To support rotation, one would inject:
    # TALOS_MASTER_KEY_OLD_1=<key>
    # TALOS_KEK_ID_OLD_1=<id>
    
    if not master_key:
        # Fallback to legacy MASTER_KEY or dev default
        master_key = os.getenv("MASTER_KEY")
        if not master_key:
            if os.getenv("DEV_MODE", "false").lower() in ("true", "1", "yes"):
                master_key = "dev-master-key-change-in-prod"
            else:
                raise RuntimeError("CRITICAL: TALOS_MASTER_KEY must be set in production mode.")

    primary = LocalKekProvider(master_key, key_id)
    secondaries: Dict[str, KekProvider] = {}
    
    # Load up to 3 old keys
    for i in range(1, 4):
        old_key = os.getenv(f"TALOS_MASTER_KEY_OLD_{i}")
        old_id = os.getenv(f"TALOS_KEK_ID_OLD_{i}")
        if old_key and old_id:
            secondaries[old_id] = LocalKekProvider(old_key, old_id)

    if not secondaries:
        return primary
        
    return MultiKekProvider(primary, secondaries)
