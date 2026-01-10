"""
SDK Integration Tests for talos-gateway.

Verifies that:
1. DI container is properly bootstrapped
2. SDK ports (audit, crypto, hash) are correctly registered
3. Crypto operations work with Ed25519 adapter
"""

import pytest
import sys
import os

# Add parent to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Skip all tests if talos_sdk is not installed
import importlib.util

SDK_AVAILABLE = importlib.util.find_spec("talos_sdk") is not None

pytestmark = pytest.mark.skipif(not SDK_AVAILABLE, reason="talos_sdk not installed")


class TestBootstrap:
    """Test DI container bootstrap."""

    def test_get_app_container_returns_container(self):
        """Container is created on first call."""
        from bootstrap import get_app_container

        container = get_app_container()
        assert container is not None

    def test_container_singleton(self):
        """Same container instance is reused."""
        from bootstrap import get_app_container

        c1 = get_app_container()
        c2 = get_app_container()
        assert c1 is c2

    def test_audit_store_registered(self):
        """IAuditStorePort is registered."""
        from bootstrap import get_app_container
        from talos_sdk.ports.audit_store import IAuditStorePort

        container = get_app_container()
        audit_store = container.resolve(IAuditStorePort)
        assert audit_store is not None

    def test_hash_port_registered(self):
        """IHashPort is registered."""
        from bootstrap import get_app_container
        from talos_sdk.ports.hash import IHashPort

        container = get_app_container()
        hash_port = container.resolve(IHashPort)
        assert hash_port is not None

    def test_crypto_port_registered(self):
        """ICryptoPort is registered."""
        from bootstrap import get_app_container
        from talos_sdk.ports.crypto import ICryptoPort

        container = get_app_container()
        crypto_port = container.resolve(ICryptoPort)
        assert crypto_port is not None


class TestCryptoPort:
    """Test cryptographic operations."""

    def test_sign_and_verify(self):
        """Crypto port can sign and verify data."""
        from bootstrap import get_app_container
        from talos_sdk.ports.crypto import ICryptoPort

        container = get_app_container()
        crypto = container.resolve(ICryptoPort)

        # Use a test private key (32 bytes seed)
        test_key = b"\x00" * 32
        message = b"test message"

        # Sign
        signature = crypto.sign(message, test_key)
        assert signature is not None
        assert len(signature) == 64  # Ed25519 signature

    def test_verify_valid_signature(self):
        """Verify returns True for valid signature."""
        from bootstrap import get_app_container
        from talos_sdk.ports.crypto import ICryptoPort
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

        container = get_app_container()
        crypto = container.resolve(ICryptoPort)

        # Generate key pair for test
        private_key = Ed25519PrivateKey.generate()
        public_key = private_key.public_key()

        message = b"test message"
        signature = private_key.sign(message)

        # Verify using crypto port
        public_bytes = public_key.public_bytes_raw()
        result = crypto.verify(message, signature, public_bytes)
        assert result is True

    def test_verify_invalid_signature(self):
        """Verify returns False for invalid signature."""
        from bootstrap import get_app_container
        from talos_sdk.ports.crypto import ICryptoPort
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

        container = get_app_container()
        crypto = container.resolve(ICryptoPort)

        private_key = Ed25519PrivateKey.generate()
        public_bytes = private_key.public_key().public_bytes_raw()

        message = b"test message"
        fake_signature = b"\x00" * 64  # Invalid signature

        result = crypto.verify(message, fake_signature, public_bytes)
        assert result is False


class TestHashPort:
    """Test hash port functionality."""

    def test_canonical_hash(self):
        """Hash port produces consistent hashes."""
        from bootstrap import get_app_container
        from talos_sdk.ports.hash import IHashPort

        container = get_app_container()
        hash_port = container.resolve(IHashPort)

        data = {"key": "value", "number": 42}
        hash1 = hash_port.canonical_hash(data)
        hash2 = hash_port.canonical_hash(data)

        assert hash1 == hash2
        assert len(hash1) == 32  # SHA-256 raw bytes


class TestAuditStore:
    """Test audit store operations."""

    def test_append_and_list(self):
        """Can append and list events."""
        from bootstrap import get_app_container
        from talos_sdk.ports.audit_store import IAuditStorePort

        container = get_app_container()
        store = container.resolve(IAuditStorePort)

        # Create a simple event-like object
        class SimpleEvent:
            event_id = "test-001"
            timestamp = 1234567890.0

        store.append(SimpleEvent())
        page = store.list(limit=10)

        assert isinstance(page.events, list)
