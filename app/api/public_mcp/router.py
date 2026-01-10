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


@router.get("/servers")
async def list_servers(auth: AuthContext = Depends(require_scope("mcp:read"))):
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
async def list_tools(server_id: str, auth: AuthContext = Depends(require_scope("mcp:read"))):
    """List tools for a server."""
    # Check access
    if not auth.can_access_mcp_server(server_id):
        raise HTTPException(status_code=403, detail={"error": {"code": "POLICY_DENIED", "message": "Server not allowed for this key"}})
    
    if not registry.is_server_allowed(auth.team_id, server_id):
        raise HTTPException(status_code=403, detail={"error": {"code": "POLICY_DENIED", "message": "Server not allowed for this team"}})
    
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
async def get_tool_schema(server_id: str, tool_name: str, auth: AuthContext = Depends(require_scope("mcp:read"))):
    """Get JSON schema for a tool."""
    # Check access
    if not registry.is_tool_allowed(auth.team_id, server_id, tool_name):
        raise HTTPException(status_code=403, detail={"error": {"code": "POLICY_DENIED", "message": "Tool not allowed"}})
    
    schema_data = discovery.get_tool_schema(server_id, tool_name)
    if not schema_data:
        raise HTTPException(status_code=404, detail={"error": {"code": "NOT_FOUND", "message": f"Schema for {tool_name} not found"}})
    
    return schema_data


@router.post("/servers/{server_id}/tools/{tool_name}:call")
async def call_tool(server_id: str, tool_name: str, request: ToolCallRequest, auth: AuthContext = Depends(require_scope("mcp:invoke"))):
    """Invoke a tool."""
    # Check access
    if not registry.is_tool_allowed(auth.team_id, server_id, tool_name):
        raise HTTPException(status_code=403, detail={"error": {"code": "POLICY_DENIED", "message": "Tool not allowed"}})
    
    # Get server config
    server = registry.get_server(server_id)
    if not server:
        raise HTTPException(status_code=404, detail={"error": {"code": "NOT_FOUND", "message": "Server not found"}})
    
    # TODO: Actual invocation via transport adapter
    # For MVP, return mock response
    return {
        "output": {"result": f"Mock invocation of {tool_name} on {server_id}", "input_received": request.input},
        "timing_ms": 42,
        "audit_ref": f"audit-{request.request_id or 'unknown'}"
    }
