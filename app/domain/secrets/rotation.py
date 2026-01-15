"""Secret Rotation Service.

This module provides logic for re-wrapping secrets when a KEK is rotated.
"""
import logging
from typing import Optional
from app.domain.secrets.ports import SecretStore, KekProvider
from app.domain.secrets.models import EncryptedEnvelope

logger = logging.getLogger(__name__)

class RotationService:
    """Service for managing secret rotation and re-wrapping."""

    def __init__(self, secret_store: SecretStore, kek_provider: KekProvider):
        self.secret_store = secret_store
        self.kek_provider = kek_provider

    def rotate_all(self) -> int:
        """
        Re-encrypt all secrets using the current active KEK.
        
        This is used when a new master key is deployed. 
        It reads each secret, decrypts with whatever KEK it was using 
        (if the provider supports it), and re-encrypts with the current KEK.
        
        Returns:
            Number of secrets successfully rotated.
        """
        secrets = self.secret_store.list_secrets()
        count = 0
        
        for s_meta in secrets:
            name = s_meta["name"]
            try:
                # get_secret_value uses the current kek_provider to decrypt
                plaintext = self.secret_store.get_secret_value(name)
                if plaintext:
                    # set_secret will encrypt with the NEW KEK if the provider has been updated
                    self.secret_store.set_secret(name, plaintext)
                    count += 1
                    logger.info(f"Rotated secret: {name}")
                else:
                    logger.warning(f"Could not decrypt secret {name} for rotation.")
            except Exception as e:
                logger.error(f"Failed to rotate secret {name}: {e}")
        
        return count

    def rotate_single(self, name: str) -> bool:
        """Rotate a specific secret (e.g. periodically or on suspicion of leak)."""
        plaintext = self.secret_store.get_secret_value(name)
        if not plaintext:
            return False
        
        try:
            self.secret_store.set_secret(name, plaintext)
            return True
        except Exception:
            return False
