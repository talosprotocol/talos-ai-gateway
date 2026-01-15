"""Key Encryption Key (KEK) Provider Interface and Implementations.

This module provides production-grade envelope encryption primitives using AES-256-GCM.
"""
import os
import binascii
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

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_id": self.schema_id,
            "schema_version": self.schema_version,
            "kek_id": self.kek_id,
            "iv": self.iv,
            "ciphertext": self.ciphertext,
            "tag": self.tag,
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
            created_at=data.get("created_at")
        )

class KekProvider(ABC):
    """Abstract interface for Key Encryption Key providers."""

    @abstractmethod
    def encrypt(self, plaintext: bytes) -> EncryptedEnvelope:
        """Encrypt plaintext using AES-GCM."""
        ...

    @abstractmethod
    def decrypt(self, envelope: EncryptedEnvelope) -> bytes:
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

    def encrypt(self, plaintext: bytes) -> EncryptedEnvelope:
        iv = os.urandom(12)
        ct_and_tag = self._aesgcm.encrypt(iv, plaintext, None)
        
        ciphertext = ct_and_tag[:-16]
        tag = ct_and_tag[-16:]

        return EncryptedEnvelope(
            kek_id=self._key_id,
            iv=binascii.hexlify(iv).decode('ascii'),
            ciphertext=binascii.hexlify(ciphertext).decode('ascii'),
            tag=binascii.hexlify(tag).decode('ascii')
        )

    def decrypt(self, envelope: EncryptedEnvelope) -> bytes:
        if envelope.kek_id != self._key_id:
            raise ValueError(f"Key mismatch: Envelope uses {envelope.kek_id}, Provider has {self._key_id}")
        
        iv = binascii.unhexlify(envelope.iv)
        tag = binascii.unhexlify(envelope.tag)
        ciphertext = binascii.unhexlify(envelope.ciphertext)
        
        return self._aesgcm.decrypt(iv, ciphertext + tag, None)


def get_kek_provider() -> KekProvider:
    """Factory function to get the active KEK provider."""
    # Production preference: Load master key from secure env
    master_key = os.getenv("TALOS_MASTER_KEY")
    key_id = os.getenv("TALOS_KEK_ID", "v1")

    if not master_key:
        # Fallback to legacy MASTER_KEY or dev default
        master_key = os.getenv("MASTER_KEY")
        if not master_key:
            if os.getenv("DEV_MODE", "false").lower() in ("true", "1", "yes"):
                master_key = "dev-master-key-change-in-prod"
            else:
                raise RuntimeError("CRITICAL: TALOS_MASTER_KEY must be set in production mode.")

    return LocalKekProvider(master_key, key_id)
