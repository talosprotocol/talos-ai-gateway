from fastapi.testclient import TestClient
from app.main import app
import json
import base64
from talos.core.session import SessionManager, PrekeyBundle
from talos.core.crypto import generate_signing_keypair

def test_handshake():
    print("Connecting to WebSocket...")
    client = TestClient(app)
    
    # 1. Fetch Prekey
    resp = client.get("/v1/protocol/prekey")
    assert resp.status_code == 200
    bundle_data = resp.json()
    bundle = PrekeyBundle(
        identity_key=base64.urlsafe_b64decode(bundle_data["identity_key"] + "==="),
        signed_prekey=base64.urlsafe_b64decode(bundle_data["signed_prekey"] + "==="),
        prekey_signature=base64.urlsafe_b64decode(bundle_data["prekey_signature"] + "===")
    )
    print("✅ Fetched Prekey Bundle")

    # 2. Setup Alice Session
    kp = generate_signing_keypair()
    alice_manager = SessionManager(kp)
    alice_session = alice_manager.create_session_as_initiator("gateway", bundle)

    try:
        with client.websocket_connect("/v1/connect") as websocket:
            # 3. Send HANDSHAKE
            handshake = {
                "version": 1,
                "type": "HANDSHAKE",
                "payload": base64.urlsafe_b64encode(alice_session.state.dh_keypair.public_key).decode().rstrip("="),
                "nonce": base64.urlsafe_b64encode(alice_manager.identity_keypair.public_key).decode().rstrip("="),
                "session_id": "test-alice-1"
            }
            websocket.send_text(json.dumps(handshake))
            print("Sent HANDSHAKE")
            
            # 4. Receive HANDSHAKE_ACK
            data = websocket.receive_text()
            response = json.loads(data)
            assert response["type"] == "HANDSHAKE_ACK"
            session_id = response["session_id"]
            print(f"✅ Handshake Success, Session: {session_id}")
            
            # 5. Send Encrypted DATA (PING)
            request = {"method": "ping", "params": {"hello": "world"}}
            plaintext = json.dumps(request).encode()
            ciphertext = alice_session.encrypt(plaintext)
            
            data_frame = {
                "version": 1,
                "type": "DATA",
                "payload": base64.urlsafe_b64encode(ciphertext).decode().rstrip("="),
                "sequence": 0
            }
            websocket.send_text(json.dumps(data_frame))
            print("Sent Encrypted DATA")
            
            # 6. Receive Encrypted Response
            data = websocket.receive_text()
            resp_frame = json.loads(data)
            assert resp_frame["type"] == "DATA"
            
            resp_ciphertext = base64.urlsafe_b64decode(resp_frame["payload"] + "===")
            resp_plaintext = alice_session.decrypt(resp_ciphertext)
            resp_json = json.loads(resp_plaintext.decode())
            
            print(f"Received: {resp_json}")
            assert "error" in resp_json # Because 'ping' method is unknown but processed securely
            print("✅ Secure DATA Exchange Success")
            
    except Exception as e:
        print(f"Test failed: {e}")
        raise

if __name__ == "__main__":
    test_handshake()
