"""Public MCP API Router - Dynamic Discovery."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Any, Dict, Optional

from app.middleware.auth_public import get_auth_context, require_scope, AuthContext
from app.domain.mcp import registry, discovery

router = APIRouter()


class ToolCallRequest(BaseModel):
    input: Dict[str, Any]
    request_id: Optional[str] = None
    session_id: Optional[str] = None



from app.errors import raise_talos_error

@router.get("/servers")
async def list_servers(auth: AuthContext = Depends(require_scope("mcp.read"))):
    """List MCP servers available to the key."""
    all_servers = registry.list_servers()
    
    # Filter by key's allowed servers
    if "*" in auth.allowed_mcp_servers:
        allowed_servers = all_servers
    else:
        allowed_servers = [s for s in all_servers if s["id"] in auth.allowed_mcp_servers]
    
    # Further filter by team policy
    team_allowed = []
    for server in allowed_servers:
        if registry.is_server_allowed(auth.team_id, server["id"]):
            team_allowed.append({
                "id": server["id"],
                "name": server["name"],
                "transport": server["transport"],
                "tags": server.get("tags", {})
            })
    
    return {"servers": team_allowed}


@router.get("/servers/{server_id}/tools")
async def list_tools(server_id: str, auth: AuthContext = Depends(require_scope("mcp.read"))):
    """List tools for a server."""
    # Check access
    if not auth.can_access_mcp_server(server_id):
        raise_talos_error("POLICY_DENIED", 403, "Server not allowed for this key")
    
    if not registry.is_server_allowed(auth.team_id, server_id):
        raise_talos_error("POLICY_DENIED", 403, "Server not allowed for this team")
    
    # Get tools
    tools = discovery.get_tools(server_id)
    
    # Filter by team policy (if tool-level restrictions exist)
    policy = registry.get_team_policy(auth.team_id)
    allowed_tools_map = policy.get("allowed_tools", {}) if policy else {}
    server_allowed_tools = allowed_tools_map.get(server_id, [])
    
    if server_allowed_tools and "*" not in server_allowed_tools:
        tools = [t for t in tools if t["name"] in server_allowed_tools]
    
    return {"tools": tools, "server_id": server_id}


@router.get("/servers/{server_id}/tools/{tool_name}/schema")
async def get_tool_schema(server_id: str, tool_name: str, auth: AuthContext = Depends(require_scope("mcp.read"))):
    """Get JSON schema for a tool."""
    # Check access
    if not registry.is_tool_allowed(auth.team_id, server_id, tool_name):
        raise_talos_error("POLICY_DENIED", 403, "Tool not allowed")
    
    schema_data = discovery.get_tool_schema(server_id, tool_name)
    if not schema_data:
        raise_talos_error("NOT_FOUND", 404, f"Schema for {tool_name} not found")
    
    return schema_data


import time
from app.dependencies import get_mcp_client
from app.adapters.mcp.client import McpClient

@router.post("/servers/{server_id}/tools/{tool_name}:call")
async def call_tool(
    server_id: str, 
    tool_name: str, 
    request: ToolCallRequest, 
    auth: AuthContext = Depends(require_scope("mcp.invoke")),
    mcp_client: McpClient = Depends(get_mcp_client)
):
    """Invoke a tool."""
    # Check access
    if not registry.is_tool_allowed(auth.team_id, server_id, tool_name):
        raise_talos_error("POLICY_DENIED", 403, "Tool not allowed")
    
    # Get server config
    server = registry.get_server(server_id)
    if not server:
        raise_talos_error("NOT_FOUND", 404, "Server not found")
    
    start_ts = time.time()
    try:
        result = await mcp_client.call_tool(server, tool_name, request.input)
        duration_ms = int((time.time() - start_ts) * 1000)
        
        return {
            "output": result,
            "timing_ms": duration_ms,
            "audit_ref": f"audit-{request.request_id or 'none'}"
        }
    except Exception as e:
        raise_talos_error("UPSTREAM_ERROR", 502, str(e))
