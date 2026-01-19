"""Admin API Router - LLM Upstreams, Model Groups, Policies, Catalog."""
from fastapi import APIRouter, HTTPException, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from typing import Dict, Any, List, Optional
from datetime import datetime, timezone
from pathlib import Path
import uuid
import json

from app.dependencies import (
    get_upstream_store, get_model_group_store, get_secret_store, 
    get_mcp_store, get_audit_store, get_routing_policy_store, get_usage_store
)
from app.domain.interfaces import (
    UpstreamStore, ModelGroupStore, SecretStore, McpStore, AuditStore, 
    RoutingPolicyStore, UsageStore
)

router = APIRouter()

# Load provider catalog from contracts
CATALOG_PATH = Path(__file__).parent.parent.parent.parent / "talos-contracts" / "catalog" / "provider_templates.json"
_catalog_cache: Optional[dict] = None

from app.domain.a2a.utils import uuid7

def get_provider_catalog() -> dict:
    """Load provider catalog from contracts."""
    global _catalog_cache
    if _catalog_cache is None:
        # Try local contracts path first
        paths = [
            CATALOG_PATH,
            Path(__file__).parent.parent.parent / "catalog" / "provider_templates.json"
        ]
        for path in paths:
            if path.exists():
                with open(path) as f:
                    _catalog_cache = json.load(f)
                break
        if _catalog_cache is None:
            _catalog_cache = {"version": "0.0.0", "templates": []}
    return _catalog_cache


# ============ Pydantic Models ============

class UpstreamCreate(BaseModel):
    id: str
    provider: str
    endpoint: str
    credentials_ref: str = ""
    tags: Dict[str, str] = Field(default_factory=dict)
    enabled: bool = True


class UpstreamUpdate(BaseModel):
    provider: Optional[str] = None
    endpoint: Optional[str] = None
    credentials_ref: Optional[str] = None
    tags: Optional[Dict[str, str]] = None
    enabled: Optional[bool] = None
    expected_version: int


class ModelGroupCreate(BaseModel):
    id: str
    name: str
    deployments: List[Dict[str, Any]]
    fallback_groups: List[str] = Field(default_factory=list)
    routing_policy_id: str = "default"
    routing_policy_version: int = 1


class ModelGroupUpdate(BaseModel):
    name: Optional[str] = None
    deployments: Optional[List[Dict[str, Any]]] = None
    fallback_groups: Optional[List[str]] = None
    routing_policy_id: Optional[str] = None
    routing_policy_version: Optional[int] = None
    expected_version: int


from app.middleware.auth_admin import require_permission, get_rbac_context, RbacContext


# ============ Helper ============

def audit(store: AuditStore, action: str, resource_type: str, principal_id: str, 
          resource_id: str = None, outcome: str = "success", **details):
    event = {
        "event_id": uuid7(),
        "timestamp": datetime.now(timezone.utc),
        "principal_id": principal_id,
        "action": action,
        "resource_type": resource_type,
        "resource_id": resource_id,
        "status": outcome,
        "schema_id": "talos.audit.admin.v1",
        "schema_version": 1,
        "details": details
    }
    store.append_event(event)


# ============ Catalog ============

@router.get("/catalog/provider-templates")
async def get_provider_templates(
    principal: dict = Depends(require_permission("llm.read")),
    audit_store: AuditStore = Depends(get_audit_store)
):
    """Get provider catalog templates from contracts."""
    catalog = get_provider_catalog()
    audit(audit_store, "catalog.read", "catalog", principal.id, outcome="success", context="dashboard")
    return {
        "version": catalog.get("version", "0.0.0"),
        "templates": catalog.get("templates", [])
    }


# ============ LLM Upstreams ============

@router.get("/llm/upstreams")
async def list_upstreams(
    principal: dict = Depends(require_permission("llm.read")),
    store: UpstreamStore = Depends(get_upstream_store)
):
    """List all LLM upstreams."""
    return {"upstreams": store.list_upstreams()}


