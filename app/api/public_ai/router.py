"""Public AI API Router - OpenAI Compatible with Real Upstream Calls."""
from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel
from typing import List, Optional, Dict, Any
from app.utils.id import uuid7
import json
import time
from datetime import datetime
from decimal import Decimal

from app.middleware.auth_public import require_scope, AuthContext
# check_rate_limit removed
from app.dependencies import (
    get_routing_service, get_model_group_store, get_audit_store, 
    get_rate_limit_store, get_budget_service, get_usage_manager
)
from app.domain.routing import RoutingService
from app.domain.interfaces import ModelGroupStore, AuditStore, RateLimitStore
from app.domain.budgets.service import BudgetService, BudgetExceededError
from app.domain.usage.manager import UsageManager
from app.adapters.redis.client import rate_limit_key
from app.adapters.upstreams_ai.client import (
    invoke_openai_compatible, 
    get_api_key,
    UpstreamError,
    UpstreamRateLimitError,
    UpstreamServerError
)
import os


# Helper for audit - can be moved to dependency or service
def audit(store: AuditStore, action: str, resource_type: str, principal_id: str, 
          resource_id: Optional[str] = None, outcome: str = "success", **details: Any) -> None:
    event = {
        "event_id": uuid7(),
        "timestamp": datetime.utcnow(),
        "principal_id": principal_id,
        "action": action,
        "resource_type": resource_type,
        "resource_id": resource_id,
        "status": outcome,
        "schema_id": "talos.audit.ai.v1",
        "schema_version": 1,
        "details": details
    }
    store.append_event(event)

router = APIRouter()


class ChatMessage(BaseModel):
    role: str
    content: str


class ChatCompletionRequest(BaseModel):
    model: str
    messages: List[ChatMessage]
    temperature: Optional[float] = 1.0
    max_tokens: Optional[int] = None
    stream: Optional[bool] = False


