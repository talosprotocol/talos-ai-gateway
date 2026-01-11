from fastapi import APIRouter, Depends, HTTPException, Header, Response
from pydantic import BaseModel, Field
from typing import List, Optional, Union, Dict, Any

from app.settings import settings
from app.middleware.auth_public import get_auth_context, AuthContext

# Constants
API_V1_STR = "/v1"
PROJECT_NAME = "Talos AI Gateway"

router = APIRouter()

# --- Pydantic Models matching schemas/a2a/agent_card.schema.json ---

class Profile(BaseModel):
    profile_id: str = "a2a-compat"
    profile_version: str = "0.1"
    spec_source: str = "a2a-protocol"

class Capabilities(BaseModel):
    chat: bool = True
    tools: bool = False
    history: bool = False

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
    x_talos: Optional[TalosExtension] = None

# --- API ---

@router.get("/.well-known/agent-card.json", response_model=AgentCard, response_model_exclude_none=True)
@router.get("/.well-known/agent.json", response_model=AgentCard, response_model_exclude_none=True)
async def get_agent_card(
    response: Response,
    auth_header: Optional[str] = Header(None, alias="Authorization")
):
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
        # Validate token using the middleware logic
        # We manually call get_auth_context here or rely on exception
        try:
             # get_auth_context parses "Bearer <token>"
             await get_auth_context(authorization=auth_header)
        except HTTPException as e:
             raise e
        except Exception:
             raise HTTPException(status_code=401, detail="Invalid credentials")

    # Set Caching headers
    response.headers["Cache-Control"] = "max-age=60"
    
    # Determine capabilities
    # TODO: Check settings.MCP_ENABLED if available
    
    return AgentCard(
        name=PROJECT_NAME,
        description="A Talos-secured AI Agent Gateway",
        endpoints=[f"{API_V1_STR}/a2a"], # Base URL for A2A
        capabilities=Capabilities(
            chat=True,
            tools=True, 
            history=True # We support tasks.get
        ),
        auth=AuthConfig(type="bearer"),
        x_talos=TalosExtension(
            supports_talos_attestation=True,
            supported_surfaces=["a2a"], # Explicitly just a2a for now, mcp is separate endpoint
            # public_key=settings.GATEWAY_PUBLIC_KEY 
        )
    )