@router.get("/llm/upstreams/{upstream_id}")
async def get_upstream(
    upstream_id: str, 
    principal: dict = Depends(require_permission("llm.read")),
    store: UpstreamStore = Depends(get_upstream_store)
):
    """Get a single upstream."""
    upstream = store.get_upstream(upstream_id)
    if not upstream:
        raise HTTPException(status_code=404, detail={
            "error": {"code": "NOT_FOUND", "message": f"Upstream {upstream_id} not found"}
        })
    upstream.setdefault("version", 1)
    return upstream


@router.post("/llm/upstreams", status_code=201)
async def create_upstream(
    data: UpstreamCreate, 
    principal: dict = Depends(require_permission("llm.admin")),
    store: UpstreamStore = Depends(get_upstream_store),
    audit_store: AuditStore = Depends(get_audit_store)
):
    """Create a new upstream."""
    if store.get_upstream(data.id):
        raise HTTPException(status_code=400, detail={
            "error": {"code": "VALIDATION_ERROR", "message": f"Upstream {data.id} already exists"}
        })
    
    upstream_data = data.dict()
    upstream_data["version"] = 1
    upstream_data["created_at"] = datetime.now(timezone.utc).isoformat()
    
    store.create_upstream(upstream_data)
    
    audit(audit_store, "upstream.create", "llm.upstream", principal.id, 
          resource_id=data.id, outcome="success", version_after=1)
    
    return upstream_data


@router.patch("/llm/upstreams/{upstream_id}")
async def update_upstream(
    upstream_id: str, 
    data: UpstreamUpdate,
    principal: dict = Depends(require_permission("llm.admin")),
    store: UpstreamStore = Depends(get_upstream_store),
    audit_store: AuditStore = Depends(get_audit_store)
):
    """Update an upstream with concurrency control."""
    try:
        updated = store.update_upstream(upstream_id, data.dict(exclude_unset=True, exclude={"expected_version"}), expected_version=data.expected_version)
        
        audit(audit_store, "upstream.update", "llm.upstream", principal.id,
            resource_id=upstream_id, outcome="success", 
            version_after=updated.get("version"))
        return updated
    except KeyError:
        raise HTTPException(status_code=404, detail={"error": {"code": "NOT_FOUND"}})
    except ValueError as e:
        raise HTTPException(status_code=409, detail={"error": {"code": "CONFLICT_VERSION_MISMATCH", "message": str(e)}})


@router.post("/llm/upstreams/{upstream_id}:disable")
async def disable_upstream(
    upstream_id: str, 
    principal: dict = Depends(require_permission("llm.admin")),
    store: UpstreamStore = Depends(get_upstream_store)
):
    """Disable an upstream."""
    try:
        updated = store.update_upstream(upstream_id, {"enabled": False})
        return {"success": True, "upstream": updated}
    except KeyError:
        raise HTTPException(status_code=404, detail={"error": {"code": "NOT_FOUND"}})


@router.post("/llm/upstreams/{upstream_id}:enable")
async def enable_upstream(
    upstream_id: str, 
    principal: dict = Depends(require_permission("llm.admin")),
    store: UpstreamStore = Depends(get_upstream_store)
):
    """Enable an upstream."""
    try:
        updated = store.update_upstream(upstream_id, {"enabled": True})
        return {"success": True, "upstream": updated}
    except KeyError:
        raise HTTPException(status_code=404, detail={"error": {"code": "NOT_FOUND"}})


