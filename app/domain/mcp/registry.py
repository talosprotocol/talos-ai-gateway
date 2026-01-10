"""MCP Server Registry - Domain Model."""
from dataclasses import dataclass, field
from typing import Dict, List, Optional
from datetime import datetime

# In-memory store for MVP - will be replaced with Postgres
MCP_SERVERS: Dict[str, dict] = {
    "filesystem": {
        "id": "filesystem",
        "name": "Filesystem MCP Server",
        "transport": "stdio",
        "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"],
        "env": {},
        "tags": {"category": "storage"},
        "enabled": True,
        "created_at": "2026-01-10T00:00:00Z"
    },
    "fetch": {
        "id": "fetch",
        "name": "Fetch MCP Server",
        "transport": "stdio",
        "command": "uvx",
        "args": ["mcp-server-fetch"],
        "env": {},
        "tags": {"category": "network"},
        "enabled": True,
        "created_at": "2026-01-10T00:00:00Z"
    }
}

MCP_POLICIES: Dict[str, dict] = {
    # team_id -> policy
    "team-1": {
        "id": "policy-team-1",
        "team_id": "team-1",
        "allowed_servers": ["*"],  # All servers allowed
        "allowed_tools": {},  # Empty means all tools allowed
        "deny_by_default": False
    }
}


def list_servers(enabled_only: bool = True) -> List[dict]:
    """List all registered MCP servers."""
    servers = list(MCP_SERVERS.values())
    if enabled_only:
        servers = [s for s in servers if s.get("enabled", True)]
    return servers


def get_server(server_id: str) -> Optional[dict]:
    """Get a server by ID."""
    return MCP_SERVERS.get(server_id)


def create_server(server_data: dict) -> dict:
    """Register a new MCP server."""
    server_id = server_data.get("id") or f"mcp-{len(MCP_SERVERS)+1}"
    server_data["id"] = server_id
    server_data["created_at"] = datetime.utcnow().isoformat() + "Z"
    MCP_SERVERS[server_id] = server_data
    return server_data


def get_team_policy(team_id: str) -> Optional[dict]:
    """Get MCP policy for a team."""
    return MCP_POLICIES.get(team_id)


def set_team_policy(team_id: str, policy_data: dict) -> dict:
    """Set MCP policy for a team."""
    policy_data["team_id"] = team_id
    policy_data["id"] = f"policy-{team_id}"
    MCP_POLICIES[team_id] = policy_data
    return policy_data


def is_server_allowed(team_id: str, server_id: str) -> bool:
    """Check if a server is allowed for a team."""
    policy = get_team_policy(team_id)
    if not policy:
        return False  # Deny by default if no policy
    
    allowed = policy.get("allowed_servers", [])
    if "*" in allowed:
        return True
    return server_id in allowed


def is_tool_allowed(team_id: str, server_id: str, tool_name: str) -> bool:
    """Check if a tool is allowed for a team."""
    if not is_server_allowed(team_id, server_id):
        return False
    
    policy = get_team_policy(team_id)
    if not policy:
        return False
    
    allowed_tools = policy.get("allowed_tools", {})
    if not allowed_tools:
        return True  # Empty means all tools allowed
    
    server_tools = allowed_tools.get(server_id, [])
    if "*" in server_tools:
        return True
    return tool_name in server_tools
