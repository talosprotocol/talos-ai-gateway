"""Secrets Domain Ports (Interfaces)."""
from abc import ABC, abstractmethod
from typing import List, Optional, Dict, Any
from .models import EncryptedEnvelope

class KekProvider(ABC):
    """Abstract Port for Key Encryption Key providers."""

    @abstractmethod
    def encrypt(self, plaintext: bytes, aad: Optional[bytes] = None) -> EncryptedEnvelope:
        """Encrypt plaintext using AES-GCM with optional AAD."""
        ...

    @abstractmethod
    def decrypt(self, envelope: EncryptedEnvelope, aad: Optional[bytes] = None) -> bytes:
        """Decrypt envelope using AES-GCM with optional AAD."""
        ...

    @property
    @abstractmethod
    def current_kek_id(self) -> str:
        """ID of the current KEK used for new encryptions."""
        ...

    @property
    @abstractmethod
    def loaded_kek_ids(self) -> List[str]:
        """IDs of all currently loaded KEKs."""
        ...

class SecretStore(ABC):
    """Abstract Port for secret persistence."""

    @abstractmethod
    def list_secrets(self) -> List[Dict[str, Any]]:
        """List secret metadata (no values returned)."""
        ...

    @abstractmethod
    def get_secret_value(self, name: str) -> Optional[str]:
        """Decrypt and return secret value."""
        ...

    @abstractmethod
    def set_secret(self, name: str, value: str, expected_kek_id: Optional[str] = None) -> bool:
        """Create or update a secret with encryption. Returns True if successful."""
        ...

    @abstractmethod
    def delete_secret(self, name: str) -> bool:
        """Delete a secret."""
        ...

    @abstractmethod
    def get_stale_counts(self) -> Dict[str, int]:
        """Return counts of secrets per KEK ID."""
        ...

    @abstractmethod
    def get_secrets_batch(self, batch_size: int, cursor: Optional[str] = None) -> List[Dict[str, Any]]:
        """Fetch a batch of secrets for rotation, ordered by name."""
        ...
