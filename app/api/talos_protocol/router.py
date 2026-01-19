from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Depends
from app.api.talos_protocol.models import Frame, FrameType
from app.dependencies import get_session_store, get_key_store
from app.domain.interfaces import SessionStore
from app.adapters.postgres.key_store import KeyStore
from app.adapters.redis.client import get_redis_client
from talos_core_rs import Wallet
from app.utils.id import uuid7
import json
import logging
import base64
import time

logger = logging.getLogger(__name__)

router = APIRouter()

@router.websocket("/v1/connect")
async def websocket_endpoint(
    websocket: WebSocket,
    store: SessionStore = Depends(get_session_store),
    key_store: KeyStore = Depends(get_key_store)
):
    await websocket.accept()
    session_id = None
    
    try:
        # Expect HANDSHAKE
        data = await websocket.receive_text()
        try:
            frame_dict = json.loads(data)
            frame = Frame(**frame_dict)
        except Exception:
            await websocket.close(code=1002, reason="Invalid format")
            return
        
        if frame.type != FrameType.HANDSHAKE:
            await websocket.close(code=1002, reason="Expected HANDSHAKE")
            return

        # 1. Validate mandatory fields
        if not all([frame.signature, frame.nonce, frame.timestamp]):
             await websocket.close(code=1008, reason="Missing security fields")
             return

        # 2. Freshness Check
        now = int(time.time())
        if abs(now - frame.timestamp) > 60:
             await websocket.close(code=1008, reason="Clock skew exceeded")
             return

        # 3. Payload Binding & Verification
        # In HANDSHAKE, the 'payload' should contain the public key or some params
        # The signature is over: version | nonce | timestamp | type | payload
        try:
            # We assume payload is base64url encoded params
            # Standard handshake signature payload:
            sig_payload = f"{frame.version}|{frame.nonce}|{frame.timestamp}|{frame.type}|{frame.payload}".encode()
            
            sig_bytes = base64.urlsafe_b64decode(frame.signature + "===")
            
            # The 'payload' itself for handshake is the public key (as b64url)
            public_key_bytes = base64.urlsafe_b64decode(frame.payload + "===")
            if len(public_key_bytes) != 32:
                 raise ValueError("Invalid public key length")
            
            if not Wallet.verify(sig_payload, sig_bytes, public_key_bytes):
                raise ValueError("Signature verification failed")
        except Exception as e:
            logger.warning(f"Handshake signature failed: {e}")
            await websocket.close(code=1008, reason="Signature verification failed")
            return

        # 4. Replay Protection
        redis_client = await get_redis_client()
        if redis_client:
            nonce_key = f"protocol:nonce:{frame.payload}:{frame.nonce}"
            if not await redis_client.set(nonce_key, "1", ex=300, nx=True):
                await websocket.close(code=1008, reason="Replay detected")
                return

        # 5. Session Creation
        public_key_hex = public_key_bytes.hex()
        session_id = uuid7()
        await store.create_session(session_id, public_key_hex)
        
        # Send HANDSHAKE_ACK with session_id
        ack = Frame(
            version=1,
            type=FrameType.HANDSHAKE_ACK,
            session_id=session_id,
            payload="OK"
        )
        await websocket.send_text(ack.model_dump_json())
        
        while True:
            data = await websocket.receive_text()
            frame_dict = json.loads(data)
            frame = Frame(**frame_dict)
            
            # Global Frame Validation: Session & Sequence
            if frame.type in [FrameType.DATA, FrameType.PING]:
                if frame.session_id != session_id:
                    await websocket.close(code=1008, reason="Session ID mismatch")
                    break
                
                if frame.sequence is None:
                    await websocket.close(code=1008, reason="Missing sequence")
                    break
                
                # REPLAY PROTECTION: Validate sequence matches expected monotonic counter
                is_valid = await store.validate_sequence(session_id, frame.sequence)
                if not is_valid:
                    logger.warning(f"Replay detected: session={session_id} seq={frame.sequence}")
                    await websocket.close(code=1008, reason="Sequence invalid or replay")
                    break

            if frame.type == FrameType.PING:
                pong = Frame(
                    type=FrameType.PONG, 
                    session_id=session_id,
                    payload=frame.payload
                )
                await websocket.send_text(pong.model_dump_json())
            elif frame.type == FrameType.CLOSE:
                await websocket.close()
                break
            elif frame.type == FrameType.DATA:
                # Process data...
                print(f"Received DATA [{frame.sequence}]: {frame.payload[:50]}...")
                
    except WebSocketDisconnect:
        logger.info(f"Client disconnected: {session_id}")
    except Exception as e:
        logger.error(f"Protocol error: {e}")
        # await websocket.close(code=1011)
