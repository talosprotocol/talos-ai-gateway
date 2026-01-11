"""PostgresSecretStore - Database-backed secret storage with envelope encryption.

This module provides a production-ready secret store that:
1. Encrypts all secrets using AES-GCM envelope encryption
2. Stores encrypted values in PostgreSQL
3. Emits audit events for all mutations
4. Supports secret rotation with version tracking
"""
import base64
import uuid
from datetime import datetime, timezone
from typing import List, Optional

from sqlalchemy.orm import Session

from ..models import Secret, AuditEvent
from ...domain.secrets.kek_provider import get_kek_provider, EncryptedEnvelope


class PostgresSecretStore:
    """Database-backed secret storage with envelope encryption."""

    def __init__(self, db: Session, principal_id: Optional[str] = None):
        """Initialize store with database session.
        
        Args:
            db: SQLAlchemy database session
            principal_id: ID of principal performing operations (for audit)
        """
        self._db = db
        self._principal_id = principal_id or "system"
        self._kek = get_kek_provider()

    def list_secrets(self) -> List[dict]:
        """List secret metadata (no values returned)."""
        secrets = self._db.query(Secret).all()
        return [
            {
                "name": s.name,
                "key_id": s.key_id,
                "version": s.version,
                "created_at": s.created_at.isoformat() if s.created_at else None,
                "rotated_at": s.rotated_at.isoformat() if s.rotated_at else None,
            }
            for s in secrets
        ]

    def set_secret(self, name: str, value: str) -> None:
        """Create or update a secret with encryption.
        
        If secret exists, this performs a rotation.
        """
        existing = self._db.query(Secret).filter(Secret.name == name).first()
        
        if existing:
            self.rotate_secret(name, value)
            return

        # Encrypt the value
        envelope = self._kek.encrypt(value.encode("utf-8"))

        secret = Secret(
            name=name,
            ciphertext=base64.b64encode(envelope.ciphertext).decode("ascii"),
            nonce=base64.b64encode(envelope.nonce).decode("ascii"),
            key_id=envelope.key_id,
            version=1,
            created_at=datetime.now(timezone.utc),
        )
        self._db.add(secret)
        self._emit_audit("create", "secret", name)
        self._db.commit()

    def rotate_secret(self, name: str, new_value: str) -> bool:
        """Rotate a secret to a new value with fresh encryption.
        
        Returns True if rotation succeeded, False if secret not found.
        """
        secret = self._db.query(Secret).filter(Secret.name == name).first()
        if not secret:
            return False

        # Encrypt with potentially new KEK
        envelope = self._kek.encrypt(new_value.encode("utf-8"))

        secret.ciphertext = base64.b64encode(envelope.ciphertext).decode("ascii")
        secret.nonce = base64.b64encode(envelope.nonce).decode("ascii")
        secret.key_id = envelope.key_id
        secret.version = secret.version + 1
        secret.rotated_at = datetime.now(timezone.utc)

        self._emit_audit("rotate", "secret", name, {"version": secret.version})
        self._db.commit()
        return True

    def delete_secret(self, name: str) -> bool:
        """Delete a secret.
        
        Returns True if deleted, False if not found.
        """
        secret = self._db.query(Secret).filter(Secret.name == name).first()
        if not secret:
            return False

        self._db.delete(secret)
        self._emit_audit("delete", "secret", name)
        self._db.commit()
        return True

    def get_secret_value(self, name: str) -> Optional[str]:
        """Decrypt and return secret value (internal use only).
        
        WARNING: This returns the raw plaintext. Never expose via API.
        """
        secret = self._db.query(Secret).filter(Secret.name == name).first()
        if not secret:
            return None

        try:
            envelope = EncryptedEnvelope(
                ciphertext=base64.b64decode(secret.ciphertext),
                nonce=base64.b64decode(secret.nonce),
                key_id=secret.key_id,
            )
            plaintext = self._kek.decrypt(envelope)
            return plaintext.decode("utf-8")
        except Exception as e:
            print(f"Error decrypting secret {name}: {e}")
            return None

    def _emit_audit(
        self,
        action: str,
        resource_type: str,
        resource_id: str,
        details: Optional[dict] = None,
    ) -> None:
        """Emit an audit event for the operation."""
        event = AuditEvent(
            event_id=str(uuid.uuid4()),
            timestamp=datetime.now(timezone.utc),
            principal_id=self._principal_id,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            details=details or {},
            status="success",
        )
        self._db.add(event)
