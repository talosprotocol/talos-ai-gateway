"""PostgresSecretStore - Database-backed secret storage with envelope encryption."""
import logging
import base64
from datetime import datetime, timezone
from typing import List, Optional, Dict, Any
from app.utils.id import uuid7
from sqlalchemy.orm import Session
from app.adapters.postgres.models import Secret, AuditEvent
from app.domain.secrets.ports import SecretStore, KekProvider
from app.domain.secrets.models import EncryptedEnvelope

logger = logging.getLogger(__name__)

class PostgresSecretStore(SecretStore):
    """Database-backed secret storage with envelope encryption."""

    def __init__(self, db: Session, kek_provider: KekProvider, principal_id: Optional[str] = None):
        """Initialize store.
        
        Args:
            db: SQLAlchemy database session
            kek_provider: Port for encryption/decryption
            principal_id: ID of principal performing operations (for audit)
        """
        self._db = db
        self._kek = kek_provider
        self._principal_id = principal_id or "system"

    def list_secrets(self) -> List[Dict[str, Any]]:
        secrets = self._db.query(Secret).all()
        return [
            {
                "name": s.name,
                "key_id": s.key_id,
                "version": s.version,
                "created_at": s.created_at.isoformat() if s.created_at else None,
                "rotated_at": s.rotated_at.isoformat() if s.rotated_at else None,
            }
            # to_dict helper usually available, but we'll build manually for stability
            for s in secrets
        ]

    def set_secret(self, name: str, value: str, expected_kek_id: Optional[str] = None) -> bool:
        query = self._db.query(Secret).filter(Secret.name == name)
        if expected_kek_id:
            query = query.filter(Secret.key_id == expected_kek_id)
            
        existing = query.first()
        
        # If expected_kek_id was provided but not found, return False (CAS failure)
        if expected_kek_id and not existing:
            return False

        # Stable ID for AAD binding
        secret_id = existing.id if existing else str(uuid7())
        # Rule: talos.secret.v1:<id>
        aad = f"talos.secret.v1:{secret_id}".encode("utf-8")
        
        envelope = self._kek.encrypt(value.encode("utf-8"), aad=aad)

        if existing:
            existing.ciphertext = envelope.ciphertext_b64u
            existing.nonce = envelope.nonce_b64u
            existing.tag = envelope.tag_b64u
            existing.aad = envelope.aad_b64u
            existing.key_id = envelope.kek_id
            existing.version += 1
            existing.rotated_at = datetime.now(timezone.utc)
            action = "rotate"
        else:
            secret = Secret(
                id=secret_id,
                name=name,
                ciphertext=envelope.ciphertext_b64u,
                nonce=envelope.nonce_b64u,
                tag=envelope.tag_b64u,
                aad=envelope.aad_b64u,
                key_id=envelope.kek_id,
                version=1,
                created_at=datetime.now(timezone.utc),
            )
            self._db.add(secret)
            action = "create"

        self._emit_audit(action, "secret", name, details={"secret_id": secret_id})
        self._db.commit()
        return True

    def delete_secret(self, name: str) -> bool:
        secret = self._db.query(Secret).filter(Secret.name == name).first()
        if not secret:
            return False

        self._db.delete(secret)
        self._emit_audit("delete", "secret", name)
        self._db.commit()
        return True

    def get_secret_value(self, name: str) -> Optional[str]:
        secret = self._db.query(Secret).filter(Secret.name == name).first()
        if not secret:
            return None

        # Dual-Read Strategy:
        # 1. Try v1 binding (talos.secret.v1:<id>)
        # 2. Try v0 binding (name)
        
        # Robust binary retrieval (handles hex legacy vs b64u)
        def robust_decode(s: str) -> str:
            # If it's hex, convert to b64u for the EncryptedEnvelope model
            if len(s) == 32 and all(c in "0123456789abcdefABCDEF" for c in s):
                try:
                    b = bytes.fromhex(s)
                    return base64.urlsafe_b64encode(b).decode('ascii').rstrip('=')
                except ValueError:
                    pass
            return s

        try:
            envelope = EncryptedEnvelope(
                kek_id=secret.key_id,
                nonce_b64u=robust_decode(secret.nonce),
                tag_b64u=robust_decode(secret.tag),
                ciphertext_b64u=secret.ciphertext,
                aad_b64u=secret.aad
            )
            
            # ATTEMPT 1: v1 binding
            try:
                aad_v1 = f"talos.secret.v1:{secret.id}".encode("utf-8")
                plaintext = self._kek.decrypt(envelope, aad=aad_v1)
                return plaintext.decode("utf-8")
            except ValueError as e:
                # DECRYPT_FAILED from KekProvider.decrypt
                if "DECRYPT_FAILED" not in str(e):
                    raise e
                # Fallthrough to legacy
            
            # ATTEMPT 2: v0 binding (legacy)
            aad_v0 = name.encode("utf-8")
            plaintext = self._kek.decrypt(envelope, aad=aad_v0)
            return plaintext.decode("utf-8")

        except Exception as e:
            logger.error(f"Error decrypting secret {name}: {e}")
            return None

    def get_stale_counts(self) -> Dict[str, int]:
        from sqlalchemy import func
        counts = self._db.query(Secret.key_id, func.count(Secret.name)).group_by(Secret.key_id).all()
        return {k: c for k, c in counts}

    def get_secrets_batch(self, batch_size: int, cursor: Optional[str] = None) -> List[Dict[str, Any]]:
        query = self._db.query(Secret).order_by(Secret.name)
        if cursor:
            query = query.filter(Secret.name > cursor)
        
        secrets = query.limit(batch_size).all()
        return [
            {
                "name": s.name,
                "key_id": s.key_id,
                "version": s.version
            }
            for s in secrets
        ]

    def _emit_audit(self, action: str, resource_type: str, resource_id: str, details: Optional[Dict] = None) -> None:
        event = AuditEvent(
            event_id=uuid7(),
            timestamp=datetime.now(timezone.utc),
            principal_id=self._principal_id,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            details=details or {},
            status="success",
        )
        self._db.add(event)
