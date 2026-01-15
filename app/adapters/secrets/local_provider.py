"""Local KEK Provider Adapter."""
import os
import binascii
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from app.domain.secrets.ports import KekProvider
from app.domain.secrets.models import EncryptedEnvelope

class LocalKekProvider(KekProvider):
    """Production-ready KEK provider using a local master key.
    
    This provider derives a 256-bit AES key from the master secret.
    """

    def __init__(self, master_key: str, key_id: str = "v1"):
        """Initialize with master key.
        
        If master_key is 64 hex chars, it's used directly.
        Otherwise, it's hashed into a 32-byte key.
        """
        if len(master_key) == 64 and all(c in "0123456789abcdef" for c in master_key.lower()):
            self._key = binascii.unhexlify(master_key)
        else:
            import hashlib
            self._key = hashlib.sha256(master_key.encode()).digest()
        
        self._key_id = key_id
        self._aesgcm = AESGCM(self._key)

    def encrypt(self, plaintext: bytes) -> EncryptedEnvelope:
        iv = os.urandom(12)
        ct_and_tag = self._aesgcm.encrypt(iv, plaintext, None)
        
        ciphertext = ct_and_tag[:-16]
        tag = ct_and_tag[-16:]

        return EncryptedEnvelope(
            kek_id=self._key_id,
            iv=binascii.hexlify(iv).decode('ascii'),
            ciphertext=binascii.hexlify(ciphertext).decode('ascii'),
            tag=binascii.hexlify(tag).decode('ascii')
        )

    def decrypt(self, envelope: EncryptedEnvelope) -> bytes:
        if envelope.kek_id != self._key_id:
            raise ValueError(f"Key mismatch: Envelope uses {envelope.kek_id}, Provider has {self._key_id}")
        
        iv = binascii.unhexlify(envelope.iv)
        tag = binascii.unhexlify(envelope.tag)
        ciphertext = binascii.unhexlify(envelope.ciphertext)
        
        return self._aesgcm.decrypt(iv, ciphertext + tag, None)
