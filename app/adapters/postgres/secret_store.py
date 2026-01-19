"""PostgresSecretStore - Database-backed secret storage with envelope encryption."""
import logging
from datetime import datetime, timezone
from typing import List, Optional, Dict, Any
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

    def set_secret(self, name: str, value: str) -> None:
        existing = self._db.query(Secret).filter(Secret.name == name).first()
        
        # Encrypt the value
        envelope = self._kek.encrypt(value.encode("utf-8"))

        if existing:
            existing.ciphertext = envelope.ciphertext
            existing.nonce = envelope.iv
            existing.tag = envelope.tag
            existing.key_id = envelope.kek_id
            existing.version += 1
            existing.rotated_at = datetime.now(timezone.utc)
            action = "rotate"
        else:
            secret = Secret(
                name=name,
                ciphertext=envelope.ciphertext,
                nonce=envelope.iv,
                tag=envelope.tag,
                key_id=envelope.kek_id,
                version=1,
                created_at=datetime.now(timezone.utc),
            )
            self._db.add(secret)
            action = "create"

        self._emit_audit(action, "secret", name)
        self._db.commit()

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

        try:
            envelope = EncryptedEnvelope(
                kek_id=secret.key_id,
                iv=secret.nonce,
                tag=secret.tag,
                ciphertext=secret.ciphertext
            )
            plaintext = self._kek.decrypt(envelope)
            return plaintext.decode("utf-8")
        except Exception as e:
            logger.error(f"Error decrypting secret {name}: {e}")
            return None

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
