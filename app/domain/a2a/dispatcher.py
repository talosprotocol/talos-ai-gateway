import uuid
import time
from datetime import datetime, timezone
from typing import Dict, Any

from app.api.a2a.jsonrpc import validator, JsonRpcException
from app.domain.a2a.mapper_llm import map_input_to_llm_messages, map_llm_response_to_task
from app.domain.a2a.mapper_mcp import McpMapper
from app.domain.routing import RoutingService
import json
from fastapi.concurrency import run_in_threadpool
from app.domain.interfaces import AuditStore, RateLimitStore, UsageStore, TaskStore
from app.middleware.auth_public import AuthContext
from app.adapters.upstreams_ai.client import (
    invoke_openai_compatible, get_api_key
)
from app.adapters.mcp.client import McpClient
from app.adapters.redis.client import rate_limit_key, get_redis_client

ALLOWED_METHODS = {"tasks.send", "tasks.get", "tasks.cancel"}

class A2ADispatcher:
    def __init__(
        self,
        auth: AuthContext,
        routing_service: RoutingService,
        audit_store: AuditStore,
        rl_store: RateLimitStore,
        usage_store: UsageStore,
        task_store: TaskStore,
        mcp_client: McpClient
    ):
        self.auth = auth
        self.routing = routing_service
        self.audit_store = audit_store
        self.rl_store = rl_store
        self.usage_store = usage_store
        self.task_store = task_store
        self.mcp_mapper = McpMapper(mcp_client, audit_store)
        self._redis_promise = get_redis_client()
        self.redis = None

    async def _get_redis(self):
        if self.redis is None:
            self.redis = await self._redis_promise
        return self.redis
        
    async def dispatch(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """
        Main entry point for JSON-RPC dispatch.
        """
        request_id = payload.get("id")
        method = payload.get("method")
        
        try:
            # 1. Validate Envelope
            validator.validate_request_envelope(payload)
            
            # 2. Check Allowlist
            if method not in ALLOWED_METHODS:
                raise JsonRpcException(-32601, "Method not found")
            
            # 3. Validate Method Request (Params)
            validator.validate_method_request(method, payload)
            
            params = payload.get("params", {})
            
            # 4. Dispatch
            result = None
            if method == "tasks.send":
                result = await self.handle_send(params, request_id)
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

    async def _publish_event(self, task_id: str, status: str, version: int, request_id: str):
        redis = await self._get_redis()
        if not redis: return
        
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
            await self.redis.publish(f"a2a:tasks:{task_id}", json.dumps(event))
            
            # Cache Last Event (1h TTL)
            await self.redis.setex(f"a2a:last_event:{task_id}", 3600, json.dumps(event))
        except Exception:
            # Non-blocking failure
            pass

    async def handle_send(self, params: Dict[str, Any], request_id: Any) -> Dict[str, Any]:
        # Scope Check for A2A
        if "a2a.invoke" not in self.auth.scopes:
             raise JsonRpcException(-32000, "Permission denied", data={"talos_code": "RBAC_DENIED", "details": "Missing 'a2a.invoke' scope"})
        
        task_id = str(uuid.uuid4())
        
        # 1. Persist QUEUED
        # Safe Metadata Only
        tool_call = params.get("tool_call")
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
        
        request_meta = self._sanitize_request_meta(request_meta)
        
        task_data = {
            "id": task_id,
            "team_id": self.auth.team_id,
            "key_id": self.auth.key_id,
            "org_id": self.auth.org_id,
            "request_id": str(request_id),
            "method": "tasks.send",
            "status": "queued",
            "version": 1,
            "request_meta": request_meta,
            # input_redacted: omitted by default for privacy
            "created_at": datetime.now(timezone.utc),
            "updated_at": datetime.now(timezone.utc)
        }
        
        
        await run_in_threadpool(self.task_store.create_task, task_data)
        current_version = 1
        
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
                result_task = await self.mcp_mapper.execute_tool(tool_call, self.auth, str(request_id))

            else:
                # --- A1: LLM Path ---
                if "llm.invoke" not in self.auth.scopes:
                     raise JsonRpcException(-32000, "Permission denied", data={"talos_code": "RBAC_DENIED", "details": "Missing 'llm.invoke' scope"})
                
                if not self.auth.allowed_model_groups:
                     raise JsonRpcException(-32000, "No models allowed", data={"talos_code": "CONFIG_ERROR"})
                
                model_group_id = self.auth.allowed_model_groups[0] if self.auth.allowed_model_groups != ["*"] else "gpt-4o"
                if model_group_id == "*": model_group_id = "gpt-4o"
                
                # Audit Start
                self._audit("a2a.tasks.send", "started", resource_id=str(request_id))
                
                start_time = time.time()
                
                # Route
                routing_req_id = str(uuid.uuid4())
                selection = self.routing.select_upstream(model_group_id, routing_req_id)
                if not selection:
                    raise JsonRpcException(-32000, "No upstream available", data={"talos_code": "UPSTREAM_UNAVAILABLE"})
                
                upstream = selection["upstream"]
                model_name = selection["model_name"]
                api_key = get_api_key(upstream.get("credentials_ref", ""))
                
                llm_messages = map_input_to_llm_messages(params.get("input", []))
                
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
            
            await run_in_threadpool(
                self.task_store.update_task_status,
                task_id, "failed",
                expected_version=current_version,
                error=error_data
            )
            await self._publish_event(task_id, "failed", current_version + 1, request_id)

            if isinstance(e, JsonRpcException): raise e
            raise JsonRpcException(-32000, "Internal error processing task", data={"talos_code": "INTERNAL_ERROR", "details": str(e)})


    async def handle_get(self, params: Dict[str, Any]) -> Dict[str, Any]:
        if "a2a.invoke" not in self.auth.scopes:
             raise JsonRpcException(-32000, "Permission denied", data={"talos_code": "RBAC_DENIED"})
        
        task_id = params.get("task_id")
        if not task_id:
             raise JsonRpcException(-32602, "Invalid params", data={"details": "Missing task_id"})
             
        task = await run_in_threadpool(self.task_store.get_task, task_id, self.auth.team_id)
        
        if not task:
             raise JsonRpcException(-32000, "Task not found", data={"talos_code": "NOT_FOUND"})
             
        # Reconstruct Task Object according to task.schema.json
        # Format dates as ISO with Z
        created_at_iso = task["created_at"].isoformat() + "Z" if task["created_at"] else None
        updated_at_iso = task["updated_at"].isoformat() + "Z" if task["updated_at"] else None
        
        # Merge stored result if completed/failed, otherwise initial structure
        if task["status"] == "completed" and task.get("result"):
             return task["result"]
             
        # Partial Task for non-completed states
        meta = task.get("request_meta", {})
        return {
             "task_id": task["id"],
             "status": task["status"],
             "created_at": created_at_iso,
             "updated_at": updated_at_iso,
             "profile": {
                 "profile_id": meta.get("profile_id", "default"),
                 "profile_version": meta.get("profile_version", "0.1")
             },
             "error": task.get("error")
        }

    def _sanitize_request_meta(self, meta: Dict[str, Any]) -> Dict[str, Any]:
        ALLOWED_KEYS = {"method", "profile_id", "profile_version", "model_group_id", "has_tool_call", "tool_server_id", "tool_name", "origin_surface"}
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
        if "a2a.invoke" not in self.auth.scopes:
             raise JsonRpcException(-32000, "Permission denied", data={"talos_code": "RBAC_DENIED"})

        raise JsonRpcException(-32000, "Cancellation not supported", data={"talos_code": "NOT_CANCELABLE"})

    def _audit(self, action: str, outcome: str, resource_id: str = None, details: Dict = None, error_code: str = None):
        event = {
            "event_id": str(uuid.uuid4()),
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

    def _record_usage(self, target: str, status: str, latency: int, result: Dict = None):
        usage = {
            "id": str(uuid.uuid4()),
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
