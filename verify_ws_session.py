import sys
import os
import json
from fastapi.testclient import TestClient

# Ensure app in path
sys.path.append(os.getcwd())

from app.api.talos_protocol.models import Frame, FrameType
from app.main import app

def test_ws_session():
    # Force DEV_MODE for simpler verification without required Redis dependency (though it works with both)
    os.environ["DEV_MODE"] = "true"
    
    client = TestClient(app)
    print("\n--- Test 1: Replay Protection ---")
    try:
        with client.websocket_connect("/v1/connect") as websocket:
            # 1. Send Handshake
            handshake = Frame(type=FrameType.HANDSHAKE, payload="mypublickey")
            websocket.send_text(handshake.model_dump_json())
            
            # 2. Receive Handshake Ack
            ack = Frame(**json.loads(websocket.receive_text()))
            session_id = ack.session_id
            print(f"Handshake successful. Session ID: {session_id}")
            
            # 3. Send seq=1 & 2
            for i in [1, 2]:
                ping = Frame(type=FrameType.PING, session_id=session_id, sequence=i, payload=f"p{i}")
                websocket.send_text(ping.model_dump_json())
                websocket.receive_text()
                print(f"Ping {i} accepted.")
            
            # 4. Replay seq=1
            print("Sending replay (seq=1)...")
            replay = Frame(type=FrameType.PING, session_id=session_id, sequence=1, payload="replay")
            websocket.send_text(replay.model_dump_json())
            
            try:
                websocket.receive_text()
                print("[FAILURE] Replay was accepted!")
            except Exception:
                print("[SUCCESS] Connection closed as expected on replay.")
    except Exception as e:
        print(f"[ERROR] Test 1 failed: {e}")

    print("\n--- Test 2: Out of Order Protection ---")
    try:
        with client.websocket_connect("/v1/connect") as websocket:
            # 1. Handshake
            handshake = Frame(type=FrameType.HANDSHAKE, payload="mypublickey2")
            websocket.send_text(handshake.model_dump_json())
            session_id = Frame(**json.loads(websocket.receive_text())).session_id
            
            # 2. Send seq=1
            ping1 = Frame(type=FrameType.PING, session_id=session_id, sequence=1, payload="p1")
            websocket.send_text(ping1.model_dump_json())
            websocket.receive_text()
            
            # 3. Send seq=3 (skip 2)
            print("Sending out-of-order (seq=3)...")
            ping3 = Frame(type=FrameType.PING, session_id=session_id, sequence=3, payload="p3")
            websocket.send_text(ping3.model_dump_json())
            
            try:
                websocket.receive_text()
                print("[FAILURE] Out-of-order was accepted!")
            except Exception:
                print("[SUCCESS] Connection closed as expected on out-of-order.")
    except Exception as e:
        print(f"[ERROR] Test 2 failed: {e}")

if __name__ == "__main__":
    test_ws_session()
