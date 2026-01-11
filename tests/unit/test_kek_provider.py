"""Tests for KEK Provider and Secrets Manager."""
import pytest
import os


def test_env_kek_provider_encrypt_decrypt():
    """Test that EnvKekProvider can encrypt and decrypt."""
    from app.domain.secrets.kek_provider import EnvKekProvider

    provider = EnvKekProvider("test-master-key")
    plaintext = b"super-secret-api-key"

    envelope = provider.encrypt(plaintext)

    assert envelope.ciphertext != plaintext
    assert len(envelope.nonce) == 12  # 96-bit nonce
    assert envelope.key_id == "env-dev-key-v1"

    decrypted = provider.decrypt(envelope)
    assert decrypted == plaintext


def test_envelop_deterministic_key_derivation():
    """Test that same master key produces same encryption key."""
    from app.domain.secrets.kek_provider import EnvKekProvider

    provider1 = EnvKekProvider("same-key")
    provider2 = EnvKekProvider("same-key")

    plaintext = b"test-data"
    envelope = provider1.encrypt(plaintext)

    # Different provider with same key should decrypt
    decrypted = provider2.decrypt(envelope)
    assert decrypted == plaintext


def test_wrong_key_fails_decryption():
    """Test that wrong key fails to decrypt."""
    from app.domain.secrets.kek_provider import EnvKekProvider
    from cryptography.exceptions import InvalidTag

    provider1 = EnvKekProvider("key-one")
    provider2 = EnvKekProvider("key-two", key_id="env-dev-key-v1")  # Same key_id but wrong key

    plaintext = b"sensitive"
    envelope = provider1.encrypt(plaintext)

    with pytest.raises(InvalidTag):
        provider2.decrypt(envelope)


def test_key_id_mismatch_raises():
    """Test that key_id mismatch raises ValueError."""
    from app.domain.secrets.kek_provider import EnvKekProvider, EncryptedEnvelope

    provider = EnvKekProvider("test-key", key_id="v1")
    plaintext = b"data"
    envelope = provider.encrypt(plaintext)

    # Create envelope with wrong key_id
    wrong_envelope = EncryptedEnvelope(
        ciphertext=envelope.ciphertext,
        nonce=envelope.nonce,
        key_id="v2"
    )

    with pytest.raises(ValueError, match="Key ID mismatch"):
        provider.decrypt(wrong_envelope)


def test_production_provider_fails_fast():
    """Test that ProductionKekProvider fails on instantiation."""
    from app.domain.secrets.kek_provider import ProductionKekProvider

    with pytest.raises(NotImplementedError, match="Production KEK provider not configured"):
        ProductionKekProvider()


def test_get_kek_provider_dev_mode(monkeypatch):
    """Test that get_kek_provider returns EnvKekProvider in dev mode."""
    from app.domain.secrets.kek_provider import get_kek_provider, EnvKekProvider

    monkeypatch.setenv("DEV_MODE", "true")
    monkeypatch.setenv("MASTER_KEY", "test-key")

    provider = get_kek_provider()
    assert isinstance(provider, EnvKekProvider)


def test_get_kek_provider_prod_mode_fails(monkeypatch):
    """Test that get_kek_provider fails in prod mode without KMS."""
    from app.domain.secrets.kek_provider import get_kek_provider

    monkeypatch.setenv("DEV_MODE", "false")
    monkeypatch.delenv("TALOS_KMS_PROVIDER", raising=False)

    with pytest.raises(RuntimeError, match="TALOS_KMS_PROVIDER must be set"):
        get_kek_provider()
