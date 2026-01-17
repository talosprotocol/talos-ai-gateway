
import logging
import time
import hashlib
from typing import Optional, Tuple
from sqlalchemy.orm import Session
from sqlalchemy import text
from app.adapters.postgres.models import SecretsKeyring
from app.domain.secrets.models import EncryptedEnvelope

logger = logging.getLogger(__name__)

class KeyManager:
    """Manages Key Encryption Keys (KEKs) lifecycle (Rotation, Versioning)."""

    def __init__(self, write_db: Session, read_db: Session, cache_client=None):
        self.write_db = write_db
        # read_db is passed but strict consistency requires write_db for metadata
        self.read_db = read_db 
        self.cache = cache_client # Redis client interface
        self.CACHE_KEY = "talos:kek:active"
        self.CACHE_TTL = 10 # Seconds (Strict Spec)

    def _advisory_lock(self) -> None:
        """Acquire lock on WRITE DB for key rotation."""
        # Lock ID: crc32('secrets_keyring') or fixed
        lock_id = 99887766 
        result = self.write_db.execute(
            text("SELECT pg_try_advisory_xact_lock(:id) AS acquired"),
            {"id": lock_id}
        ).fetchone()
        if not result or not result.acquired:
            raise ValueError("KEY_ROTATION_LOCK_CONTENTION")

    def get_active_key_id(self) -> Tuple[str, int]:
        """Get (kek_id, version). Caches result."""
        # 1. Try Cache
        if self.cache:
            try:
                cached = self.cache.get(self.CACHE_KEY)
                if cached:
                    parts = cached.decode().split(":")
                    if len(parts) == 2:
                        return parts[0], int(parts[1])
            except Exception as e:
                logger.warning(f"Cache get failed: {e}")

        # 2. Fetch from WRITE DB (Strict Consistency for Keys)
        # We always check write_db for keys to avoid using expired keys from replica lag
        keyring = self.write_db.query(SecretsKeyring).filter(
            SecretsKeyring.id == "default"
        ).first()

        if not keyring:
            # Initialize if missing
            return self._initialize_keyring()

        # 3. Update Cache
        if self.cache:
            try:
                val = f"{keyring.active_kek_id}:{keyring.version}"
                self.cache.setex(self.CACHE_KEY, self.CACHE_TTL, val)
            except Exception as e:
                logger.warning(f"Cache set failed: {e}")

        return keyring.active_kek_id, keyring.version

    def rotate_key(self, new_kek_id: str) -> None:
        """Rotate the active KEK. Uses Write DB + Advisory Lock."""
        self._advisory_lock()
        
        keyring = self.write_db.query(SecretsKeyring).filter(
            SecretsKeyring.id == "default"
        ).first()

        if not keyring:
            keyring = SecretsKeyring(id="default", active_kek_id=new_kek_id, version=1)
            self.write_db.add(keyring)
        else:
            keyring.active_kek_id = new_kek_id
            keyring.version += 1
        
        self.write_db.commit()
        
        # Invalidate Cache
        if self.cache:
            self.cache.delete(self.CACHE_KEY)
            
        logger.info(f"Rotated KEK to {new_kek_id} (v{keyring.version})")

    def _initialize_keyring(self) -> Tuple[str, int]:
        """Bootstrap the keyring if empty."""
        try:
            # Check again under lock if possible, but here we optimistically insert/ignore
            initial_id = "v1"
            # Using merge or check-then-set
            existing = self.write_db.query(SecretsKeyring).filter(SecretsKeyring.id == "default").first()
            if existing:
                return existing.active_kek_id, existing.version
                
            keyring = SecretsKeyring(id="default", active_kek_id=initial_id, version=1)
            self.write_db.add(keyring)
            self.write_db.commit()
            return initial_id, 1
        except Exception as e:
            self.write_db.rollback()
            # Likely race condition, retry fetch
            existing = self.write_db.query(SecretsKeyring).filter(SecretsKeyring.id == "default").first()
            if existing:
                return existing.active_kek_id, existing.version
            raise e