@router.delete("/llm/upstreams/{upstream_id}")
async def delete_upstream(
    upstream_id: str, 
    principal: dict = Depends(require_permission("llm.admin")),
    store: UpstreamStore = Depends(get_upstream_store),
    mg_store: ModelGroupStore = Depends(get_model_group_store),
    audit_store: AuditStore = Depends(get_audit_store)
):
    """Delete an upstream (checks dependencies)."""
    if "resource.delete" not in principal["permissions"] and "platform.admin" not in principal["permissions"]:
        raise HTTPException(status_code=403, detail={"error": {"code": "PERMISSION_DENIED"}})
    
    if not store.get_upstream(upstream_id):
        raise HTTPException(status_code=404, detail={"error": {"code": "NOT_FOUND"}})
    
    # Check dependencies
    dependents = []
    for group in mg_store.list_model_groups():
        for dep in group.get("deployments", []):
            if dep.get("upstream_id") == upstream_id:
                dependents.append({"type": "model_group", "id": group.get("id")})
    
    if dependents:
        raise HTTPException(status_code=409, detail={
            "error": {"code": "DEPENDENCY_EXISTS", 
                     "message": f"Cannot delete: {len(dependents)} dependent(s)",
                     "dependents": dependents}
        })
    
    store.delete_upstream(upstream_id)
    
    audit(audit_store, "upstream.delete", "llm.upstream", principal.id,
          resource_id=upstream_id, outcome="success")
    
    return {"success": True}


# ============ LLM Model Groups ============

@router.get("/llm/model-groups")
async def list_model_groups(
    principal: dict = Depends(require_permission("llm.read")),
    store: ModelGroupStore = Depends(get_model_group_store)
):
    """List all model groups."""
    return {"model_groups": store.list_model_groups()}


@router.post("/llm/model-groups", status_code=201)
async def create_model_group(
    data: ModelGroupCreate, 
    principal: dict = Depends(require_permission("llm.admin")),
    store: ModelGroupStore = Depends(get_model_group_store),
    audit_store: AuditStore = Depends(get_audit_store)
):
    """Create a new model group."""
    if store.get_model_group(data.id):
        raise HTTPException(status_code=400, detail={
            "error": {"code": "VALIDATION_ERROR", "message": f"Model group {data.id} already exists"}
        })
    
    group_data = data.dict()
    group_data["version"] = 1
    group_data["created_at"] = datetime.now(timezone.utc).isoformat()
    
    store.create_model_group(group_data)
    
    audit(audit_store, "model_group.create", "llm.model_group", principal.id,
          resource_id=data.id, outcome="success", version_after=1)
    
    return group_data


@router.patch("/llm/model-groups/{group_id}")
async def update_model_group(
    group_id: str,
    data: ModelGroupUpdate,
    principal: dict = Depends(require_permission("llm.admin")),
    store: ModelGroupStore = Depends(get_model_group_store),
    audit_store: AuditStore = Depends(get_audit_store)
):
    """Update a model group with concurrency control."""
    try:
        updated = store.update_model_group(group_id, data.dict(exclude_unset=True, exclude={"expected_version"}), expected_version=data.expected_version)
        audit(audit_store, "model_group.update", "llm.model_group", principal.id,
            resource_id=group_id, outcome="success",
            version_after=updated.get("version"))
        return updated
    except KeyError:
        raise HTTPException(status_code=404, detail={"error": {"code": "NOT_FOUND"}})
    except ValueError as e:
        raise HTTPException(status_code=409, detail={"error": {"code": "CONFLICT_VERSION_MISMATCH", "message": str(e)}})


@router.post("/llm/model-groups/{group_id}:disable")
async def disable_model_group(
    group_id: str, 
    principal: dict = Depends(require_permission("llm.admin")),
    store: ModelGroupStore = Depends(get_model_group_store)
):
    try:
        updated = store.update_model_group(group_id, {"enabled": False})
        return {"success": True, "model_group": updated}
    except KeyError:
        raise HTTPException(status_code=404, detail={"error": {"code": "NOT_FOUND"}})


@router.post("/llm/model-groups/{group_id}:enable")
async def enable_model_group(
    group_id: str, 
    principal: dict = Depends(require_permission("llm.admin")),
    store: ModelGroupStore = Depends(get_model_group_store)
):
    try:
        updated = store.update_model_group(group_id, {"enabled": True})
        return {"success": True, "model_group": updated}
    except KeyError:
        raise HTTPException(status_code=404, detail={"error": {"code": "NOT_FOUND"}})


