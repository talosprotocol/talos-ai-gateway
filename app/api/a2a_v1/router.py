"""A2A v1 protocol routes."""

import json
from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response

from app.adapters.mcp.client import McpClient
from app.api.a2a.routes import get_jsonrpc_auth_context
from app.api.a2a_v1.agent_card import build_agent_card
from app.api.a2a_v1.service import A2AV1Service
from app.dependencies import (
    get_audit_logger,
    get_audit_store,
    get_capability_validator,
    get_mcp_client,
    get_rate_limit_store,
    get_routing_service,
    get_task_store,
    get_usage_store,
)
from app.domain.audit import AuditLogger
from app.domain.interfaces import AuditStore, RateLimitStore, TaskStore, UsageStore
from app.domain.routing import RoutingService
from app.settings import settings


router = APIRouter()


def _ensure_v1_enabled() -> None:
    if settings.a2a_protocol_mode == "compat":
        raise HTTPException(status_code=404, detail="A2A v1 disabled")


def _ensure_root_rpc_compat_enabled() -> None:
    if settings.a2a_protocol_mode != "dual":
        raise HTTPException(status_code=404, detail="A2A root JSON-RPC compatibility disabled")


async def _handle_rpc_request(
    request: Request,
    routing_service: RoutingService,
    audit_store: AuditStore,
    rl_store: RateLimitStore,
    usage_store: UsageStore,
    task_store: TaskStore,
    mcp_client: McpClient,
    capability_validator: Any,
    audit_logger: AuditLogger,
) -> Any:
    auth = await get_jsonrpc_auth_context(
        request=request,
        authorization=request.headers.get("Authorization"),
    )

    payload: Any = {}
    try:
        raw_body = await request.body()
        if raw_body:
            payload = json.loads(raw_body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        return {
            "jsonrpc": "2.0",
            "id": None,
            "error": {
                "code": -32700,
                "message": "Parse error",
                "data": {"details": str(exc)},
            },
        }

    service = A2AV1Service(
        auth=auth,
        routing_service=routing_service,
        audit_store=audit_store,
        rl_store=rl_store,
        usage_store=usage_store,
        task_store=task_store,
        mcp_client=mcp_client,
        capability_validator=capability_validator,
        request=request,
        audit_logger=audit_logger,
    )
    res = await service.handle_rpc(payload)

    if isinstance(res, Response):
        return res

    return res


@router.post("/rpc", name="a2a_v1_rpc")
async def rpc_entrypoint(
    request: Request,
    routing_service: RoutingService = Depends(get_routing_service),
    audit_store: AuditStore = Depends(get_audit_store),
    rl_store: RateLimitStore = Depends(get_rate_limit_store),
    usage_store: UsageStore = Depends(get_usage_store),
    task_store: TaskStore = Depends(get_task_store),
    mcp_client: McpClient = Depends(get_mcp_client),
    capability_validator: Any = Depends(get_capability_validator),
    audit_logger: AuditLogger = Depends(get_audit_logger),
) -> Dict[str, Any]:
    """Expose the A2A v1 RPC endpoint behind a protocol-mode gate."""
    _ensure_v1_enabled()
    return await _handle_rpc_request(
        request=request,
        routing_service=routing_service,
        audit_store=audit_store,
        rl_store=rl_store,
        usage_store=usage_store,
        task_store=task_store,
        mcp_client=mcp_client,
        capability_validator=capability_validator,
        audit_logger=audit_logger,
    )


@router.post("/", name="a2a_v1_root_rpc_compat")
async def root_rpc_compat_entrypoint(
    request: Request,
    routing_service: RoutingService = Depends(get_routing_service),
    audit_store: AuditStore = Depends(get_audit_store),
    rl_store: RateLimitStore = Depends(get_rate_limit_store),
    usage_store: UsageStore = Depends(get_usage_store),
    task_store: TaskStore = Depends(get_task_store),
    mcp_client: McpClient = Depends(get_mcp_client),
    capability_validator: Any = Depends(get_capability_validator),
    audit_logger: AuditLogger = Depends(get_audit_logger),
) -> Dict[str, Any]:
    """Expose a root-path JSON-RPC alias for upstream v0.3-era interop in dual mode."""
    _ensure_v1_enabled()
    _ensure_root_rpc_compat_enabled()
    return await _handle_rpc_request(
        request=request,
        routing_service=routing_service,
        audit_store=audit_store,
        rl_store=rl_store,
        usage_store=usage_store,
        task_store=task_store,
        mcp_client=mcp_client,
        capability_validator=capability_validator,
        audit_logger=audit_logger,
    )


@router.get("/extendedAgentCard", name="a2a_v1_extended_agent_card")
async def get_extended_agent_card(request: Request) -> Dict[str, Any]:
    """Expose the authenticated extended Agent Card for A2A v1 clients."""
    _ensure_v1_enabled()

    await get_jsonrpc_auth_context(
        request=request,
        authorization=request.headers.get("Authorization"),
    )

    return build_agent_card(
        request,
        include_compat_extension=settings.a2a_protocol_mode == "dual",
        include_extended_details=True,
    )


@router.get("/protocol/metadata", name="a2a_v1_protocol_metadata")
async def get_protocol_metadata() -> Dict[str, Any]:
    """Expose protocol version and capabilities for A2A v1."""
    return {
        "protocol": "a2a",
        "version": "1.0",
        "capabilities": [
            "rpc",
            "streaming",
            "push_notifications",
            "agent_card_extension"
        ]
    }
