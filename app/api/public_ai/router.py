"""Public AI API Router - OpenAI Compatible with Real Upstream Calls."""
from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel
from typing import List, Optional, Dict, Any
import uuid
import time
from datetime import datetime

from app.middleware.auth_public import get_auth_context, require_scope, AuthContext
# check_rate_limit removed
from app.dependencies import (
    get_routing_service, get_model_group_store, get_audit_store, 
    get_rate_limit_store, get_usage_store
)
from app.domain.routing import RoutingService
from app.domain.interfaces import ModelGroupStore, AuditStore, RateLimitStore, UsageStore
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
          resource_id: str = None, outcome: str = "success", **details):
    event = {
        "event_id": str(uuid.uuid4()),
        "timestamp": datetime.utcnow(),
        "principal_id": principal_id,
        "action": action,
        "resource_type": resource_type,
        "resource_id": resource_id,
        "status": outcome,
        "details": details
    }
    store.append_event(event)
    

def record_usage(store: UsageStore, auth: AuthContext, model_group_id: str, 
                 status: str, latency_ms: int, input_tokens: int = 0, output_tokens: int = 0):
    store.record_usage({
        "id": str(uuid.uuid4()),
        "key_id": auth.key_id,
        "team_id": auth.team_id,
        "org_id": auth.org_id,
        "surface": "llm",
        "target": model_group_id,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "latency_ms": latency_ms,
        "status": status
    })

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
    auth: AuthContext = Depends(require_scope("llm:invoke")),
    routing_service: RoutingService = Depends(get_routing_service),
    audit_store: AuditStore = Depends(get_audit_store),
    rl_store: RateLimitStore = Depends(get_rate_limit_store),
    usage_store: UsageStore = Depends(get_usage_store)
):
    """OpenAI-compatible chat completions endpoint."""
    request_id = str(uuid.uuid4())
    start_time = time.time()
    
    # Check if model group is allowed
    model_group_id = request.model
    if not auth.can_access_model_group(model_group_id):
        audit(audit_store, "denied", "llm", auth.key_id, model_group_id, "denied", error_code="MODEL_NOT_ALLOWED")
        raise HTTPException(status_code=403, detail={
            "error": {"code": "MODEL_NOT_ALLOWED", "message": f"Model {model_group_id} not allowed for this key"}
        })
    
    # Rate limit check
    rpm_limit = int(os.getenv("DEFAULT_RPM", "60"))
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
        raise HTTPException(status_code=400, detail={
            "error": {"code": "STREAMING_NOT_SUPPORTED", "message": "Streaming not yet implemented"}
        })
    
    # Select upstream via router
    selection = routing_service.select_upstream(model_group_id, request_id)
    if not selection:
        audit(audit_store, "routing_decision", "llm", auth.key_id, model_group_id, "error", error_code="NO_UPSTREAM")
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
    
    # Mock Response Logic
    if requires_auth and not api_key:
        latency_ms = int((time.time() - start_time) * 1000)
        audit(audit_store, "invoke_result", "llm", auth.key_id, model_group_id, "success")
        res = {
            "id": f"chatcmpl-{request_id[:8]}",
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
        
        record_usage(usage_store, auth, model_group_id, "success", latency_ms, 10, 10)
        return res
    
    # Make real upstream call
    try:
        messages = [{"role": m.role, "content": m.content} for m in request.messages]
        
        result = await invoke_openai_compatible(
            endpoint=upstream["endpoint"],
            model_name=model_name,
            messages=messages,
            api_key=api_key,
            temperature=request.temperature,
            max_tokens=request.max_tokens
        )
        
        latency_ms = int((time.time() - start_time) * 1000)
        record_usage(
            usage_store, auth, model_group_id, "success", latency_ms,
            result.get("usage", {}).get("prompt_tokens", 0),
            result.get("usage", {}).get("completion_tokens", 0)
        )
        
        audit(audit_store, "invoke_result", "llm", auth.key_id, model_group_id, "success")
        
        # Add gateway headers
        response.headers["X-Request-Id"] = request_id
        response.headers["X-Upstream-Id"] = upstream["id"]
        response.headers["X-Latency-Ms"] = str(latency_ms)
        
        return result
        
    except UpstreamRateLimitError:
        latency_ms = int((time.time() - start_time) * 1000)
        record_usage(usage_store, auth, model_group_id, "denied", latency_ms)
        routing_service.mark_failure(upstream["id"])
        audit(audit_store, "invoke_result", "llm", auth.key_id, model_group_id, "error", error_code="UPSTREAM_RATE_LIMITED")
        raise HTTPException(status_code=429, detail={
            "error": {"code": "RATE_LIMITED", "message": "Upstream rate limited"}
        })
        
    except UpstreamServerError as e:
        latency_ms = int((time.time() - start_time) * 1000)
        record_usage(usage_store, auth, model_group_id, "error", latency_ms)
        routing_service.mark_failure(upstream["id"])
        audit(audit_store, "invoke_result", "llm", auth.key_id, model_group_id, "error", error_code="UPSTREAM_5XX", details={"message": str(e)})
        raise HTTPException(status_code=502, detail={
            "error": {"code": "UPSTREAM_5XX", "message": str(e)}
        })
        
    except UpstreamError as e:
        latency_ms = int((time.time() - start_time) * 1000)
        record_usage(usage_store, auth, model_group_id, "error", latency_ms)
        audit(audit_store, "invoke_result", "llm", auth.key_id, model_group_id, "error", error_code="UPSTREAM_ERROR", details={"message": str(e)})
        raise HTTPException(status_code=502, detail={
            "error": {"code": "UPSTREAM_5XX", "message": str(e)}
        })
        
    except Exception as e:
        latency_ms = int((time.time() - start_time) * 1000)
        record_usage(usage_store, auth, model_group_id, "error", latency_ms)
        audit(audit_store, "invoke_result", "llm", auth.key_id, model_group_id, "error", error_code="INTERNAL", details={"message": str(e)})
        raise HTTPException(status_code=500, detail={
            "error": {"code": "INTERNAL", "message": "Internal gateway error"}
        })


@router.get("/models")
async def list_models(
    auth: AuthContext = Depends(require_scope("llm:invoke")),
    store: ModelGroupStore = Depends(get_model_group_store)
):
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
