import json
import time
from uuid import uuid4
from jose import jws
import hashlib
from cryptography.hazmat.primitives.asymmetric import ed25519
from cryptography.hazmat.primitives import serialization

def generate_test_keys():
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
    
    return priv_pem, pub_pem

def mint_capability(priv_pem, constraints):
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
    
    token = jws.sign(payload, priv_pem, algorithm='EdDSA')
    return token, payload

def test_gateway_logic():
    priv, pub = generate_test_keys()
    
    # 1. Valid Read-Only Capability
    constraints = {
        "tool_server": "mcp-github",
        "tool_name": "list-issues",
        "target_allowlist": ["talosprotocol/*"],
        "arg_constraints": None,
        "read_only": True
    }
    
    token, payload = mint_capability(priv, constraints)
    digest = hashlib.sha256(token.encode('utf-8')).hexdigest()
    
    print(f"Token: {token[:20]}...")
    print(f"Digest: {digest}")
    print(f"Public Key (PEM):\n{pub}")
    
    # Test Payload
    tool_call = {
        "server_id": "mcp-github",
        "tool_name": "list-issues",
        "arguments": {"owner": "talosprotocol", "repo": "talos"},
        "capability_digest": digest
    }
    
    print("\nSimulated TGA Tool Call Payload:")
    print(json.dumps(tool_call, indent=2))
    print(f"Header X-Talos-Capability: {token[:20]}...")

if __name__ == "__main__":
    test_gateway_logic()
