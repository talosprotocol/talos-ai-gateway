"""MCP Discovery - Tool fetching and caching."""
from typing import Dict, List, Optional
import hashlib
import json
from datetime import datetime, timedelta, timezone

# In-memory schema cache for MVP
SCHEMA_CACHE: Dict[str, dict] = {}
TOOL_LIST_CACHE: Dict[str, dict] = {}

# Mock tool data for demo servers
MOCK_TOOLS = {
    "filesystem": [
        {"name": "read_file", "description": "Read contents of a file", "tags": {}},
        {"name": "write_file", "description": "Write contents to a file", "tags": {}},
        {"name": "list_directory", "description": "List directory contents", "tags": {}},
    ],
    "fetch": [
        {"name": "fetch", "description": "Fetch a URL and return its contents", "tags": {}},
    ]
}

MOCK_SCHEMAS = {
    "filesystem": {
        "read_file": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Path to the file"}
            },
            "required": ["path"]
        },
        "write_file": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "content": {"type": "string"}
            },
            "required": ["path", "content"]
        },
        "list_directory": {
            "type": "object",
            "properties": {
                "path": {"type": "string"}
            },
            "required": ["path"]
        }
    },
    "fetch": {
        "fetch": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "format": "uri"}
            },
            "required": ["url"]
        }
    }
}


def get_tools(server_id: str) -> List[dict]:
    """Get tools for a server (with caching)."""
    cache_key = f"tools:{server_id}"
    cached = TOOL_LIST_CACHE.get(cache_key)
    
    if cached:
        expires_at = datetime.fromisoformat(cached["expires_at"].replace("Z", "+00:00"))
        if datetime.now(expires_at.tzinfo) < expires_at:
            return cached["tools"]
    
    # Fetch tools (mock for MVP)
    tools = MOCK_TOOLS.get(server_id, [])
    
    # Cache for 60 seconds
    TOOL_LIST_CACHE[cache_key] = {
        "tools": tools,
        "fetched_at": datetime.now(timezone.utc).isoformat() + "Z",
        "expires_at": (datetime.now(timezone.utc) + timedelta(seconds=60)).isoformat() + "Z"
    }
    
    return tools


def get_tool_schema(server_id: str, tool_name: str) -> Optional[dict]:
    """Get JSON schema for a tool (with caching)."""
    cache_key = f"schema:{server_id}:{tool_name}"
    cached = SCHEMA_CACHE.get(cache_key)
    
    if cached:
        expires_at = datetime.fromisoformat(cached["expires_at"].replace("Z", "+00:00"))
        if datetime.now(expires_at.tzinfo) < expires_at:
            return cached
    
    # Fetch schema (mock for MVP)
    server_schemas = MOCK_SCHEMAS.get(server_id, {})
    schema = server_schemas.get(tool_name)
    
    if not schema:
        return None
    
    # Compute hash
    schema_hash = hashlib.sha256(json.dumps(schema, sort_keys=True).encode()).hexdigest()[:16]
    
    result = {
        "json_schema": schema,
        "schema_hash": schema_hash,
        "fetched_at": datetime.now(timezone.utc).isoformat() + "Z",
        "expires_at": (datetime.now(timezone.utc) + timedelta(seconds=600)).isoformat() + "Z",  # 10 min TTL
        "ttl_seconds": 600
    }
    
    SCHEMA_CACHE[cache_key] = result
    return result
