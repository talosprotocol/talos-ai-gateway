"""Public MCP API Router - Dynamic Discovery."""
from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel
from typing import Any, Dict, Optional
from decimal import Decimal

from app.middleware.auth_public import require_scope, AuthContext
from app.domain.mcp import registry, discovery
from app.utils.id import uuid7

# Phase 15 Imports
from app.dependencies import get_budget_service, get_usage_manager, get_mcp_client
from app.domain.budgets.service import BudgetService, BudgetExceededError
from app.domain.usage.manager import UsageManager
from app.adapters.mcp.client import McpClient
from app.errors import raise_talos_error

import time

router = APIRouter()


class ToolCallRequest(BaseModel):
    input: Dict[str, Any]
    request_id: Optional[str] = None
    session_id: Optional[str] = None


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


@router.post("/servers/{server_id}/tools/{tool_name}:call")
async def call_tool(
    server_id: str, 
    tool_name: str, 
    request: ToolCallRequest, 
    response: Response,
    auth: AuthContext = Depends(require_scope("mcp.invoke")),
    mcp_client: McpClient = Depends(get_mcp_client),
    budget_service: BudgetService = Depends(get_budget_service),
    usage_manager: UsageManager = Depends(get_usage_manager)
):
    """Invoke a tool."""
    request_id = request.request_id or uuid7()
    
    # Check access
    if not registry.is_tool_allowed(auth.team_id, server_id, tool_name):
        raise_talos_error("POLICY_DENIED", 403, "Tool not allowed")
    
    # Get server config
    server = registry.get_server(server_id)
    if not server:
        raise_talos_error("NOT_FOUND", 404, "Server not found")
    
    # --- Phase 15: Budget Enforcement ---
    estimate_usd = Decimal("0") # Default for MCP unless configured
    cost_usd = Decimal("0")
    
    # Retrieve cost from registry
    cost_usd, _ = budget_service.pricing.get_mcp_cost(server_id, tool_name)
    estimate_usd = cost_usd # Estimate is exact cost for MCP typically
    
    try:
        limit_team = Decimal(str(auth.team_budget_metadata.get("limit_usd", "0")))
        limit_key = Decimal(str(auth.budget_metadata.get("limit_usd", "0")))
        overdraft = Decimal(auth.overdraft_usd)
        
        budget_headers = budget_service.reserve(
            request_id=str(request_id),
            team_id=auth.team_id,
            key_id=auth.key_id,
            budget_mode=auth.budget_mode,
            estimate_usd=estimate_usd,
            limit_usd_team=limit_team,
            limit_usd_key=limit_key,
            overdraft_usd=overdraft
        )
        
        for k, v in budget_headers.items():
            response.headers[k] = v
            
    except BudgetExceededError as e:
        raise_talos_error("BUDGET_EXCEEDED", 402, f"Budget exceeded: {e.message}")
    except Exception as e:
        # Fallback for budget errors
        print(f"Budget Reserve Error: {e}")
        # Proceed logic failure handled by budget_service likely raising if fatal
    
    start_ts = time.time()
    status = "success"
    try:
        result = await mcp_client.call_tool(server, tool_name, request.input)
        duration_ms = int((time.time() - start_ts) * 1000)
        
        # Record & Settle
        await usage_manager.record_event(
            request_id=str(request_id),
            team_id=auth.team_id,
            key_id=auth.key_id,
            org_id=auth.org_id or "",
            surface="mcp",
            target=f"{server_id}:{tool_name}",
            latency_ms=duration_ms,
            status="success",
            token_count_source="not_applicable"
        )
        
        return {
            "output": result,
            "timing_ms": duration_ms,
            "audit_ref": f"audit-{str(request_id)}"
        }
    except Exception as e:
        duration_ms = int((time.time() - start_ts) * 1000)
        
        # Record Failure & Settle with 0 cost
        await usage_manager.record_event(
            request_id=str(request_id),
            team_id=auth.team_id,
            key_id=auth.key_id,
            org_id=auth.org_id or "",
            surface="mcp",
            target=f"{server_id}:{tool_name}",
            latency_ms=duration_ms,
            status="error",
            token_count_source="not_applicable"
        )
        
        raise_talos_error("UPSTREAM_ERROR", 502, str(e))
