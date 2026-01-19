import os
import pytest
import base64
from app.adapters.secrets.multi_provider import MultiKekProvider
from app.domain.secrets.models import EncryptedEnvelope

def b64u_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode('ascii').rstrip('=')

def test_multi_kek_provider_aad(monkeypatch):
    # Setup keys in env
    k1 = b64u_encode(b"0" * 32)
    monkeypatch.setenv("TALOS_KEK_v1", k1)
    monkeypatch.setenv("TALOS_CURRENT_KEK_ID", "v1")
    
    provider = MultiKekProvider()
    
    plaintext = b"secret data"
    aad = b"secret-name-1"
    
    # 1. Successful Encrypt/Decrypt
    envelope = provider.encrypt(plaintext, aad=aad)
    assert envelope.kek_id == "v1"
    assert envelope.aad_b64u == b64u_encode(aad)
    
    decrypted = provider.decrypt(envelope, aad=aad)
    assert decrypted == plaintext
    
    # 2. AAD Binding Mismatch
    with pytest.raises(ValueError, match="AAD mismatch"):
        provider.decrypt(envelope, aad=b"secret-name-2")

def test_multi_kek_provider_key_rotation(monkeypatch):
    k1 = b64u_encode(b"1" * 32)
    k2 = b64u_encode(b"2" * 32)
    monkeypatch.setenv("TALOS_KEK_v1", k1)
    monkeypatch.setenv("TALOS_KEK_v2", k2)
    
    # Encrypt with v1
    p1 = MultiKekProvider(current_kek_id="v1")
    envelope = p1.encrypt(b"data", aad=b"id")
    
    # Decrypt with v2 (it should have both keys loaded)
    p2 = MultiKekProvider(current_kek_id="v2")
    decrypted = p2.decrypt(envelope, aad=b"id")
    assert decrypted == b"data"
    
    # New encryption with p2 uses v2
    envelope2 = p2.encrypt(b"data2", aad=b"id")
    assert envelope2.kek_id == "v2"

def test_multi_kek_provider_fail_closed(monkeypatch):
    monkeypatch.setenv("TALOS_CURRENT_KEK_ID", "missing-key")
    monkeypatch.setenv("DEV_MODE", "false")
    # Ensure no other TALOS_KEK_* are present that might satisfy it
    for k in list(os.environ.keys()):
        if k.startswith("TALOS_KEK_"):
            monkeypatch.delenv(k)
    
    with pytest.raises(RuntimeError, match="is not loaded"):
        MultiKekProvider()

def test_multi_kek_id_validation(monkeypatch):
    monkeypatch.setenv("TALOS_KEK_inv@lid", b64u_encode(b"3" * 32))
    monkeypatch.setenv("DEV_MODE", "true") # Allow fallback/relaxed startup for this test
    # The regex should skip this key
    provider = MultiKekProvider()
    assert "inv@lid" not in provider.loaded_kek_ids
