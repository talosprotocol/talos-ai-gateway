from enum import Enum
from typing import Optional
from pydantic import BaseModel, Field

class FrameType(str, Enum):
    HANDSHAKE = "HANDSHAKE"
    HANDSHAKE_ACK = "HANDSHAKE_ACK"
    DATA = "DATA"
    PING = "PING"
    PONG = "PONG"
    CLOSE = "CLOSE"

class Frame(BaseModel):
    version: int = Field(default=1, description="Protocol version")
    type: FrameType
    session_id: Optional[str] = Field(default=None, description="Logical session ID")
    sequence: Optional[int] = Field(default=None, description="Monotonic sequence counter")
    nonce: Optional[str] = Field(default=None, description="Base64url nonce for handshake/attestation")
    timestamp: Optional[int] = Field(default=None, description="Unix timestamp")
    payload: str = Field(description="Base64url-encoded payload bytes")
    signature: Optional[str] = Field(default=None, description="Base64url signature")
    flags: int = 0
