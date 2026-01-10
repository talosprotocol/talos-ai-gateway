"""Public AI API Router - OpenAI Compatible with Real Upstream Calls."""
from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel
from typing import List, Optional, Dict, Any
import uuid
import time
from datetime import datetime

from app.middleware.auth_public import get_auth_context, require_scope, AuthContext
from app.middleware.ratelimit import check_rate_limit
from app.domain.router_ai import router as llm_router
from app.adapters.upstreams_ai.client import (
    invoke_openai_compatible, 
    get_api_key,
    UpstreamError,
    UpstreamRateLimitError,
    UpstreamServerError
)
from app.adapters.audit.audit import emit_event

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
    auth: AuthContext = Depends(require_scope("llm:invoke"))
):
    """OpenAI-compatible chat completions endpoint."""
    request_id = str(uuid.uuid4())
    start_time = time.time()
    
    # Check if model group is allowed
    model_group_id = request.model
    if not auth.can_access_model_group(model_group_id):
        emit_event("denied", "llm", request_id, auth.key_id, auth.team_id, auth.org_id,
                   target=model_group_id, outcome="denied", error_code="MODEL_NOT_ALLOWED")
        raise HTTPException(status_code=403, detail={
            "error": {"code": "MODEL_NOT_ALLOWED", "message": f"Model {model_group_id} not allowed for this key"}
        })
    
    # Rate limit check
    rl_result = check_rate_limit(auth.key_id, auth.team_id, "llm", model_group_id)
    if not rl_result.allowed:
        emit_event("denied", "llm", request_id, auth.key_id, auth.team_id, auth.org_id,
                   target=model_group_id, outcome="denied", error_code="RATE_LIMITED")
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
    selection = llm_router.select_upstream(model_group_id, request_id)
    if not selection:
        emit_event("routing_decision", "llm", request_id, auth.key_id, auth.team_id, auth.org_id,
                   target=model_group_id, outcome="error", error_code="NO_UPSTREAM")
        raise HTTPException(status_code=502, detail={
            "error": {"code": "UPSTREAM_5XX", "message": "No available upstream for model group"}
        })
    
    upstream = selection["upstream"]
    model_name = selection["model_name"]
    
    # Emit routing decision
    emit_event("routing_decision", "llm", request_id, auth.key_id, auth.team_id, auth.org_id,
               target=model_group_id, outcome="success", policy_version=selection.get("policy_version"))
    
    # Get API key for upstream
    api_key = get_api_key(upstream.get("credentials_ref", ""))
    
    # If no API key configured, return mock response
    if not api_key:
        latency_ms = int((time.time() - start_time) * 1000)
        emit_event("invoke_result", "llm", request_id, auth.key_id, auth.team_id, auth.org_id,
                   target=model_group_id, outcome="success", latency_ms=latency_ms)
        return {
            "id": f"chatcmpl-{request_id[:8]}",
            "object": "chat.completion",
            "created": int(datetime.utcnow().timestamp()),
            "model": model_name,
            "choices": [{
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": f"[Mock - no API key] Response via {upstream['id']}"
                },
                "finish_reason": "stop"
            }],
            "usage": {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20}
        }
    
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
        
        # Emit success
        emit_event("invoke_result", "llm", request_id, auth.key_id, auth.team_id, auth.org_id,
                   target=model_group_id, outcome="success", latency_ms=latency_ms)
        
        # Add gateway headers
        response.headers["X-Request-Id"] = request_id
        response.headers["X-Upstream-Id"] = upstream["id"]
        response.headers["X-Latency-Ms"] = str(latency_ms)
        
        return result
        
    except UpstreamRateLimitError:
        llm_router.mark_upstream_failed(upstream["id"])
        emit_event("invoke_result", "llm", request_id, auth.key_id, auth.team_id, auth.org_id,
                   target=model_group_id, outcome="error", error_code="UPSTREAM_RATE_LIMITED")
        raise HTTPException(status_code=429, detail={
            "error": {"code": "RATE_LIMITED", "message": "Upstream rate limited"}
        })
        
    except UpstreamServerError as e:
        llm_router.mark_upstream_failed(upstream["id"])
        emit_event("invoke_result", "llm", request_id, auth.key_id, auth.team_id, auth.org_id,
                   target=model_group_id, outcome="error", error_code="UPSTREAM_5XX")
        raise HTTPException(status_code=502, detail={
            "error": {"code": "UPSTREAM_5XX", "message": str(e)}
        })
        
    except UpstreamError as e:
        emit_event("invoke_result", "llm", request_id, auth.key_id, auth.team_id, auth.org_id,
                   target=model_group_id, outcome="error", error_code="UPSTREAM_ERROR")
        raise HTTPException(status_code=502, detail={
            "error": {"code": "UPSTREAM_5XX", "message": str(e)}
        })
        
    except Exception as e:
        emit_event("invoke_result", "llm", request_id, auth.key_id, auth.team_id, auth.org_id,
                   target=model_group_id, outcome="error", error_code="INTERNAL")
        raise HTTPException(status_code=500, detail={
            "error": {"code": "INTERNAL", "message": "Internal gateway error"}
        })


@router.get("/models")
async def list_models(auth: AuthContext = Depends(require_scope("llm:invoke"))):
    """List allowed models for the authenticated key."""
    all_groups = llm_router.list_model_groups()
    
    # Filter by key's allowed model groups
    if "*" in auth.allowed_model_groups:
        allowed = all_groups
    else:
        allowed = [g for g in all_groups if g["id"] in auth.allowed_model_groups]
    
    return {
        "object": "list",
        "data": [
            {
                "id": g["id"],
                "object": "model",
                "created": 1700000000,
                "owned_by": "talos"
            }
            for g in allowed
        ]
    }
