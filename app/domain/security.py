"""Security Utilities for Secrets Encryption."""
import os
import base64
import hashlib
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

# Initial Master Key setup
MASTER_KEY = os.getenv("MASTER_KEY", "dev-master-key-change-in-prod")

def get_key() -> bytes:
    """Derive 32-byte key from MASTER_KEY string using SHA256."""
    return hashlib.sha256(MASTER_KEY.encode()).digest()

def encrypt_value(plaintext: str) -> str:
    """Encrypt plaintext using AES-GCM. Returns base64(nonce + ciphertext + tag)."""
    if not plaintext:
        return ""
    key = get_key()
    aesgcm = AESGCM(key)
    nonce = os.urandom(12)
    # encrypt returns ciphertext + tag
    ct = aesgcm.encrypt(nonce, plaintext.encode("utf-8"), None)
    return base64.b64encode(nonce + ct).decode("utf-8")

def decrypt_value(b64_data: str) -> str:
    """Decrypt base64(nonce + ciphertext + tag) string."""
    if not b64_data:
        return ""
    try:
        key = get_key()
        aesgcm = AESGCM(key)
        data = base64.b64decode(b64_data)
        if len(data) < 12:
            raise ValueError("Data too short")
        nonce = data[:12]
        ct = data[12:]
        plaintext = aesgcm.decrypt(nonce, ct, None)
        return plaintext.decode("utf-8")
    except Exception as e:
        # Avoid leaking details, but log if possible
        raise ValueError("Decryption failed") from e
