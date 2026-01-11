from fastapi.testclient import TestClient
from app.main import app
import json
import base64

def test_handshake():
    print("Connecting to WebSocket...")
    client = TestClient(app)
    try:
        with client.websocket_connect("/v1/connect") as websocket:
            # 1. Send Handshake
            handshake = {
                "version": 1,
                "type": "HANDSHAKE",
                "payload": "eyJrZXkiOiJ2YWwifQ" # Base64url {"key":"val"}
            }
            websocket.send_text(json.dumps(handshake))
            print("Sent HANDSHAKE")
            
            # 2. Receive ACK
            data = websocket.receive_text()
            print(f"Received: {data}")
            response = json.loads(data)
            assert response["type"] == "HANDSHAKE_ACK"
            print("✅ Handshake Success")
            
            # 3. Send PING
            ping = {
                "version": 1,
                "type": "PING",
                "payload": "ping-payload"
            }
            websocket.send_text(json.dumps(ping))
            print("Sent PING")
            
            # 4. Receive PONG
            data = websocket.receive_text()
            print(f"Received: {data}")
            response = json.loads(data)
            assert response["type"] == "PONG"
            assert response["payload"] == "ping-payload"
            print("✅ Ping/Pong Success")
            
            # 5. Close
            close = {"version": 1, "type": "CLOSE", "payload": ""}
            websocket.send_text(json.dumps(close))
            print("Sent CLOSE")
    except Exception as e:
        print(f"Test failed: {e}")
        raise

if __name__ == "__main__":
    test_handshake()
