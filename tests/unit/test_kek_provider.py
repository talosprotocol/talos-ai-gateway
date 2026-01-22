"""Tests for KEK Provider and Secrets Manager."""
import pytest


def test_local_kek_provider_encrypt_decrypt():
    """Test that LocalKekProvider can encrypt and decrypt."""
    from app.domain.secrets.kek_provider import LocalKekProvider

    provider = LocalKekProvider("test-master-key", key_id="env-dev-key-v1")
    plaintext = b"super-secret-api-key"

    envelope = provider.encrypt(plaintext)

    assert envelope.ciphertext != plaintext.hex()
    assert len(envelope.iv) == 24  # 12 bytes = 24 hex chars
    assert envelope.kek_id == "env-dev-key-v1"

    decrypted = provider.decrypt(envelope)
    assert decrypted == plaintext


def test_envelop_deterministic_key_derivation():
    """Test that same master key produces same encryption key."""
    from app.domain.secrets.kek_provider import LocalKekProvider

    provider1 = LocalKekProvider("same-key")
    provider2 = LocalKekProvider("same-key")

    plaintext = b"test-data"
    envelope = provider1.encrypt(plaintext)

    # Different provider with same key should decrypt
    decrypted = provider2.decrypt(envelope)
    assert decrypted == plaintext


def test_wrong_key_fails_decryption():
    """Test that wrong key fails to decrypt."""
    from app.domain.secrets.kek_provider import LocalKekProvider
    from cryptography.exceptions import InvalidTag

    provider1 = LocalKekProvider("key-one")
    provider2 = LocalKekProvider("key-two", key_id="v1")  # Same key_id but wrong master key

    plaintext = b"sensitive"
    envelope = provider1.encrypt(plaintext)

    with pytest.raises(InvalidTag):
        provider2.decrypt(envelope)


def test_key_id_mismatch_raises():
    """Test that key_id mismatch raises ValueError."""
    from app.domain.secrets.kek_provider import LocalKekProvider, EncryptedEnvelope

    provider = LocalKekProvider("test-key", key_id="v1")
    plaintext = b"data"
    envelope = provider.encrypt(plaintext)

    # Create envelope with wrong key_id
    wrong_envelope = EncryptedEnvelope(
        kek_id="v2",
        iv=envelope.iv,
        ciphertext=envelope.ciphertext,
        tag=envelope.tag
    )

    with pytest.raises(ValueError, match="Key mismatch"):
        provider.decrypt(wrong_envelope)


def test_get_kek_provider_dev_mode(monkeypatch):
    """Test that get_kek_provider behaves as expected in dev mode."""
    from app.domain.secrets.kek_provider import get_kek_provider, LocalKekProvider

    monkeypatch.setenv("DEV_MODE", "true")
    # Should use default dev key
    monkeypatch.delenv("TALOS_MASTER_KEY", raising=False)
    monkeypatch.delenv("MASTER_KEY", raising=False)

    provider = get_kek_provider()
    assert isinstance(provider, LocalKekProvider)
    # Validate it works implicitly


def test_get_kek_provider_prod_mode_fails(monkeypatch):
    """Test that get_kek_provider fails in prod mode without MASTER_KEY."""
    from app.domain.secrets.kek_provider import get_kek_provider

    monkeypatch.setenv("DEV_MODE", "false")
    monkeypatch.delenv("TALOS_MASTER_KEY", raising=False)
    monkeypatch.delenv("MASTER_KEY", raising=False)

    with pytest.raises(RuntimeError, match="TALOS_MASTER_KEY must be set"):
        get_kek_provider()
