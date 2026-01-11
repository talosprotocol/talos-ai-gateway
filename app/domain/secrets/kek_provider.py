"""Key Encryption Key (KEK) Provider Interface and Implementations.

This module provides envelope encryption primitives using AES-GCM.
In production, the KEK must come from a KMS/Vault/HSM.
"""
import os
import secrets
from abc import ABC, abstractmethod
from typing import NamedTuple


class EncryptedEnvelope(NamedTuple):
    """Encrypted data envelope with AES-GCM."""
    ciphertext: bytes
    nonce: bytes
    key_id: str


class KekProvider(ABC):
    """Abstract interface for Key Encryption Key providers."""

    @abstractmethod
    def get_key_id(self) -> str:
        """Return the current active key identifier."""
        ...

    @abstractmethod
    def encrypt(self, plaintext: bytes) -> EncryptedEnvelope:
        """Encrypt plaintext using AES-GCM."""
        ...

    @abstractmethod
    def decrypt(self, envelope: EncryptedEnvelope) -> bytes:
        """Decrypt ciphertext using AES-GCM."""
        ...


class EnvKekProvider(KekProvider):
    """DEV-ONLY KEK provider that reads key from environment variable.

    WARNING: This provider should ONLY be used when DEV_MODE=true.
    Production deployments must use a proper KMS/Vault/HSM.
    """

    def __init__(self, master_key: str, key_id: str = "env-dev-key-v1"):
        """Initialize with master key string.

        The master key is hashed to derive a 256-bit AES key.
        """
        import hashlib
        self._key = hashlib.sha256(master_key.encode()).digest()
        self._key_id = key_id

    def get_key_id(self) -> str:
        return self._key_id

    def encrypt(self, plaintext: bytes) -> EncryptedEnvelope:
        """Encrypt using AES-GCM with random 96-bit nonce."""
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM

        nonce = secrets.token_bytes(12)  # 96-bit nonce for AES-GCM
        aesgcm = AESGCM(self._key)
        ciphertext = aesgcm.encrypt(nonce, plaintext, None)

        return EncryptedEnvelope(
            ciphertext=ciphertext,
            nonce=nonce,
            key_id=self._key_id
        )

    def decrypt(self, envelope: EncryptedEnvelope) -> bytes:
        """Decrypt ciphertext using AES-GCM."""
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM

        if envelope.key_id != self._key_id:
            raise ValueError(f"Key ID mismatch: expected {self._key_id}, got {envelope.key_id}")

        aesgcm = AESGCM(self._key)
        return aesgcm.decrypt(envelope.nonce, envelope.ciphertext, None)


class ProductionKekProvider(KekProvider):
    """Placeholder for production KMS/Vault integration.

    This class will fail fast if instantiated without proper configuration,
    ensuring that production deployments cannot accidentally use mock keys.
    """

    def __init__(self):
        raise NotImplementedError(
            "Production KEK provider not configured. "
            "Set TALOS_KMS_PROVIDER environment variable to one of: "
            "aws_kms, gcp_kms, azure_keyvault, hashicorp_vault"
        )

    def get_key_id(self) -> str:
        raise NotImplementedError()

    def encrypt(self, plaintext: bytes) -> EncryptedEnvelope:
        raise NotImplementedError()

    def decrypt(self, envelope: EncryptedEnvelope) -> bytes:
        raise NotImplementedError()


def get_kek_provider() -> KekProvider:
    """Factory function to get the appropriate KEK provider.

    In DEV_MODE, uses environment variable for master key.
    In production, requires proper KMS configuration (fails fast otherwise).
    """
    dev_mode = os.getenv("DEV_MODE", "false").lower() in ("true", "1", "yes")

    if dev_mode:
        master_key = os.getenv("MASTER_KEY", "dev-master-key-change-in-prod")
        return EnvKekProvider(master_key)

    # Production mode - require proper KMS
    kms_provider = os.getenv("TALOS_KMS_PROVIDER")
    if not kms_provider:
        raise RuntimeError(
            "TALOS_KMS_PROVIDER must be set in production mode. "
            "Set DEV_MODE=true for development environments."
        )

    # Future: implement actual KMS providers
    return ProductionKekProvider()
