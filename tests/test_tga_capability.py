"""Test TGA Capability minting and validation using PyJWT with Ed25519."""
import json
import time
from uuid import uuid4
import jwt
import hashlib
from cryptography.hazmat.primitives.asymmetric import ed25519
from cryptography.hazmat.primitives import serialization

def generate_test_keys():
    """Generate Ed25519 key pair for testing."""
    private_key = ed25519.Ed25519PrivateKey.generate()
    public_key = private_key.public_key()
    
    priv_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption()
    ).decode('utf-8')
    
    pub_pem = public_key.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo
    ).decode('utf-8')
    
    return priv_pem, pub_pem, private_key

def mint_capability(private_key, constraints):
    """Mint a JWS capability using EdDSA."""
    trace_id = str(uuid4())
    plan_id = str(uuid4())
    now = int(time.time())
    
    payload = {
        "iss": "supervisor-test",
        "aud": "talos-gateway",
        "iat": now,
        "exp": now + 300,
        "nonce": str(uuid4()),
        "trace_id": trace_id,
        "plan_id": plan_id,
        "constraints": constraints
    }
    
    # PyJWT uses the private key object directly for EdDSA
    token = jwt.encode(payload, private_key, algorithm='EdDSA')
    return token, payload

def test_gateway_logic():
    """Test the TGA capability minting and validation flow."""
    priv_pem, pub_pem, private_key = generate_test_keys()
    
    # 1. Valid Read-Only Capability
    constraints = {
        "tool_server": "mcp-github",
        "tool_name": "list-issues",
        "target_allowlist": ["talosprotocol/*"],
        "arg_constraints": None,
        "read_only": True
    }
    
    token, payload = mint_capability(private_key, constraints)
    digest = hashlib.sha256(token.encode('utf-8')).hexdigest()
    
    print(f"Token: {token[:50]}...")
    print(f"Digest: {digest}")
    print(f"Public Key (PEM):\n{pub_pem}")
    
    # Verify the token can be decoded
    from cryptography.hazmat.primitives.serialization import load_pem_public_key
    pub_key = load_pem_public_key(pub_pem.encode('utf-8'))
    decoded = jwt.decode(token, pub_key, algorithms=['EdDSA'], audience='talos-gateway')
    
    print(f"\nDecoded Payload:")
    print(json.dumps(decoded, indent=2, default=str))
    
    # Test Payload for Gateway
    tool_call = {
        "server_id": "mcp-github",
        "tool_name": "list-issues",
        "arguments": {"owner": "talosprotocol", "repo": "talos"},
        "capability_digest": digest
    }
    
    print("\nSimulated TGA Tool Call Payload:")
    print(json.dumps(tool_call, indent=2))
    print(f"Header X-Talos-Capability: {token[:50]}...")
    
    print("\n✓ TGA Capability test passed!")

if __name__ == "__main__":
    test_gateway_logic()
