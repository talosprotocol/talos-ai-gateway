import os
from app.utils.id import uuid7
import time
from datetime import datetime, timezone
from typing import Any, Dict, Optional, cast

from app.api.a2a.jsonrpc import validator, JsonRpcException
from app.domain.a2a.mapper_llm import map_input_to_llm_messages, map_llm_response_to_task
from app.domain.a2a.mapper_mcp import McpMapper
from app.domain.routing import RoutingService
import json
from fastapi.concurrency import run_in_threadpool
from app.domain.interfaces import AuditStore, RateLimitStore, UsageStore, TaskStore
from app.middleware.auth_public import AuthContext
from app.adapters.upstreams_ai.client import (
    UpstreamClientError,
    UpstreamRateLimitError,
    UpstreamServerError,
    UpstreamTransportError,
    get_api_key,
    invoke_openai_compatible,
)
from app.adapters.mcp.client import McpClient
from redis.asyncio import Redis
from app.adapters.redis.client import get_redis_client, rate_limit_key
from app.domain.a2a.push_notifications import schedule_push_notifications

ALLOWED_METHODS = {"tasks.send", "tasks.get", "tasks.cancel"}
AUTH_REQUIRED_PROVIDERS = {
    "anthropic",
    "azure",
    "cerebras",
    "deepinfra",
    "google",
    "groq",
    "mistral",
    "openai",
    "sambanova",
    "together",
}


def _has_scope(scopes: object, scope: str) -> bool:
    if not isinstance(scopes, list):
        return False

    for granted in scopes:
        if not isinstance(granted, str):
            continue
        if granted in {"*", "*:*", scope}:
            return True
        if granted.endswith(".*") and scope.startswith(granted[:-1]):
            return True
        if granted.endswith(":*") and scope.startswith(f"{granted[:-2]}:"):
            return True
    return False


def _has_any_scope(scopes: object, *required: str) -> bool:
    return any(_has_scope(scopes, scope) for scope in required)


def _simulated_llm_enabled() -> bool:
    return os.getenv("A2A_SIMULATED_LLM_RESPONSES", "false").lower() == "true"


def _dev_mode_enabled() -> bool:
    return os.getenv("DEV_MODE", "false").lower() == "true"


def _build_simulated_completion(
    *,
    model_name: str,
    upstream_id: str,
    provider: str,
    messages: list[dict[str, Any]],
    reason: str,
) -> Dict[str, Any]:
    last_user_message = next(
        (
            str(message.get("content", "")).strip()
            for message in reversed(messages)
            if message.get("role") == "user"
        ),
        "",
    )
    suffix = f" Echo: {last_user_message}" if last_user_message else ""
    content = f"[Mock - {reason} via {upstream_id} ({provider}/{model_name})]{suffix}"
    return {
        "id": f"chatcmpl-{uuid7()[:8]}",
        "object": "chat.completion",
        "created": int(datetime.now(timezone.utc).timestamp()),
        "model": model_name,
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": content,
                },
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20},
    }


