"""Virtual Key Authentication for Data Plane."""
from fastapi import Header, HTTPException, Depends
from typing import Optional
import hashlib

# Mock key store for MVP - will be replaced with Redis/Postgres lookup
MOCK_KEYS = {
    # key_hash -> key_data
    hashlib.sha256(b"sk-test-key-1").hexdigest(): {
        "id": "key-1",
        "team_id": "team-1",
        "org_id": "org-1",
        "scopes": ["llm:invoke", "mcp:invoke", "mcp:read"],
        "allowed_model_groups": ["gpt-4-turbo", "gpt-3.5-turbo", "llama3", "qwen-coder", "gemma"],
        "allowed_mcp_servers": ["*"],
        "revoked": False
    }
}

class AuthContext:
    """Authentication context for requests."""
    def __init__(self, key_id: str, team_id: str, org_id: str, scopes: list, 
                 allowed_model_groups: list, allowed_mcp_servers: list):
        self.key_id = key_id
        self.team_id = team_id
        self.org_id = org_id
        self.scopes = scopes
        self.allowed_model_groups = allowed_model_groups
        self.allowed_mcp_servers = allowed_mcp_servers

    def has_scope(self, scope: str) -> bool:
        return scope in self.scopes

    def can_access_model_group(self, group_id: str) -> bool:
        return "*" in self.allowed_model_groups or group_id in self.allowed_model_groups

    def can_access_mcp_server(self, server_id: str) -> bool:
        return "*" in self.allowed_mcp_servers or server_id in self.allowed_mcp_servers


async def get_auth_context(authorization: Optional[str] = Header(None)) -> AuthContext:
    """Extract and validate virtual key from Authorization header."""
    if not authorization:
        raise HTTPException(status_code=401, detail={"error": {"code": "AUTH_INVALID", "message": "Missing Authorization header"}})
    
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail={"error": {"code": "AUTH_INVALID", "message": "Invalid Authorization format"}})
    
    key = authorization[7:]  # Remove "Bearer "
    key_hash = hashlib.sha256(key.encode()).hexdigest()
    
    key_data = MOCK_KEYS.get(key_hash)
    if not key_data:
        raise HTTPException(status_code=401, detail={"error": {"code": "AUTH_INVALID", "message": "Invalid key"}})
    
    if key_data.get("revoked"):
        raise HTTPException(status_code=401, detail={"error": {"code": "AUTH_REVOKED", "message": "Key has been revoked"}})
    
    return AuthContext(
        key_id=key_data["id"],
        team_id=key_data["team_id"],
        org_id=key_data["org_id"],
        scopes=key_data["scopes"],
        allowed_model_groups=key_data["allowed_model_groups"],
        allowed_mcp_servers=key_data["allowed_mcp_servers"]
    )


def require_scope(scope: str):
    """Dependency that requires a specific scope."""
    async def checker(auth: AuthContext = Depends(get_auth_context)):
        if not auth.has_scope(scope):
            raise HTTPException(status_code=403, detail={"error": {"code": "POLICY_DENIED", "message": f"Missing scope: {scope}"}})
        return auth
    return checker
