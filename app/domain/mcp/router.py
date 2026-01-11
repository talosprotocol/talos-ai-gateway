"""MCP Domain Router - Servers, Policies."""
from typing import Dict, List, Optional
import json
from pathlib import Path

# In-memory storage for MVP
MCP_SERVERS: Dict[str, dict] = {}
MCP_POLICIES: Dict[str, dict] = {}
CONFIG_FILE = Path("config/mcp.json")

def load_config():
    """Load MCP config from file."""
    global MCP_SERVERS, MCP_POLICIES
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE) as f:
                data = json.load(f)
                MCP_SERVERS = data.get("servers", {})
                MCP_POLICIES = data.get("policies", {})
        except Exception as e:
            print(f"Error loading MCP config: {e}")

def save_config():
    """Save MCP config to file."""
    data = {
        "servers": MCP_SERVERS,
        "policies": MCP_POLICIES
    }
    CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_FILE, "w") as f:
        json.dump(data, f, indent=2)

# Server Ops
def list_servers() -> List[dict]:
    return list(MCP_SERVERS.values())

def get_server(server_id: str) -> Optional[dict]:
    return MCP_SERVERS.get(server_id)

def create_server(server: dict) -> dict:
    MCP_SERVERS[server["id"]] = server
    save_config()
    return server

def delete_server(server_id: str):
    if server_id in MCP_SERVERS:
        del MCP_SERVERS[server_id]
        save_config()

# Policy Ops
def list_policies(team_id: Optional[str] = None) -> List[dict]:
    policies = list(MCP_POLICIES.values())
    if team_id:
        return [p for p in policies if p.get("team_id") == team_id]
    return policies

def create_policy(policy: dict) -> dict:
    MCP_POLICIES[policy["id"]] = policy
    save_config()
    return policy

def delete_policy(policy_id: str):
    if policy_id in MCP_POLICIES:
        del MCP_POLICIES[policy_id]
        save_config()

# Initialize
load_config()
