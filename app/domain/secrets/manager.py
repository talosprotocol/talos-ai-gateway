"""Secrets Domain Manager.

This module manages secrets with proper encryption at rest.
It orchestrates between KekProvider (Port) and SecretStore (Port).
"""
from typing import List, Optional, Dict, Any
from .ports import KekProvider, SecretStore
from .models import EncryptedEnvelope

class SecretsManager:
    """Domain Service for Secrets Management."""

    def __init__(self, kek_provider: KekProvider, secret_store: SecretStore):
        self.kek_provider = kek_provider
        self.secret_store = secret_store

    def list_secrets(self) -> List[Dict[str, Any]]:
        """List secret metadata (no values returned)."""
        return self.secret_store.list_secrets()

    def set_secret(self, name: str, value: str):
        """Set a secret value with AES-GCM encryption."""
        # The SecretStore.set_secret already handles encryption in its current implementation,
        # but to be strictly hexagonal, the Domain Service should decide the encryption.
        # However, many implementations put encryption inside the Store for 'transparency'.
        # We'll follow the pattern where the Service explicitly use the KekProvider.
        # Wait, if SecretStore already has a kek_provider injected, it's redundant.
        # Let's make SecretStore a pure storage Port, and SecretsManager handle logic.
        
        # NOTE: Current PostgresSecretStore implementation ALREADY uses kek_provider.
        # We will keep it that way for now but ensure it follows the Port interface.
        self.secret_store.set_secret(name, value)

    def get_secret_value(self, name: str) -> Optional[str]:
        """Get decrypted secret value."""
        return self.secret_store.get_secret_value(name)

    def delete_secret(self, name: str) -> bool:
        """Delete a secret."""
        return self.secret_store.delete_secret(name)