class A2ADispatcher:
    def __init__(
        self,
        auth: AuthContext,
        routing_service: RoutingService,
        audit_store: AuditStore,
        rl_store: RateLimitStore,
        usage_store: UsageStore,
        task_store: TaskStore,
        mcp_client: McpClient,
        capability_validator: Optional[Any] = None
    ):
        self.auth = auth
        self.routing = routing_service
        self.audit_store = audit_store
        self.rl_store = rl_store
        self.usage_store = usage_store
        self.task_store = task_store
        self.mcp_mapper = McpMapper(mcp_client, audit_store, capability_validator)
        self._redis_promise = None
        self.redis: Optional[Redis] = None

    async def _get_redis(self) -> Optional[Redis]:
        if self.redis is None:
            if self._redis_promise is None:
                self._redis_promise = get_redis_client()
            self.redis = await self._redis_promise
        return self.redis
        
    async def dispatch(self, payload: Dict[str, Any], capability: Optional[str] = None) -> Dict[str, Any]:
        """
        Main entry point for JSON-RPC dispatch.
        """
        request_id = payload.get("id")
        method = payload.get("method")
        normalized_payload = payload
        
        try:
            # 1. Validate Envelope
            validator.validate_request_envelope(payload)
            
            # 2. Check Allowlist
            if method not in ALLOWED_METHODS:
                raise JsonRpcException(-32601, "Method not found")

            if method == "tasks.send" and isinstance(payload.get("params"), dict):
                params_copy = dict(payload["params"])
                if "tool_call" in params_copy and not isinstance(params_copy["tool_call"], dict):
                    params_copy.pop("tool_call")
                normalized_payload = dict(payload)
                normalized_payload["params"] = params_copy
            
            # 3. Validate Method Request (Params)
            validator.validate_method_request(method, normalized_payload)
            
            params = normalized_payload.get("params", {})
            
            # 4. Dispatch
            result = None
            if method == "tasks.send":
                result = await self.handle_send(params, request_id, capability)
            elif method == "tasks.get":
                result = await self.handle_get(params)
            elif method == "tasks.cancel":
                result = await self.handle_cancel(params)
                
            # 5. Validate Response Structure (Optional/Strict)
            response_payload = {
                "jsonrpc": "2.0",
                "id": request_id,
                "result": result
            }
            
            # Strict validation for tasks.get results
            if method == "tasks.get":
                validator.validate_method_response(method, response_payload)
                
            return response_payload

        except JsonRpcException as e:
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {
                    "code": e.code,
                    "message": e.message,
                    "data": e.data
                }
            }
        except Exception as e:
            # Internal Error
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {
                    "code": -32603,
                    "message": "Internal error",
                    "data": {"details": str(e)}
                }
            }

    async def _publish_event(self, task_id: str, status: str, version: int, request_id: str) -> None:
        redis = await self._get_redis()
        if not redis:
            return
        
        event = {
            "event_id": f"{task_id}:{version}",
            "task_id": task_id,
            "status": status, 
            "version": version,
            "request_id": str(request_id),
            "updated_at": datetime.now(timezone.utc).isoformat()
        }
        
        try:
            # PubSub
            await redis.publish(f"a2a:tasks:{task_id}", json.dumps(event))
            
            # Cache Last Event (1h TTL)
            await redis.setex(f"a2a:last_event:{task_id}", 3600, json.dumps(event))
        except Exception:
            # Non-blocking failure
            pass

        try:
            task = await run_in_threadpool(self.task_store.get_task, task_id, self.auth.team_id)
            if not task:
                return

            configs = await run_in_threadpool(
                self.task_store.list_task_push_notification_configs,
                task_id,
                self.auth.team_id,
            )
            if not configs:
                return

            payload = {
                "statusUpdate": {
                    "taskId": task_id,
                    "contextId": task.get("request_meta", {}).get("context_id", task_id),
                    "status": {
                        "state": self._map_v1_state(status),
                        "timestamp": event["updated_at"],
                    },
                    "metadata": {
                        "eventId": event["event_id"],
                        "final": status in {"completed", "failed", "canceled", "rejected"},
                        "version": version,
                    },
                }
            }
            schedule_push_notifications(configs, payload)
        except Exception:
            pass

    async def handle_send(self, params: Dict[str, Any], request_id: Any, capability: Optional[str] = None) -> Dict[str, Any]:
        # Scope Check for A2A
        if not _has_any_scope(self.auth.scopes, "a2a.invoke", "a2a.send"):
             raise JsonRpcException(-32000, "Permission denied", data={"talos_code": "RBAC_DENIED", "details": "Missing A2A send scope"})
        
        task_id = str(params.get("task_id") or uuid7())
        
        # 1. Persist QUEUED
        # Safe Metadata Only
        raw_tool_call = params.get("tool_call")
        tool_call = raw_tool_call if isinstance(raw_tool_call, dict) else None
        profile = params.get("profile", {})
        request_meta = {
            "method": "tasks.send",
            "has_tool_call": bool(tool_call),
            "tool_name": tool_call.get("tool_name") if tool_call else None,
            "profile_id": profile.get("profile_id", "default"),
            "profile_version": profile.get("profile_version", "0.1"),
            "model_group_id": self.auth.allowed_model_groups[0] if self.auth.allowed_model_groups else "unknown",
            "origin_surface": "a2a"
        }
        if params.get("context_id"):
            request_meta["context_id"] = str(params["context_id"])
        if params.get("message_id"):
            request_meta["message_id"] = str(params["message_id"])
        
        request_meta = self._sanitize_request_meta(request_meta)
        input_redacted = params.get("input_redacted")
        if not isinstance(input_redacted, dict):
            input_redacted = None
        
        task_data = {
            "id": task_id,
            "team_id": self.auth.team_id,
            "key_id": self.auth.key_id,
            "org_id": self.auth.org_id,
            "request_id": str(request_id),
            "origin_surface": str(params.get("origin_surface") or "a2a"),
            "method": "tasks.send",
            "status": "queued",
            "version": 1,
            "request_meta": request_meta,
            "input_redacted": input_redacted,
            "created_at": datetime.now(timezone.utc),
            "updated_at": datetime.now(timezone.utc)
        }
        
        
        # Cast to Any to satisfy mypy argument check for run_in_threadpool
        # ideally task_data should be a valid TypedDict or Pydantic model
        await run_in_threadpool(self.task_store.create_task, cast(Any, task_data))
        current_version = 1

        push_notification_configs = params.get("push_notification_configs")
        if isinstance(push_notification_configs, list):
            for raw_config in push_notification_configs:
                if not isinstance(raw_config, dict):
                    continue
                await run_in_threadpool(
                    self.task_store.create_task_push_notification_config,
                    task_id,
                    self.auth.team_id,
                    raw_config,
                )
        
        # New: Check for explicit tool_call in params (Phase A2)
        
        # Rate Limiting
        rpm_limit = 60 
        key = rate_limit_key(self.auth.team_id, self.auth.key_id, "a2a", "invocations")
        rl_res = await self.rl_store.check_limit(key, rpm_limit)
        
        if not rl_res.allowed:
            self._audit("denied", "rate_limited", error_code="RATE_LIMITED")
            
            # Fail Task
            await run_in_threadpool(
                self.task_store.update_task_status, 
                task_id, "failed", 
                expected_version=current_version,
                error={"code": "RATE_LIMITED"}
            )
            
            raise JsonRpcException(-32000, "Rate limit exceeded", data={"talos_code": "RATE_LIMITED", "retry_after_ms": 1000})

        # 2. Update RUNNING
        current_version = await run_in_threadpool(
            self.task_store.update_task_status,
            task_id, "running",
            expected_version=current_version
        )
        await self._publish_event(task_id, "running", current_version, request_id)

        try:
            result_task = None
            
            # --- A2: MCP Path ---
            if tool_call:
                result_task = await self.mcp_mapper.execute_tool(
                    tool_call, 
                    self.auth, 
                    str(request_id),
                    capability=capability
                )

            else:
                # --- A1: LLM Path ---
                if "llm.invoke" not in self.auth.scopes:
                     raise JsonRpcException(-32000, "Permission denied", data={"talos_code": "RBAC_DENIED", "details": "Missing 'llm.invoke' scope"})
                
                if not self.auth.allowed_model_groups:
                     raise JsonRpcException(-32000, "No models allowed", data={"talos_code": "CONFIG_ERROR"})
                
                if "*" in self.auth.allowed_model_groups:
                    model_group_id = self.routing.default_model_group_id()
                else:
                    model_group_id = self.auth.allowed_model_groups[0]
                
                if not model_group_id:
                    raise JsonRpcException(-32000, "No models allowed", data={"talos_code": "CONFIG_ERROR"})
                
                # Audit Start
                self._audit("a2a.tasks.send", "started", resource_id=str(request_id))
                
                start_time = time.time()
                
                # Route
                routing_req_id = uuid7()
                selection = self.routing.select_upstream(model_group_id, routing_req_id)
                if not selection:
                    raise JsonRpcException(-32000, "No upstream available", data={"talos_code": "UPSTREAM_UNAVAILABLE"})
                
                upstream = selection["upstream"]
                model_name = selection["model_name"]
                provider = str(upstream.get("provider", "openai")).lower()
                api_key = get_api_key(upstream.get("credentials_ref", ""))
                
                llm_messages = map_input_to_llm_messages(params.get("input", []))

                if _simulated_llm_enabled():
                    result = _build_simulated_completion(
                        model_name=model_name,
                        upstream_id=str(upstream.get("id", "mock-upstream")),
                        provider=provider,
                        messages=llm_messages,
                        reason="A2A mock responses enabled",
                    )
                elif _dev_mode_enabled() and provider in AUTH_REQUIRED_PROVIDERS and not api_key:
                    result = _build_simulated_completion(
                        model_name=model_name,
                        upstream_id=str(upstream.get("id", "mock-upstream")),
                        provider=provider,
                        messages=llm_messages,
                        reason=f"no API key configured for {provider}",
                    )
                else:
                    result = await invoke_openai_compatible(
                        endpoint=upstream["endpoint"],
                        model_name=model_name,
                        messages=llm_messages,
                        api_key=api_key
                    )
                
                latency = int((time.time() - start_time) * 1000)
                
                self._record_usage(model_group_id, "success", latency, result)
                self._audit("a2a.tasks.send", "completed", resource_id=str(request_id))
                
                result_task = map_llm_response_to_task(result, params.get("profile", {}))

            # 3. Update COMPLETED
            # Overwrite task ID in result to match persistent ID
            result_task["task_id"] = task_id
            
            current_version = await run_in_threadpool(
                self.task_store.update_task_status,
                task_id, "completed",
                expected_version=current_version,
                result=result_task
            )
            await self._publish_event(task_id, "completed", current_version, request_id)
            
            return result_task

        except Exception as e:
            # 3. Update FAILED
            error_data = {"details": str(e)}
            if isinstance(e, JsonRpcException):
                error_data = e.data or {"code": e.code, "message": e.message}
            elif isinstance(e, UpstreamRateLimitError):
                error_data = self._upstream_error_data("UPSTREAM_RATE_LIMITED", e)
            elif isinstance(e, UpstreamServerError):
                error_data = self._upstream_error_data("UPSTREAM_5XX", e)
            elif isinstance(e, UpstreamClientError):
                error_data = self._upstream_error_data("UPSTREAM_4XX", e)
            elif isinstance(e, UpstreamTransportError):
                error_data = self._upstream_error_data("UPSTREAM_TRANSPORT_ERROR", e)
            
            await run_in_threadpool(
                self.task_store.update_task_status,
                task_id, "failed",
                expected_version=current_version,
                error=error_data
            )
            await self._publish_event(task_id, "failed", current_version + 1, request_id)

            if isinstance(e, JsonRpcException):
                raise e
            if isinstance(e, UpstreamRateLimitError):
                raise JsonRpcException(-32000, "Upstream rate limited", data=error_data)
            if isinstance(e, UpstreamServerError):
                raise JsonRpcException(-32000, "Upstream server error", data=error_data)
            if isinstance(e, UpstreamClientError):
                raise JsonRpcException(-32000, "Upstream rejected request", data=error_data)
            if isinstance(e, UpstreamTransportError):
                raise JsonRpcException(-32000, "Upstream transport error", data=error_data)
            raise JsonRpcException(-32000, "Internal error processing task", data={"talos_code": "INTERNAL_ERROR", "details": str(e)})


    async def handle_get(self, params: Dict[str, Any]) -> Dict[str, Any]:
        if not _has_any_scope(self.auth.scopes, "a2a.invoke", "a2a.get"):
             raise JsonRpcException(-32000, "Permission denied", data={"talos_code": "RBAC_DENIED"})

        raise JsonRpcException(-32000, "tasks.get not implemented", data={"talos_code": "NOT_IMPLEMENTED"})

    def _sanitize_request_meta(self, meta: Dict[str, Any]) -> Dict[str, Any]:
        ALLOWED_KEYS = {"method", "profile_id", "profile_version", "model_group_id", "has_tool_call", "tool_server_id", "tool_name", "origin_surface", "context_id", "message_id"}
        FORBIDDEN_KEYS = {"messages", "prompt", "input", "tool_input", "headers", "authorization", "api_key", "secret", "cookie"}
        
        sanitized = {}
        for k, v in meta.items():
            if k in FORBIDDEN_KEYS:
                # Log warning or just drop? Plan says "reject if forbidden keys present (optional)". 
                # We will strict reject for internal safety? No, just drop and ensure they don't get in.
                # Actually, user said: "reject if forbidden keys are present (optional, but recommended in prod)"
                # But request_meta is constructed by US in handle_send, not passed by user.
                # So we just need to ensure *we* don't put them in.
                continue
            if k in ALLOWED_KEYS:
                sanitized[k] = v
                
        # Deep scan check (Simulated for safety, but since we construct it, shallow check of keys is enough)
        # But if 'tool_name' contained a 'secret', that would be bad. 
        # Since we construct request_meta from params, we are safe if we only select known scalars.
        return sanitized

    async def handle_cancel(self, params: Dict[str, Any]) -> Dict[str, Any]:
        if not _has_any_scope(self.auth.scopes, "a2a.invoke", "a2a.cancel"):
             raise JsonRpcException(-32000, "Permission denied", data={"talos_code": "RBAC_DENIED"})

        raise JsonRpcException(-32000, "Cancellation not supported", data={"talos_code": "NOT_CANCELABLE"})

    def _audit(self, action: str, outcome: str, resource_id: Optional[str] = None, details: Optional[Dict[str, Any]] = None, error_code: Optional[str] = None) -> None:
        event = {
            "event_id": uuid7(),
            "timestamp": datetime.now(timezone.utc),
            "team_id": self.auth.team_id,
            "principal_id": self.auth.key_id,
            "context": "a2a",
            "action": action,
            "request_id": resource_id,
            "resource_type": "a2a_task",
            "resource_id": resource_id,
            "status": outcome,
            "schema_id": "talos.audit.a2a.v1",
            "schema_version": 1,
            "details": details or {}
        }
        if error_code:
            event["details"]["error_code"] = error_code
        self.audit_store.append_event(event)

    def _record_usage(self, target: str, status: str, latency: int, result: Optional[Dict[str, Any]] = None) -> None:
        usage = {
            "id": uuid7(),
            "key_id": self.auth.key_id,
            "team_id": self.auth.team_id,
            "org_id": self.auth.org_id,
            "surface": "a2a", # Surface is a2a
            "target": target, # But target is the model group
            "latency_ms": latency,
            "status": status,
            "input_tokens": 0,
            "output_tokens": 0
        }
        if result and "usage" in result:
            usage["input_tokens"] = result["usage"].get("prompt_tokens", 0)
            usage["output_tokens"] = result["usage"].get("completion_tokens", 0)
            
        self.usage_store.record_usage(usage)

    def _map_v1_state(self, status: str) -> str:
        return {
            "queued": "TASK_STATE_SUBMITTED",
            "running": "TASK_STATE_WORKING",
            "completed": "TASK_STATE_COMPLETED",
            "failed": "TASK_STATE_FAILED",
            "canceled": "TASK_STATE_CANCELED",
            "input_required": "TASK_STATE_INPUT_REQUIRED",
            "rejected": "TASK_STATE_REJECTED",
            "auth_required": "TASK_STATE_AUTH_REQUIRED",
        }.get(status, status)

    def _upstream_error_data(self, talos_code: str, error: Any) -> Dict[str, Any]:
        data: Dict[str, Any] = {
            "talos_code": talos_code,
            "details": str(error),
        }
        request_id = getattr(error, "request_id", None)
        if request_id:
            data["upstream_request_id"] = request_id
        status_code = getattr(error, "status_code", None)
        if isinstance(status_code, int):
            data["upstream_status_code"] = status_code
        retry_after_seconds = getattr(error, "retry_after_seconds", None)
        if isinstance(retry_after_seconds, (int, float)):
            retry_after_seconds = max(0.0, float(retry_after_seconds))
            data["retry_after_seconds"] = retry_after_seconds
            data["retry_after_ms"] = int(round(retry_after_seconds * 1000))
        return data
