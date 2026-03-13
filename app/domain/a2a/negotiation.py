from typing import List, Optional, Dict, Literal
from pydantic import BaseModel, Field

class UICapability(BaseModel):
    """Represents a specific UI schema or component the agent can render."""
    id: str
    version: str = "1.0"
    schema_uri: Optional[str] = None

class UXNegotiation(BaseModel):
    """Negotiation parameters for UI/UX interaction."""
    allowed_frame_ancestors: List[str] = Field(default_factory=list)
    ui_capabilities: List[UICapability] = Field(default_factory=list)
    supports_forms: bool = True
    supports_iframes: bool = True

class MultimediaCapability(BaseModel):
    """Negotiation for streaming modalities."""
    type: Literal["audio", "video"]
    codecs: List[str]
    max_bitrate: Optional[int] = None # in kbps

class NegotiationState(BaseModel):
    """The overall negotiated state for an A2A session."""
    ux: UXNegotiation = Field(default_factory=UXNegotiation)
    multimedia: List[MultimediaCapability] = Field(default_factory=list)
