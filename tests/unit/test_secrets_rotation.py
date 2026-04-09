import os
import base64
import pytest
from app.adapters.secrets.multi_provider import MultiKekProvider
from app.domain.secrets.models import EncryptedEnvelope

def b64u_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode('ascii').rstrip('=')

@pytest.fixture
def mock_env_keys(monkeypatch):
    key1 = os.urandom(32)
    key2 = os.urandom(32)
    monkeypatch.setenv("TALOS_KEK_v1", b64u_encode(key1))
    monkeypatch.setenv("TALOS_KEK_v2", b64u_encode(key2))
    monkeypatch.setenv("TALOS_CURRENT_KEK_ID", "v1")
    monkeypatch.setenv("DEV_MODE", "true")
    return key1, key2

def test_multi_kek_encrypt_decrypt(mock_env_keys):
    key1, _ = mock_env_keys
    provider = MultiKekProvider()
    
    plaintext = b"secret message"
    envelope = provider.encrypt(plaintext)
    
    assert envelope.kek_id == "v1"
    assert provider.is_stale(envelope.kek_id) is False
    
    decrypted = provider.decrypt(envelope)
    assert decrypted == plaintext

def test_multi_kek_rotation_parity(mock_env_keys, monkeypatch):
    key1, key2 = mock_env_keys
    provider = MultiKekProvider(current_kek_id="v1")
    
    plaintext = b"sensitive data"
    envelope_v1 = provider.encrypt(plaintext)
    
    # Simulate rotation
    monkeypatch.setenv("TALOS_CURRENT_KEK_ID", "v2")
    provider_v2 = MultiKekProvider()
    
    assert provider_v2.current_kek_id == "v2"
    assert provider_v2.is_stale("v1") is True
    assert provider_v2.is_stale("v2") is False
    
    # Can still decrypt old data
    decrypted = provider_v2.decrypt(envelope_v1)
    assert decrypted == plaintext
    
    # New encryption uses v2
    envelope_v2 = provider_v2.encrypt(plaintext)
    assert envelope_v2.kek_id == "v2"

def test_multi_kek_invalid_key_id():
    provider = MultiKekProvider()
    envelope = EncryptedEnvelope(
        kek_id="non-existent",
        nonce_b64u="abc",
        ciphertext_b64u="def",
        tag_b64u="ghi"
    )
    with pytest.raises(ValueError, match="unknown KEK"):
        provider.decrypt(envelope)
