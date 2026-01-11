"""Secrets Domain Manager with AES-GCM Envelope Encryption.

This module manages secrets with proper encryption at rest.
Secrets are never stored in plaintext.
"""
import json
import base64
from pathlib import Path
from typing import List, Optional
from datetime import datetime, timezone

from .kek_provider import get_kek_provider, EncryptedEnvelope

SECRETS_FILE = Path("config/secrets.encrypted.json")
_SECRETS_CACHE: dict = {}
_KEK_PROVIDER = None


def _get_kek():
    """Lazy-load KEK provider."""
    global _KEK_PROVIDER
    if _KEK_PROVIDER is None:
        _KEK_PROVIDER = get_kek_provider()
    return _KEK_PROVIDER


def load_secrets():
    """Load encrypted secrets from file."""
    global _SECRETS_CACHE
    if SECRETS_FILE.exists():
        try:
            with open(SECRETS_FILE) as f:
                _SECRETS_CACHE = json.load(f)
        except Exception as e:
            print(f"Error loading secrets: {e}")


def save_secrets():
    """Save encrypted secrets to file."""
    SECRETS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(SECRETS_FILE, "w") as f:
        json.dump(_SECRETS_CACHE, f, indent=2)


def list_secrets() -> List[dict]:
    """List secret metadata (no values returned)."""
    return [
        {
            "name": k,
            "created_at": v.get("created_at"),
            "rotated_at": v.get("rotated_at"),
            "key_id": v.get("key_id"),
            "type": "secret"
        }
        for k, v in _SECRETS_CACHE.items()
    ]


def set_secret(name: str, value: str):
    """Set a secret value with AES-GCM encryption."""
    kek = _get_kek()
    envelope = kek.encrypt(value.encode("utf-8"))

    _SECRETS_CACHE[name] = {
        "ciphertext": base64.b64encode(envelope.ciphertext).decode("ascii"),
        "nonce": base64.b64encode(envelope.nonce).decode("ascii"),
        "tag": base64.b64encode(envelope.tag).decode("ascii"),
        "key_id": envelope.key_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "rotated_at": None
    }
    save_secrets()


def rotate_secret(name: str, new_value: str) -> bool:
    """Rotate a secret to a new value with new encryption."""
    if name not in _SECRETS_CACHE:
        return False

    old_entry = _SECRETS_CACHE[name]
    created_at = old_entry.get("created_at")

    kek = _get_kek()
    envelope = kek.encrypt(new_value.encode("utf-8"))

    _SECRETS_CACHE[name] = {
        "ciphertext": base64.b64encode(envelope.ciphertext).decode("ascii"),
        "nonce": base64.b64encode(envelope.nonce).decode("ascii"),
        "tag": base64.b64encode(envelope.tag).decode("ascii"),
        "key_id": envelope.key_id,
        "created_at": created_at,
        "rotated_at": datetime.now(timezone.utc).isoformat()
    }
    save_secrets()
    return True


def delete_secret(name: str) -> bool:
    """Delete a secret."""
    if name in _SECRETS_CACHE:
        del _SECRETS_CACHE[name]
        save_secrets()
        return True
    return False


def get_secret_value(name: str) -> Optional[str]:
    """Get decrypted secret value (internal use only).

    WARNING: This returns the raw secret value.
    Never expose this through any API endpoint.
    """
    secret = _SECRETS_CACHE.get(name)
    if not secret:
        return None

    try:
        kek = _get_kek()
        # Handle cases where tag might be missing from old data?
        # Since we are resetting, we assume fresh data.
        envelope = EncryptedEnvelope(
            ciphertext=base64.b64decode(secret["ciphertext"]),
            nonce=base64.b64decode(secret["nonce"]),
            tag=base64.b64decode(secret.get("tag", "")), # Safety get, though should be there
            key_id=secret["key_id"]
        )
        plaintext = kek.decrypt(envelope)
        return plaintext.decode("utf-8")
    except Exception as e:
        print(f"Error decrypting secret {name}: {e}")
        return None


# Load secrets on module import
load_secrets()