@router.post("/chat/completions")
async def chat_completions(
    request: ChatCompletionRequest,
    response: Response,
    auth: AuthContext = Depends(require_scope("llm.invoke")),
    routing_service: RoutingService = Depends(get_routing_service),
    audit_store: AuditStore = Depends(get_audit_store),
    rl_store: RateLimitStore = Depends(get_rate_limit_store),
    budget_service: BudgetService = Depends(get_budget_service),
    usage_manager: UsageManager = Depends(get_usage_manager)
) -> Any:
    """OpenAI-compatible chat completions endpoint."""
    request_id = uuid7()
    start_time = time.time()
    
    # Authenticate (Phase 7 RBAC)
    
    # Check if model group is allowed
    model_group_id = request.model
    if not auth.can_access_model_group(model_group_id):
        audit(audit_store, "denied", "llm", auth.key_id, model_group_id, "denied", error_code="MODEL_NOT_ALLOWED")
        raise HTTPException(status_code=403, detail={
            "error": {"code": "MODEL_NOT_ALLOWED", "message": f"Model {model_group_id} not allowed for this key"}
        })
    
    # Rate limit check
    if os.getenv("RATE_LIMIT_ENABLED", "true").lower() == "true":
        rpm_limit = int(os.getenv("DEFAULT_RPM", "600"))
        key = rate_limit_key(auth.team_id, auth.key_id, "llm", model_group_id)
        
        rl_result = await rl_store.check_limit(key, rpm_limit)
        
        if not rl_result.allowed:
            audit(audit_store, "denied", "llm", auth.key_id, model_group_id, "denied", error_code="RATE_LIMITED")
            raise HTTPException(status_code=429, detail={
                "error": {"code": "RATE_LIMITED", "message": "Rate limit exceeded"}
            }, headers={
                "X-RateLimit-Limit": str(rl_result.limit),
                "X-RateLimit-Remaining": "0",
                "X-RateLimit-Reset": rl_result.reset_at.isoformat() + "Z"
            })
    
    # Handle streaming
    if request.stream:
        # TODO: Implement streaming with settle-on-end logic in Phase 3
        raise HTTPException(status_code=400, detail={
            "error": {"code": "STREAMING_NOT_SUPPORTED", "message": "Streaming not yet implemented"}
        })
    
    # --- Phase 15: Budget Enforcement ---
    latency_ms = 0
    status = "success"
    prompt_tokens = 0
    completion_tokens = 0
    cost_usd = Decimal("0")
    
    # 1. Prepare Budget Context
    # Use max_tokens or default
    MAX_TOKENS_DEFAULT = 4096
    MAX_TOKENS_CAP = 10000 # Safety cap for estimation
    
    req_max_tokens = request.max_tokens or auth.max_tokens_default or MAX_TOKENS_DEFAULT
    estimate_tokens = min(req_max_tokens, MAX_TOKENS_CAP)
    
    estimate_usd, _ = budget_service.pricing.get_llm_cost(
        model_name=model_group_id,
        provider="unknown", 
        group_id=model_group_id,
        input_tokens=estimate_tokens, 
        output_tokens=0 
    )
    
    limit_team = Decimal(str(auth.team_budget_metadata.get("limit_usd", "0")))
    limit_key = Decimal(str(auth.budget_metadata.get("limit_usd", "0")))
    overdraft = Decimal(auth.overdraft_usd)

    # 2. Reservation (Only for Hard mode AND Non-streaming)
    # Phase 15 rule: If stream:true, bypass lock/reservation (WARN behavior)
    reserving = (auth.budget_mode == "hard" and not request.stream)
    
    if reserving:
        try:
            budget_headers = await budget_service.reserve(
                request_id=str(request_id),
                team_id=auth.team_id,
                key_id=auth.key_id,
                budget_mode="hard",
                estimate_usd=estimate_usd,
                limit_usd_team=limit_team,
                limit_usd_key=limit_key,
                overdraft_usd=overdraft
            )
            for k, v in budget_headers.items():
                response.headers[k] = v
        except BudgetExceededError as e:
            audit(audit_store, "denied", "llm", auth.key_id, model_group_id, "denied", error_code="BUDGET_EXCEEDED")
            raise HTTPException(status_code=402, detail={
                "error": {
                    "code": "BUDGET_EXCEEDED", 
                    "message": e.message,
                    "remaining_usd": str(e.remaining),
                    "limit_usd": str(e.limit)
                }
            })
    else:
        # WARN/OFF mode or Forced WARN (streaming)
        budget_headers = await budget_service.reserve(
            request_id=str(request_id),
            team_id=auth.team_id,
            key_id=auth.key_id,
            budget_mode=auth.budget_mode if not request.stream else "warn",
            estimate_usd=estimate_usd,
            limit_usd_team=limit_team,
            limit_usd_key=limit_key,
            overdraft_usd=overdraft
        )
        for k, v in budget_headers.items():
            response.headers[k] = v

    # Select upstream via router
    selection = routing_service.select_upstream(model_group_id, str(request_id))
    if not selection:
        audit(audit_store, "routing_decision", "llm", auth.key_id, model_group_id, "error", error_code="NO_UPSTREAM")
        # Release budget reservation if any
        if reserving:
            await budget_service.settle(str(request_id), auth.team_id, auth.key_id, estimate_usd, Decimal("0"))
        raise HTTPException(status_code=502, detail={
            "error": {"code": "UPSTREAM_5XX", "message": "No available upstream for model group"}
        })
    
    upstream = selection["upstream"]
    model_name = selection["model_name"]
    
    # Emit routing decision
    audit(audit_store, "routing_decision", "llm", auth.key_id, model_group_id, "success")
    
    # Get API key
    api_key = get_api_key(upstream.get("credentials_ref", ""))
    provider = upstream.get("provider", "openai")
    
    providers_requiring_auth = {"openai", "azure", "anthropic", "google", "groq", "together", "mistral", "deepinfra", "sambanova", "cerebras"}
    requires_auth = provider in providers_requiring_auth
    
    try:
        # Mock Response Logic
        if requires_auth and not api_key:
            latency_ms = int((time.time() - start_time) * 1000)
            res = {
                "id": f"chatcmpl-{str(request_id)[:8]}",
                "object": "chat.completion",
                "created": int(datetime.utcnow().timestamp()),
                "model": model_name,
                "choices": [{
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": f"[Mock - no API key configured for {provider}] Response via {upstream['id']}"
                    },
                    "finish_reason": "stop"
                }],
                "usage": {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20}
            }
            prompt_tokens = 10
            completion_tokens = 10
            
            # Record & Settle
            await usage_manager.record_event(
                request_id=str(request_id),
                team_id=auth.team_id,
                key_id=auth.key_id,
                org_id=auth.org_id or "",
                surface="llm",
                target=model_group_id,
                input_tokens=prompt_tokens,
                output_tokens=completion_tokens,
                latency_ms=latency_ms,
                status="success",
                token_count_source="estimated",
                estimate_usd=estimate_usd
            )
            
            return res
        
        # Real upstream call
        messages = [{"role": m.role, "content": m.content} for m in request.messages]
        
        result = await invoke_openai_compatible(
            endpoint=upstream["endpoint"], 
            model_name=model_name, 
            messages=messages, 
            api_key=api_key,
            temperature=request.temperature or 0.7,
            max_tokens=request.max_tokens
        )
        
        latency_ms = int((time.time() - start_time) * 1000)
        prompt_tokens = result.get("usage", {}).get("prompt_tokens", 0)
        completion_tokens = result.get("usage", {}).get("completion_tokens", 0)
        
        audit(audit_store, "invoke_result", "llm", auth.key_id, model_group_id, "success")
        
        response.headers["X-Request-Id"] = str(request_id)
        response.headers["X-Upstream-Id"] = upstream["id"]
        response.headers["X-Latency-Ms"] = str(latency_ms)
        
        # Record & Settle
        await usage_manager.record_event(
            request_id=str(request_id),
            team_id=auth.team_id,
            key_id=auth.key_id,
            org_id=auth.org_id or "",
            surface="llm",
            target=model_group_id,
            provider=provider or "unknown",
            input_tokens=prompt_tokens,
            output_tokens=completion_tokens,
            latency_ms=latency_ms,
            status="success",
            token_count_source="provider_reported",
            estimate_usd=estimate_usd
        )

        return result

    except Exception as e:
        latency_ms = int((time.time() - start_time) * 1000)
        status = "error"
        # Determine specific error code
        err_code = "INTERNAL"
        if isinstance(e, UpstreamRateLimitError): err_code = "UPSTREAM_RATE_LIMITED"
        elif isinstance(e, UpstreamServerError): err_code = "UPSTREAM_5XX"
        elif isinstance(e, UpstreamError): err_code = "UPSTREAM_ERROR"
        
        # Record Failure & Settle with 0 cost
        await usage_manager.record_event(
             request_id=str(request_id),
             team_id=auth.team_id,
             key_id=auth.key_id,
             org_id=auth.org_id or "",
             surface="llm",
             target=model_group_id,
             input_tokens=0,
             output_tokens=0,
             latency_ms=latency_ms,
             status="error",
             token_count_source="unknown",
             estimate_usd=estimate_usd
        )
        
        if isinstance(e, HTTPException): raise e
        
        audit(audit_store, "invoke_result", "llm", auth.key_id, model_group_id, "error", error_code=err_code, details={"message": str(e)})
        
        # Re-raise appropriate HTTP Exception
        if err_code == "UPSTREAM_RATE_LIMITED":
            raise HTTPException(status_code=429, detail={"error": {"code": "RATE_LIMITED", "message": "Upstream rate limited"}})
        if err_code == "UPSTREAM_5XX":
            raise HTTPException(status_code=502, detail={"error": {"code": "UPSTREAM_5XX", "message": str(e)}})
            
        raise HTTPException(status_code=500, detail={"error": {"code": "INTERNAL", "message": f"Internal gateway error: {str(e)}"} })


@router.get("/models")
async def list_models(
    auth: AuthContext = Depends(require_scope("llm.read")),
    store: ModelGroupStore = Depends(get_model_group_store)
) -> Dict[str, Any]:
    """List allowed models for the authenticated key."""
    all_groups = store.list_model_groups()
    
    # Filter by key's allowed model groups
    if "*" in auth.allowed_model_groups:
        allowed = all_groups
    else:
        allowed = [g for g in all_groups if g.get("id") in auth.allowed_model_groups]
    
    return {
        "object": "list",
        "data": [
            {
                "id": g.get("id"),
                "object": "model",
                "created": 1700000000,
                "owned_by": "talos"
            }
            for g in allowed
        ]
    }
