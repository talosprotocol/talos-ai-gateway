from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Depends
from app.api.talos_protocol.models import Frame, FrameType
from app.dependencies import get_session_store, get_key_store, get_protocol_session_manager, get_mcp_client
from app.domain.interfaces import SessionStore
from app.adapters.postgres.key_store import KeyStore
from app.adapters.redis.client import get_redis_client
from talos.core.session import SessionManager, PrekeyBundle, RatchetState
from talos.core.crypto import KeyPair
from app.utils.id import uuid7
import json
import logging
import base64
import time
from typing import Any, Dict

logger = logging.getLogger(__name__)

router = APIRouter()

@router.get("/v1/protocol/prekey")
async def get_prekey_bundle(
    manager: SessionManager = Depends(get_protocol_session_manager)
):
    """Get the Gateway's prekey bundle for X3DH handshake."""
    bundle = manager.get_prekey_bundle()
    return {
        "identity_key": base64.urlsafe_b64encode(bundle.identity_key).decode().rstrip("="),
        "signed_prekey": base64.urlsafe_b64encode(bundle.signed_prekey).decode().rstrip("="),
        "prekey_signature": base64.urlsafe_b64encode(bundle.prekey_signature).decode().rstrip("="),
    }

@router.websocket("/v1/connect")
async def websocket_endpoint(
    websocket: WebSocket,
    store: SessionStore = Depends(get_session_store),
    manager: SessionManager = Depends(get_protocol_session_manager),
    mcp_client: Any = Depends(get_mcp_client)
):
    await websocket.accept()
    session_id = None
    session_obj = None
    
    try:
        # 1. HANDSHAKE (X3DH Responder)
        data = await websocket.receive_text()
        frame = Frame.model_validate_json(data)
        
        if frame.type != FrameType.HANDSHAKE:
            await websocket.close(code=1002, reason="Expected HANDSHAKE")
            return

        try:
            # Initiator's ephemeral public key is in frame.payload
            # Initiator's identity is in frame.nonce
            ephemeral_public = base64.urlsafe_b64decode(frame.payload + "===")
            initiator_identity = base64.urlsafe_b64decode(frame.nonce + "===")
            
            session_obj = manager.create_session_as_responder(
                peer_id=frame.session_id or "initiator",
                peer_dh_public=ephemeral_public,
                peer_identity=initiator_identity
            )
            session_id = str(uuid7())
        except Exception as e:
            logger.warning(f"Handshake failed: {e}")
            await websocket.close(code=1008, reason="Handshake failed")
            return

        # Send HANDSHAKE_ACK
        ack = Frame(
            version=1,
            type=FrameType.HANDSHAKE_ACK,
            session_id=session_id,
            payload="OK"
        )
        await websocket.send_text(ack.model_dump_json())
        
        # 2. Secure DATA Loop
        while True:
            data = await websocket.receive_text()
            frame = Frame.model_validate_json(data)
            
            if frame.type == FrameType.PING:
                pong = Frame(type=FrameType.PONG, session_id=session_id, payload=frame.payload)
                await websocket.send_text(pong.model_dump_json())
            elif frame.type == FrameType.CLOSE:
                await websocket.close()
                break
            elif frame.type == FrameType.DATA:
                # Decrypt DATA frame using Double Ratchet
                try:
                    ciphertext = base64.urlsafe_b64decode(frame.payload + "===")
                    plaintext = session_obj.decrypt(ciphertext)
                    
                    # Process Request
                    request = json.loads(plaintext.decode())
                    method = request.get("method")
                    params = request.get("params", {})
                    
                    logger.info(f"Protocol: Received secure {method}")
                    
                    result = {}
                    if method == "list_tools":
                        server_id = params.get("server_id")
                        result = {"tools": await mcp_client.list_tools(server_id)}
                    elif method == "get_tool_schema":
                        server_id = params.get("server_id")
                        tool_name = params.get("tool_name")
                        result = {"json_schema": await mcp_client.get_tool_schema(server_id, tool_name)}
                    elif method == "call_tool":
                        server_id = params.get("server_id")
                        tool_name = params.get("tool_name")
                        arguments = params.get("arguments", {})
                        result = {"output": await mcp_client.call_tool(server_id, tool_name, arguments)}
                    else:
                        result = {"error": "Unknown method"}

                    # Encrypt Response
                    encrypted_response = session_obj.encrypt(json.dumps(result).encode())
                    
                    resp_frame = Frame(
                        type=FrameType.DATA,
                        session_id=session_id,
                        sequence=frame.sequence,
                        payload=base64.urlsafe_b64encode(encrypted_response).decode().rstrip("=")
                    )
                    await websocket.send_text(resp_frame.model_dump_json())
                    
                except Exception as e:
                    logger.error(f"Secure processing failed: {e}")
                    await websocket.close(code=1008, reason="Processing failure")
                    break
                
    except WebSocketDisconnect:
        logger.info(f"Client disconnected: {session_id}")
    except Exception as e:
        logger.error(f"Protocol error: {e}")
