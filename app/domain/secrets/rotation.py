"""Secret Rotation Service.

This module provides logic for re-wrapping secrets when a KEK is rotated.
"""
import logging
from typing import Optional, Tuple, Dict, Any
from app.domain.secrets.ports import SecretStore, KekProvider
from app.domain.secrets.models import EncryptedEnvelope

logger = logging.getLogger(__name__)

class RotationService:
    """Service for managing secret rotation and re-wrapping."""

    def __init__(self, secret_store: SecretStore, kek_provider: KekProvider):
        self.secret_store = secret_store
        self.kek_provider = kek_provider

    def rotate_batch(self, batch_size: int = 100, cursor: Optional[str] = None) -> Tuple[int, Optional[str], int, int]:
        """
        Rotates a batch of secrets to the latest KEK.
        
        Args:
            batch_size: Max secrets to process in this batch.
            cursor: The name of the last secret processed in the previous batch.
            
        Returns:
            A tuple of (rotated_count, last_secret_name, scanned_count, failed_count).
        """
        secrets = self.secret_store.get_secrets_batch(batch_size, cursor)
        if not secrets:
            return 0, None, 0, 0
        
        rotated_count = 0
        failed_count = 0
        scanned_count = len(secrets)
        last_name = None
        current_kek_id = self.kek_provider.current_kek_id

        for s in secrets:
            name = s["name"]
            old_kek_id = s["key_id"]
            last_name = name

            # Skip if already using the latest KEK
            if old_kek_id == current_kek_id:
                continue

            try:
                # Decryption logic in store handles finding the old KEK
                plaintext = self.secret_store.get_secret_value(name)
                if plaintext:
                    # Use CAS (Compare-and-Swap) to ensure we don't overwrite concurrent updates
                    success = self.secret_store.set_secret(name, plaintext, expected_kek_id=old_kek_id)
                    if success:
                        rotated_count += 1
                        logger.info(f"Rotated secret: {name} ({old_kek_id} -> {current_kek_id})")
                    else:
                        failed_count += 1
                        logger.warning(f"CAS failure for secret {name}: stale KEK ID {old_kek_id}")
                else:
                    failed_count += 1
                    logger.warning(f"Could not decrypt secret {name} for rotation.")
            except Exception as e:
                failed_count += 1
                logger.error(f"Failed to rotate secret {name}: {e}")
        
        return rotated_count, last_name, scanned_count, failed_count

    def rotate_all(self) -> int:
        """Deprecated: Use batch-based rotation for production safety."""
        rotated_total = 0
        cursor = None
        while True:
            rotated, cursor, scanned = self.rotate_batch(batch_size=100, cursor=cursor)
            rotated_total += rotated
            if scanned < 100:
                break
        return rotated_total