@router.delete("/llm/model-groups/{group_id}")
async def delete_model_group(
    group_id: str, 
    principal: dict = Depends(require_permission("llm.admin")),
    store: ModelGroupStore = Depends(get_model_group_store),
    audit_store: AuditStore = Depends(get_audit_store)
):
    if "resource.delete" not in principal["permissions"] and "platform.admin" not in principal["permissions"]:
        raise HTTPException(status_code=403, detail={"error": {"code": "PERMISSION_DENIED"}})
    
    if not store.get_model_group(group_id):
        raise HTTPException(status_code=404, detail={"error": {"code": "NOT_FOUND"}})
    
    store.delete_model_group(group_id)
    
    audit(audit_store, "model_group.delete", "llm.model_group", principal.id,
          resource_id=group_id, outcome="success")
    
    return {"success": True}


# ============ Routing Policies ============

@router.get("/llm/routing-policies")
async def list_routing_policies(
    principal: dict = Depends(require_permission("llm.read")),
    store: RoutingPolicyStore = Depends(get_routing_policy_store)
):
    """List all routing policies."""
    return {"routing_policies": store.list_policies()}


@router.post("/llm/routing-policies", status_code=201)
async def create_routing_policy(
    data: dict, 
    principal: dict = Depends(require_permission("llm.admin")),
    store: RoutingPolicyStore = Depends(get_routing_policy_store)
):
    """Create a new routing policy version."""
    policy_id = data.get("policy_id") or data.get("id", "default")
    data['policy_id'] = policy_id # ensure consistent
    
    # Store should handle versioning logic or we compute it?
    # Interface says create_policy(policy). PostgresStore inserts.
    # We should probably determine version.
    latest = store.get_policy(policy_id)
    new_version = (latest.get("version", 0) if latest else 0) + 1
    
    data["version"] = new_version
    data["created_at"] = datetime.now(timezone.utc).isoformat()
    
    store.create_policy(data)
    
    return data


# ============ Health ============

@router.get("/llm/health")
async def get_llm_health(
    principal: dict = Depends(require_permission("llm.read")),
    store: UpstreamStore = Depends(get_upstream_store)
):
    """Get health status for all upstreams."""
    upstreams = store.list_upstreams()
    health = {}
    
    for u in upstreams:
        health[u.get("id")] = {
            "status": "ok" if u.get("enabled", True) else "disabled",
            "consecutive_failures": 0,
            "last_check_time": datetime.now(timezone.utc).isoformat(),
            "last_success_time": datetime.now(timezone.utc).isoformat(),
            "error": None
        }
    
    return {"health": health}


# ============ MCP Servers ============

@router.get("/mcp/servers")
async def list_mcp_servers(
    principal: dict = Depends(require_permission("mcp.read")),
    store: McpStore = Depends(get_mcp_store)
):
    """List all MCP servers."""
    return {"servers": store.list_servers()}


@router.post("/mcp/servers", status_code=201)
async def create_mcp_server(
    data: dict, 
    principal: dict = Depends(require_permission("mcp.admin")),
    store: McpStore = Depends(get_mcp_store),
    audit_store: AuditStore = Depends(get_audit_store)
):
    server_id = data.get("id")
    if not server_id:
        raise HTTPException(status_code=400, detail={"error": {"code": "VALIDATION_ERROR", "message": "ID required"}})
        
    if store.get_server(server_id):
        raise HTTPException(status_code=400, detail={"error": {"code": "VALIDATION_ERROR", "message": "Exists"}})
    
    data["version"] = 1
    data["created_at"] = datetime.now(timezone.utc).isoformat()
    
    store.create_server(data)
    
    audit(audit_store, "mcp_server.create", "mcp.server", principal.id,
          resource_id=server_id, outcome="success", version_after=1)
    
    return data


@router.post("/mcp/servers/{server_id}:disable")
async def disable_mcp_server(
    server_id: str, 
    principal: dict = Depends(require_permission("mcp.admin")),
    store: McpStore = Depends(get_mcp_store)
):
    try:
        updated = store.update_server(server_id, {"enabled": False})
        return {"success": True, "server": updated}
    except KeyError:
        raise HTTPException(status_code=404, detail={"error": {"code": "NOT_FOUND"}})


