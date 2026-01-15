"""Secrets Domain Ports (Interfaces)."""
from abc import ABC, abstractmethod
from typing import List, Optional, Dict, Any
from .models import EncryptedEnvelope

class KekProvider(ABC):
    """Abstract Port for Key Encryption Key providers."""

    @abstractmethod
    def encrypt(self, plaintext: bytes) -> EncryptedEnvelope:
        """Encrypt plaintext using AES-GCM."""
        ...

    @abstractmethod
    def decrypt(self, envelope: EncryptedEnvelope) -> bytes:
        """Decrypt envelope using AES-GCM."""
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
    def set_secret(self, name: str, value: str) -> None:
        """Create or update a secret with encryption."""
        ...

    @abstractmethod
    def delete_secret(self, name: str) -> bool:
        """Delete a secret."""
        ...
