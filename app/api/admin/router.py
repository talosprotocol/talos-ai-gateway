"""Admin API Router - RBAC Protected."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import List, Optional, Dict

from app.middleware.auth_admin import get_rbac_context, require_permission, RbacContext
from app.domain.mcp import registry as mcp_registry

router = APIRouter()

# --- Models ---
class McpServerCreate(BaseModel):
    id: Optional[str] = None
    name: str
    transport: str  # stdio, http, talos_tunnel
    command: Optional[str] = None
    args: Optional[List[str]] = None
    endpoint: Optional[str] = None
    env: Optional[Dict[str, str]] = None
    tags: Optional[Dict[str, str]] = None
    enabled: bool = True


class McpPolicyUpdate(BaseModel):
    allowed_servers: List[str] = ["*"]
    allowed_tools: Dict[str, List[str]] = {}
    deny_by_default: bool = True


# --- RBAC ---
@router.get("/rbac/roles")
async def list_roles(rbac: RbacContext = Depends(require_permission("rbac.read"))):
    """List available roles."""
    from app.middleware.auth_admin import MOCK_ROLES
    return {"roles": [{"id": k, **v} for k, v in MOCK_ROLES.items()]}


@router.post("/rbac/bindings")
async def create_binding(rbac: RbacContext = Depends(require_permission("rbac.write"))):
    """Create a role binding."""
    return {"error": {"code": "NOT_IMPLEMENTED", "message": "Binding creation coming soon"}}


# --- MCP Registry ---
@router.get("/mcp/servers")
async def list_mcp_servers(rbac: RbacContext = Depends(require_permission("mcp.read"))):
    """List registered MCP servers."""
    servers = mcp_registry.list_servers()
    return {"servers": servers}


@router.get("/mcp/servers/{server_id}")
async def get_mcp_server(server_id: str, rbac: RbacContext = Depends(require_permission("mcp.read"))):
    """Get an MCP server by ID."""
    server = mcp_registry.get_server(server_id)
    if not server:
        raise HTTPException(status_code=404, detail={"error": {"code": "NOT_FOUND", "message": f"Server {server_id} not found"}})
    return server


@router.post("/mcp/servers")
async def register_mcp_server(data: McpServerCreate, rbac: RbacContext = Depends(require_permission("mcp.admin"))):
    """Register an MCP server."""
    server = mcp_registry.create_server(data.model_dump())
    return server


# --- MCP Policies ---
@router.get("/mcp/policies")
async def list_mcp_policies(team_id: Optional[str] = None, rbac: RbacContext = Depends(require_permission("mcp.read"))):
    """List MCP policies."""
    if team_id:
        policy = mcp_registry.get_team_policy(team_id)
        return {"policies": [policy] if policy else []}
    return {"policies": list(mcp_registry.MCP_POLICIES.values())}


@router.post("/mcp/policies/{team_id}")
async def set_mcp_policy(team_id: str, data: McpPolicyUpdate, rbac: RbacContext = Depends(require_permission("policies.write"))):
    """Set MCP policy for a team."""
    policy = mcp_registry.set_team_policy(team_id, data.model_dump())
    return policy


# --- Keys (stub) ---
@router.post("/teams/{team_id}/keys")
async def create_key(team_id: str, rbac: RbacContext = Depends(require_permission("keys.write"))):
    """Create a virtual key for a team."""
    import secrets
    key = f"sk-{secrets.token_urlsafe(32)}"
    return {"key": key, "id": f"key-{secrets.token_hex(4)}", "team_id": team_id, "note": "Key shown only once"}


# --- LLM Config ---
@router.get("/llm/upstreams")
async def list_upstreams(rbac: RbacContext = Depends(require_permission("llm.read"))):
    """List LLM upstreams."""
    from app.domain.router_ai import router as llm_router
    return {"upstreams": llm_router.list_upstreams()}


@router.post("/llm/upstreams")
async def create_upstream(data: dict, rbac: RbacContext = Depends(require_permission("llm.admin"))):
    """Create an LLM upstream."""
    from app.domain.router_ai import router as llm_router
    upstream = llm_router.create_upstream(data)
    return upstream


@router.get("/llm/model_groups")
async def list_model_groups(rbac: RbacContext = Depends(require_permission("llm.read"))):
    """List model groups."""
    from app.domain.router_ai import router as llm_router
    return {"model_groups": llm_router.list_model_groups()}


@router.post("/llm/model_groups")
async def create_model_group(data: dict, rbac: RbacContext = Depends(require_permission("llm.admin"))):
    """Create a model group."""
    from app.domain.router_ai import router as llm_router
    group = llm_router.create_model_group(data)
    return group


@router.get("/llm/routing_policies")
async def list_routing_policies(rbac: RbacContext = Depends(require_permission("llm.read"))):
    """List routing policies."""
    from app.domain.router_ai import router as llm_router
    return {"policies": list(llm_router.ROUTING_POLICIES.values())}
