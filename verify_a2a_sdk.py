#!/usr/bin/env python3
"""Phase 10.2 SDK Verification Script.

Verifies that the Phase 10.2 SDK updates work correctly:
- API path correctness
- Ratchet state serialization
- Session create/accept/rotate with ratchet state
- Frame encryption/decryption

Does NOT require running Gateway (can run standalone).
"""

import asyncio
import sys
from pathlib import Path

# Add SDK to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "sdks/python/src"))

from talos_sdk import Wallet
from talos_sdk.a2a import RatchetFrameCrypto
from talos_sdk.crypto import generate_signing_keypair
from talos_sdk.session import SessionManager


def print_header(text: str):
    print(f"\n{'='*60}")
    print(f"  {text}")
    print(f"{'='*60}")


def print_success(text: str):
    print(f"  ✅ {text}")


def print_info(text: str):
    print(f"  ℹ️  {text}")


def print_error(text: str):
    print(f"  ❌ {text}")


async def main():
    print_header("Phase 10.2 SDK Verification")
    
    # =========================================================================
    # Test 1: Ratchet State Serialization
    # =========================================================================
    print_header("Test 1: Ratchet State Serialization")
    
    alice_keypair = generate_signing_keypair()
    alice_manager = SessionManager(alice_keypair)
    
    bob_keypair = generate_signing_keypair()
    bob_manager = SessionManager(bob_keypair)
    
    # X3DH key exchange
    bob_bundle = bob_manager.get_prekey_bundle()
    alice_ratchet = alice_manager.create_session_as_initiator("bob", bob_bundle)
    bob_ratchet = bob_manager.create_session_as_responder(
        "alice", alice_ratchet.state.dh_keypair.public_key
    )
    
    # Create crypto adapters
    alice_crypto = RatchetFrameCrypto(alice_ratchet)
    bob_crypto = RatchetFrameCrypto(bob_ratchet)
    
    # Test get_ratchet_state()
    try:
        ratchet_blob, ratchet_digest = alice_crypto.get_ratchet_state()
        assert isinstance(ratchet_blob, str), "Ratchet blob must be string"
        assert isinstance(ratchet_digest, str), "Ratchet digest must be string"
        assert len(ratchet_digest) == 64, "Ratchet digest must be SHA-256 (64 hex chars)"
        assert "=" not in ratchet_blob, "Ratchet blob must not contain padding"
        print_success(f"Ratchet state serialization works")
        print_info(f"Blob length: {len(ratchet_blob)} chars")
        print_info(f"Digest: {ratchet_digest[:16]}...")
    except Exception as e:
        print_error(f"Ratchet state serialization failed: {e}")
        return False
    
    # =========================================================================
    # Test 2: Encrypt/Decrypt with Ratchet Crypto
    # =========================================================================
    print_header("Test 2: Encrypt/Decrypt with Ratchet Crypto")
    
    plaintext = b"Hello from Phase 10.2! A2A channels are working."
    
    try:
        # Alice encrypts
        header_b64u, ciphertext_b64u, ciphertext_hash = alice_crypto.encrypt(plaintext)
        
        assert isinstance(header_b64u, str), "Header must be string"
        assert isinstance(ciphertext_b64u, str), "Ciphertext must be string"
        assert isinstance(ciphertext_hash, str), "Ciphertext hash must be string"
        assert len(ciphertext_hash) == 64, "Ciphertext hash must be SHA-256"
        assert "=" not in header_b64u, "Header must not contain padding"
        assert "=" not in ciphertext_b64u, "Ciphertext must not contain padding"
        
        print_success("Encryption successful")
        print_info(f"Plaintext: {len(plaintext)} bytes")
        print_info(f"Ciphertext: {len(ciphertext_b64u)} b64u chars")
        print_info(f"Ciphertext hash: {ciphertext_hash[:16]}...")
        
        # Bob decrypts
        decrypted = bob_crypto.decrypt(header_b64u, ciphertext_b64u, ciphertext_hash)
        
        assert decrypted == plaintext, "Decryption must recover original plaintext"
        print_success(f"Decryption successful: {decrypted.decode()}")
        
    except Exception as e:
        print_error(f"Encrypt/decrypt failed: {e}")
        return False
    
    # =========================================================================
    # Test 3: API Path Correctness
    # =========================================================================
    print_header("Test 3: API Path Correctness")
    
    from talos_sdk.a2a import transport
    import inspect
    
    # Check transport methods use correct paths
    transport_source = inspect.getsource(transport.A2ATransport)
    
    issues = []
    if "/a2a/v1/" in transport_source:
        issues.append("Found old /a2a/v1/ paths (should be /a2a/)")
    
    # Check for correct paths
    correct_paths = [
        '"/a2a/sessions"',
        '"/a2a/groups"',
    ]
    
    for path in correct_paths:
        if path not in transport_source:
            issues.append(f"Missing expected path: {path}")
    
    if issues:
        for issue in issues:
            print_error(issue)
        return False
    else:
        print_success("All API paths use /a2a/* (no /v1/)")
        print_info("Transport layer correctly updated")
    
    # =========================================================================
    # Test 4: Session Create/Accept/Rotate Signatures
    # =========================================================================
    print_header("Test 4: Session Method Signatures")
    
    from talos_sdk.a2a import A2ATransport
    import inspect
    
    # Check create_session signature
    create_sig = inspect.signature(A2ATransport.create_session)
    create_params = list(create_sig.parameters.keys())
    
    if "ratchet_state_blob_b64u" not in create_params:
        print_error("create_session missing ratchet_state_blob_b64u parameter")
        return False
    if "ratchet_state_digest" not in create_params:
        print_error("create_session missing ratchet_state_digest parameter")
        return False
    
    print_success("create_session has ratchet state parameters")
    
    # Check accept_session signature
    accept_sig = inspect.signature(A2ATransport.accept_session)
    accept_params = list(accept_sig.parameters.keys())
    
    if "ratchet_state_blob_b64u" not in accept_params:
        print_error("accept_session missing ratchet_state_blob_b64u parameter")
        return False
    if "ratchet_state_digest" not in accept_params:
        print_error("accept_session missing ratchet_state_digest parameter")
        return False
    
    print_success("accept_session has ratchet state parameters")
    
    # Check rotate_session signature
    rotate_sig = inspect.signature(A2ATransport.rotate_session)
    rotate_params = list(rotate_sig.parameters.keys())
    
    if "ratchet_state_blob_b64u" not in rotate_params:
        print_error("rotate_session missing ratchet_state_blob_b64u parameter")
        return False
    if "ratchet_state_digest" not in rotate_params:
        print_error("rotate_session missing ratchet_state_digest parameter")
        return False
    
    print_success("rotate_session has ratchet state parameters")
    
    # =========================================================================
    # Summary
    # =========================================================================
    print_header("Verification Summary")
    
    print_success("All Phase 10.2 SDK updates verified!")
    print("")
    print("  Verified components:")
    print("    ✓ Ratchet state serialization (get_ratchet_state)")
    print("    ✓ Frame encryption/decryption")
    print("    ✓ API paths updated (/a2a/* not /a2a/v1/*)")
    print("    ✓ Session methods include ratchet state parameters")
    print("")
    print_info("SDK is ready for Gateway integration testing")
    print("")
    print("  Next step: Run with live Gateway")
    print("    1. Start Gateway: cd services/ai-gateway && docker compose up")
    print("    2. Run: python examples/a2a_live_integration.py")
    print("")
    
    return True


if __name__ == "__main__":
    result = asyncio.run(main())
    sys.exit(0 if result else 1)
