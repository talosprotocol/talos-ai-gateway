from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Header, Request, Response
from pydantic import BaseModel, Field

from app.api.a2a_v1.agent_card import build_agent_card as build_v1_agent_card
from app.settings import settings

# Constants
API_V1_STR = "/v1"
PROJECT_NAME = "Talos AI Gateway"

router = APIRouter()

# --- Pydantic Models matching schemas/a2a/agent_card.schema.json ---

class Profile(BaseModel):
    profile_id: str = "a2a-compat"
    profile_version: str = "0.1"
    spec_source: str = "a2a-protocol"

class UXCapabilities(BaseModel):
    supported_surfaces: List[str] = ["iframe"]

class MultimediaCapabilities(BaseModel):
    audio: bool = False
    video: bool = False

class Capabilities(BaseModel):
    chat: bool = True
    tools: bool = False
    history: bool = False
    ux: Optional[UXCapabilities] = None
    multimedia: Optional[MultimediaCapabilities] = None

class AuthConfig(BaseModel):
    type: str = "bearer"
    scheme: Optional[str] = None

class TalosExtension(BaseModel):
    supports_talos_attestation: bool = True
    supported_surfaces: List[str] = ["a2a", "mcp"]
    public_key: Optional[str] = None

class AgentCard(BaseModel):
    profile: Profile = Field(default_factory=Profile)
    name: str
    description: str
    endpoints: List[str]
    capabilities: Capabilities = Field(default_factory=Capabilities)
    auth: AuthConfig = Field(default_factory=AuthConfig)
    links: Optional[Dict[str, str]] = None
    onboarding: Optional[str] = None
    privacy_policy: Optional[str] = None
    x_talos: Optional[TalosExtension] = None


def build_compat_agent_card() -> Dict[str, Any]:
    return AgentCard(
        name=settings.a2a_agent_name,
        description=settings.a2a_agent_description,
        endpoints=[f"{API_V1_STR}/a2a"],
        capabilities=Capabilities(
            chat=True,
            tools=True,
            history=True,
        ),
        auth=AuthConfig(type="bearer"),
        x_talos=TalosExtension(
            supports_talos_attestation=True,
            supported_surfaces=["a2a"],
        ),
    ).model_dump(exclude_none=True)


# --- API ---

@router.get("/.well-known/agent-card.json")
@router.get("/.well-known/agent.json")
async def get_agent_card(
    request: Request,
    response: Response,
    auth_header: Optional[str] = Header(None, alias="Authorization"),
) -> Dict[str, Any]:
    """
    Returns the Agent Card for A2A discovery.
    Visibility controlled by A2A_AGENT_CARD_VISIBILITY setting.
    """
    visibility = settings.a2a_agent_card_visibility
    
    if visibility == "disabled":
        raise HTTPException(status_code=404, detail="Agent discovery disabled")
        
    if visibility == "auth_required":
        if not auth_header:
             raise HTTPException(status_code=401, detail="Authentication required for discovery")
        if not auth_header.startswith("Bearer "):
             raise HTTPException(status_code=401, detail="Invalid credentials")

    # Set Caching headers
    response.headers["Cache-Control"] = "max-age=60"
    
    if settings.a2a_protocol_mode in {"dual", "v1"}:
        return build_v1_agent_card(
            request,
            include_compat_extension=False,
        )

    return build_compat_agent_card()