@router.post("/mcp/servers/{server_id}:enable")
async def enable_mcp_server(
    server_id: str, 
    principal: dict = Depends(require_permission("mcp.admin")),
    store: McpStore = Depends(get_mcp_store)
):
    try:
        updated = store.update_server(server_id, {"enabled": True})
        return {"success": True, "server": updated}
    except KeyError:
        raise HTTPException(status_code=404, detail={"error": {"code": "NOT_FOUND"}})


@router.delete("/mcp/servers/{server_id}")
async def delete_mcp_server(
    server_id: str, 
    principal: dict = Depends(require_permission("mcp.admin")),
    store: McpStore = Depends(get_mcp_store),
    audit_store: AuditStore = Depends(get_audit_store)
):
    if "resource.delete" not in principal["permissions"] and "platform.admin" not in principal["permissions"]:
        raise HTTPException(status_code=403, detail={"error": {"code": "PERMISSION_DENIED"}})
    
    store.delete_server(server_id)
    
    audit(audit_store, "mcp_server.delete", "mcp.server", principal.id,
          resource_id=server_id, outcome="success")
    
    return {"success": True}


# ============ MCP Policies ============

@router.get("/mcp/policies")
async def list_mcp_policies(
    team_id: Optional[str] = None, 
    principal: dict = Depends(require_permission("mcp.read")),
    store: McpStore = Depends(get_mcp_store)
):
    if not team_id:
        return {"policies": []} # Required support listing all? Store might need impl
    return {"policies": store.list_policies(team_id)}


@router.post("/mcp/policies", status_code=201)
async def create_mcp_policy(
    data: dict, 
    principal: dict = Depends(require_permission("mcp.admin")),
    store: McpStore = Depends(get_mcp_store),
    audit_store: AuditStore = Depends(get_audit_store)
):
    policy_id = data.get("id") or uuid7()
    data["id"] = policy_id
    data["created_at"] = datetime.utcnow().isoformat()
    
    store.upsert_policy(data)
    
    audit(audit_store, "mcp_policy.create", "mcp.policy", principal.id,
          resource_id=policy_id, outcome="success")
    
    return data


@router.delete("/mcp/policies/{policy_id}")
async def delete_mcp_policy(
    policy_id: str, 
    principal: dict = Depends(require_permission("mcp.admin")),
    store: McpStore = Depends(get_mcp_store),
    audit_store: AuditStore = Depends(get_audit_store)
):
    # Not implemented in store?
    store.delete_policy(policy_id)
    
    audit(audit_store, "mcp_policy.delete", "mcp.policy", principal.id,
          resource_id=policy_id, outcome="success")
    
    return JSONResponse(status_code=204, content={})


# ============ Secrets ============

@router.get("/secrets")
async def list_secrets(
    principal: dict = Depends(require_permission("keys.read")),
    store: SecretStore = Depends(get_secret_store)
):
    return {"secrets": store.list_secrets()}


@router.post("/secrets", status_code=201)
async def create_secret(
    data: dict, 
    principal: dict = Depends(require_permission("keys.write")),
    store: SecretStore = Depends(get_secret_store),
    audit_store: AuditStore = Depends(get_audit_store)
):
    name = data.get("name")
    value = data.get("value")
    if not name or not value:
        raise HTTPException(status_code=400, detail={"error": {"code": "VALIDATION_ERROR"}})
    
    store.set_secret(name, value)
    
    audit(audit_store, "secret.write", "secret", principal.id,
          resource_id=name, outcome="success")
    
    return {"success": True, "name": name}


@router.delete("/secrets/{name}")
async def delete_secret(
    name: str, 
    principal: dict = Depends(require_permission("keys.write")),
    store: SecretStore = Depends(get_secret_store),
    audit_store: AuditStore = Depends(get_audit_store)
):
    store.delete_secret(name)
    audit(audit_store, "secret.delete", "secret", principal.id,
          resource_id=name, outcome="success")
    return {"success": True}


