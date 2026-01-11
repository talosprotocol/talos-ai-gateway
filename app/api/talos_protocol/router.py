from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Depends
from app.api.talos_protocol.models import Frame, FrameType
from app.dependencies import get_session_store
from app.domain.interfaces import SessionStore
import json
import uuid
import logging

logger = logging.getLogger(__name__)

router = APIRouter()

@router.websocket("/v1/connect")
async def websocket_endpoint(
    websocket: WebSocket,
    store: SessionStore = Depends(get_session_store)
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
            
        # Implementation Plan 3.2: Handshake with session creation
        # Extract public key from payload (mocked for now)
        public_key = frame.payload # In real scenario, payload is JSON with pk
        session_id = str(uuid.uuid4())
        
        await store.create_session(session_id, public_key)
        
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
