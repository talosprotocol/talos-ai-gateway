"""Secrets Domain Manager."""
import json
from pathlib import Path
from typing import List, Optional
from datetime import datetime

SECRETS_FILE = Path("config/secrets.json")
# In-memory cache
_SECRETS_CACHE = {}

def load_secrets():
    """Load secrets from file (mock encryption)."""
    global _SECRETS_CACHE
    if SECRETS_FILE.exists():
        try:
            with open(SECRETS_FILE) as f:
                _SECRETS_CACHE = json.load(f)
        except Exception as e:
            print(f"Error loading secrets: {e}")

def save_secrets():
    """Save secrets to file."""
    SECRETS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(SECRETS_FILE, "w") as f:
        json.dump(_SECRETS_CACHE, f, indent=2)

def list_secrets() -> List[dict]:
    """List secret metadata (no values)."""
    return [
        {"name": k, "created_at": v.get("created_at"), "type": "secret"}
        for k, v in _SECRETS_CACHE.items()
    ]

def set_secret(name: str, value: str):
    """Set a secret value."""
    _SECRETS_CACHE[name] = {
        "value": value,  # In prod, this would be encrypted
        "created_at": datetime.utcnow().isoformat()
    }
    save_secrets()

def delete_secret(name: str):
    """Delete a secret."""
    if name in _SECRETS_CACHE:
        del _SECRETS_CACHE[name]
        save_secrets()
        return True
    return False

def get_secret_value(name: str) -> Optional[str]:
    """Get raw secret value (internal only)."""
    secret = _SECRETS_CACHE.get(name)
    return secret["value"] if secret else None

load_secrets()