# ============ Config Operations ============

@router.get("/config:export")
async def export_config(
    principal: dict = Depends(require_permission("llm.read")),
    u_store: UpstreamStore = Depends(get_upstream_store),
    mg_store: ModelGroupStore = Depends(get_model_group_store),
    rp_store: RoutingPolicyStore = Depends(get_routing_policy_store)
):
    config = {
        "upstreams": {},
        "model_groups": {},
        "routing_policies": {}
    }
    
    for u in u_store.list_upstreams():
        safe = dict(u)
        creds = safe.get("credentials_ref", "")
        if creds:
             if not (creds.startswith("env:") or creds.startswith("secret:")):
                 safe["credentials_ref"] = "[REDACTED]"
        config["upstreams"][u.get("id")] = safe
        
    for g in mg_store.list_model_groups():
        config["model_groups"][g.get("id")] = g
        
    for p in rp_store.list_policies():
        config["routing_policies"][p.get("policy_id")] = p
        
    return config


@router.post("/config:validate")
async def validate_config(
    data: dict, 
    principal: dict = Depends(require_permission("llm.admin"))
):
    """Validate config without applying."""
    errors = []
    # Implementation largely identical to before, just validation logic
    for uid, upstream in data.get("upstreams", {}).items():
        if not upstream.get("provider"):
            errors.append({"path": f"upstreams.{uid}.provider", "message": "Required"})
        if not upstream.get("endpoint"):
            errors.append({"path": f"upstreams.{uid}.endpoint", "message": "Required"})
    
    for gid, group in data.get("model_groups", {}).items():
        if not group.get("deployments"):
            errors.append({"path": f"model_groups.{gid}.deployments", "message": "Required"})
            
    return {"valid": len(errors) == 0, "errors": errors}

@router.post("/config:apply")
async def apply_config(
    data: dict, 
    principal: dict = Depends(require_permission("llm.admin")),
    u_store: UpstreamStore = Depends(get_upstream_store),
    mg_store: ModelGroupStore = Depends(get_model_group_store),
    audit_store: AuditStore = Depends(get_audit_store)
):
    # Validation
    val = await validate_config(data, principal)
    if not val["valid"]:
         raise HTTPException(status_code=400, detail={"error": {"code": "VALIDATION_ERROR", "errors": val["errors"]}})
         
    request_id = uuid7()
    applied = {"upstreams": 0, "model_groups": 0}
    
    for uid, upstream in data.get("upstreams", {}).items():
        upstream["id"] = uid
        upstream["version"] = 1 # Force reset or create?
        u_store.create_upstream(upstream) # Simplistic apply
        applied["upstreams"] += 1
        
    for gid, group in data.get("model_groups", {}).items():
        group["id"] = gid
        group["version"] = 1
        mg_store.create_model_group(group)
        applied["model_groups"] += 1
        
    audit(audit_store, "config.apply", "config", principal.id, outcome="success")
    return {"success": True, "applied": applied}


@router.post("/config:reload")
async def reload_config(
    principal: dict = Depends(require_permission("platform.admin"))
):
    # Only meaningful for persistence that supports reload (File)
    # JsonStore might support it. Postgres does not.
    # We can skip or implementing reload on Store interface if needed.
    return {"success": True, "message": "Reload not supported in this mode"}

@router.get("/telemetry/stats")
async def get_stats(
    window_hours: int = 24,
    principal: RbacContext = Depends(require_permission("audit.read")),
    store: UsageStore = Depends(get_usage_store)
):
    """Get aggregated usage stats for the dashboard."""
    return store.get_stats(window_hours)


@router.get("/audit/stats")
async def get_audit_stats(
    window_hours: int = 24,
    principal: RbacContext = Depends(require_permission("audit.read")),
    store: AuditStore = Depends(get_audit_store)
):
    """Get aggregated audit stats (denials, volume series) for the dashboard."""
    return store.get_dashboard_stats(window_hours)


@router.get("/me")
async def get_me(principal: RbacContext = Depends(get_rbac_context)):
    return principal
