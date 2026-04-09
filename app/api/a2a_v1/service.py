"""A2A v1 JSON-RPC adapter over the existing Talos task pipeline."""

from __future__ import annotations

import asyncio
import base64
import json
from datetime import datetime, timezone
from typing import Any, AsyncGenerator, Dict, Optional, Sequence, cast

from fastapi.concurrency import run_in_threadpool
from fastapi.responses import StreamingResponse
from pydantic import ValidationError
from starlette.requests import Request

from app.api.a2a.jsonrpc import JsonRpcException
from app.api.a2a_v1.agent_card import build_agent_card
from app.api.a2a_v1.models import (
    Artifact,
    CancelTaskParams,
    DeleteTaskPushNotificationConfigParams,
    GetTaskParams,
    GetTaskPushNotificationConfigParams,
    ListTasksParams,
    ListTasksResponse,
    ListTaskPushNotificationConfigsParams,
    ListTaskPushNotificationConfigsResponse,
    Message,
    SendMessageParams,
    StreamResponse,
    SubscribeToTaskParams,
    Task,
    TaskPushNotificationConfig,
    TaskArtifactUpdateEvent,
    TaskStatus,
    TaskStatusUpdateEvent,
    TextPart,
)
from app.adapters.redis.client import get_redis_client
from app.domain.a2a.dispatcher import A2ADispatcher
from app.domain.a2a.streaming import stream_task_events
from app.domain.interfaces import AuditStore, RateLimitStore, TaskStore, UsageStore
from app.domain.routing import RoutingService
from app.middleware.auth_public import AuthContext
from app.adapters.mcp.client import McpClient
from app.settings import settings
from app.utils.id import uuid7

ALLOWED_V1_METHODS = {
    "GetExtendedAgentCard",
    "SendMessage",
    "SendStreamingMessage",
    "GetTask",
    "CancelTask",
    "ListTasks",
    "SubscribeToTask",
    "CreateTaskPushNotificationConfig",
    "GetTaskPushNotificationConfig",
    "ListTaskPushNotificationConfigs",
    "DeleteTaskPushNotificationConfig",
    "message/send",
    "message/stream",
    "tasks/get",
    "tasks/cancel",
    "tasks/list",
    "tasks/resubscribe",
    "tasks/pushNotificationConfig/set",
    "tasks/pushNotificationConfig/get",
    "tasks/pushNotificationConfig/list",
    "tasks/pushNotificationConfig/delete",
    "agent/getAuthenticatedExtendedCard",
}
METHOD_ALIASES = {
    "agent/getAuthenticatedExtendedCard": "GetExtendedAgentCard",
    "message/send": "SendMessage",
    "message/stream": "SendStreamingMessage",
    "tasks/get": "GetTask",
    "tasks/cancel": "CancelTask",
    "tasks/list": "ListTasks",
    "tasks/resubscribe": "SubscribeToTask",
    "tasks/pushNotificationConfig/set": "CreateTaskPushNotificationConfig",
    "tasks/pushNotificationConfig/get": "GetTaskPushNotificationConfig",
    "tasks/pushNotificationConfig/list": "ListTaskPushNotificationConfigs",
    "tasks/pushNotificationConfig/delete": "DeleteTaskPushNotificationConfig",
}
STRICT_V1_METHODS = ALLOWED_V1_METHODS - set(METHOD_ALIASES)
FINAL_STATES = {"completed", "failed", "canceled", "rejected"}
STATUS_MAP = {
    "queued": "TASK_STATE_SUBMITTED",
    "running": "TASK_STATE_WORKING",
    "completed": "TASK_STATE_COMPLETED",
    "failed": "TASK_STATE_FAILED",
    "canceled": "TASK_STATE_CANCELED",
    "input_required": "TASK_STATE_INPUT_REQUIRED",
    "rejected": "TASK_STATE_REJECTED",
    "auth_required": "TASK_STATE_AUTH_REQUIRED",
}
ROLE_MAP = {
    "ROLE_USER": "user",
    "USER": "user",
    "user": "user",
    "ROLE_AGENT": "agent",
    "AGENT": "agent",
    "agent": "agent",
    "assistant": "agent",
    "ROLE_ASSISTANT": "agent",
    "system": "system",
    "SYSTEM": "system",
}
DISCOVERY_SCOPE_SETS = (
    ("a2a.discovery.read",),
    ("a2a.invoke",),
)
SEND_SCOPE_SETS = (
    ("a2a.send",),
    ("a2a.invoke",),
)
SEND_STREAM_SCOPE_SETS = (
    ("a2a.send", "a2a.subscribe"),
    ("a2a.invoke", "a2a.stream"),
)
GET_SCOPE_SETS = (
    ("a2a.get",),
    ("a2a.invoke",),
)
CANCEL_SCOPE_SETS = (
    ("a2a.cancel",),
    ("a2a.invoke",),
)
LIST_SCOPE_SETS = (
    ("a2a.list",),
    ("a2a.invoke",),
)
SUBSCRIBE_SCOPE_SETS = (
    ("a2a.subscribe",),
    ("a2a.stream",),
)
PUSH_CONFIG_READ_SCOPE_SETS = (
    ("a2a.push_config.read",),
    ("a2a.invoke",),
)
PUSH_CONFIG_WRITE_SCOPE_SETS = (
    ("a2a.push_config.write",),
    ("a2a.invoke",),
)
LEGACY_V1_SCOPE_FALLBACKS = {"a2a.invoke", "a2a.stream"}


class A2AV1Service:
    """Translate A2A v1 JSON-RPC onto the compat dispatcher and task store."""

    def __init__(
        self,
        auth: AuthContext,
        routing_service: RoutingService,
        audit_store: AuditStore,
        rl_store: RateLimitStore,
        usage_store: UsageStore,
        task_store: TaskStore,
        mcp_client: McpClient,
        capability_validator: Optional[Any] = None,
        request: Optional[Request] = None,
    ) -> None:
        self.auth = auth
        self.task_store = task_store
        self.request = request
        self.compat_dispatcher = A2ADispatcher(
            auth=auth,
            routing_service=routing_service,
            audit_store=audit_store,
            rl_store=rl_store,
            usage_store=usage_store,
            task_store=task_store,
            mcp_client=mcp_client,
            capability_validator=capability_validator,
        )

    async def handle_rpc(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        request_id = payload.get("id") if isinstance(payload, dict) else None

        try:
            self._validate_envelope(payload)

            raw_method = cast(str, payload["method"])
            method = METHOD_ALIASES.get(raw_method, raw_method)
            params = payload.get("params", {})
            if params is None:
                params = {}
            if not isinstance(params, dict):
                raise JsonRpcException(-32602, "Invalid params", data={"details": "params must be an object"})

            if method == "GetExtendedAgentCard":
                result = self._handle_get_extended_agent_card()
            elif method == "SendMessage":
                result = await self._handle_send_message(cast(Dict[str, Any], params), request_id)
            elif method == "SendStreamingMessage":
                return await self._handle_send_streaming_message(cast(Dict[str, Any], params), request_id)
            elif method == "GetTask":
                result = await self._handle_get_task(cast(Dict[str, Any], params))
            elif method == "CancelTask":
                result = await self._handle_cancel_task(cast(Dict[str, Any], params))
            elif method == "ListTasks":
                result = await self._handle_list_tasks(cast(Dict[str, Any], params))
            elif method == "SubscribeToTask":
                return await self._handle_subscribe_to_task(cast(Dict[str, Any], params), request_id)
            elif method == "CreateTaskPushNotificationConfig":
                result = await self._handle_create_task_push_notification_config(cast(Dict[str, Any], params))
            elif method == "GetTaskPushNotificationConfig":
                result = await self._handle_get_task_push_notification_config(cast(Dict[str, Any], params))
            elif method == "ListTaskPushNotificationConfigs":
                result = await self._handle_list_task_push_notification_configs(cast(Dict[str, Any], params))
            elif method == "DeleteTaskPushNotificationConfig":
                result = await self._handle_delete_task_push_notification_config(cast(Dict[str, Any], params))
            else:
                raise JsonRpcException(-32601, "Method not found")

            return {"jsonrpc": "2.0", "id": request_id, "result": result}
        except JsonRpcException as exc:
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {
                    "code": exc.code,
                    "message": exc.message,
                    "data": exc.data,
                },
            }
        except Exception as exc:
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {
                    "code": -32603,
                    "message": "Internal error",
                    "data": {"details": str(exc)},
                },
            }

    def _validate_envelope(self, payload: Dict[str, Any]) -> None:
        if not isinstance(payload, dict):
            raise JsonRpcException(-32600, "Invalid Request", data={"details": "payload must be an object"})
        if payload.get("jsonrpc") != "2.0":
            raise JsonRpcException(-32600, "Invalid Request", data={"details": "jsonrpc must equal '2.0'"})
        if "id" in payload:
            request_id = payload.get("id")
            if request_id is not None and not isinstance(request_id, (str, int, float)):
                raise JsonRpcException(-32600, "Invalid Request", data={"details": "id must be a string, number, or null"})
        method = payload.get("method")
        if not isinstance(method, str):
            raise JsonRpcException(-32600, "Invalid Request", data={"details": "method must be a string"})
        allowed_methods = STRICT_V1_METHODS if settings.a2a_protocol_mode == "v1" else ALLOWED_V1_METHODS
        if method not in allowed_methods:
            raise JsonRpcException(-32601, "Method not found")

    def _handle_get_extended_agent_card(self) -> Dict[str, Any]:
        self._require_scope_sets("GetExtendedAgentCard", DISCOVERY_SCOPE_SETS)
        if self.request is None:
            raise JsonRpcException(-32603, "Internal error", data={"details": "Request context unavailable"})
        return build_agent_card(
            self.request,
            include_compat_extension=settings.a2a_protocol_mode == "dual",
            include_extended_details=True,
        )

    async def _handle_send_message(self, params: Dict[str, Any], request_id: Any) -> Dict[str, Any]:
        self._require_scope_sets("SendMessage", SEND_SCOPE_SETS)
        task_id, history_length, return_immediately, compat_params = self._prepare_send_request(params)

        if return_immediately:
            background = asyncio.create_task(self.compat_dispatcher.handle_send(compat_params, request_id))
            background.add_done_callback(self._swallow_background_exception)
            record = await self._await_task_record(task_id, background)
        else:
            compat_result = await self.compat_dispatcher.handle_send(compat_params, request_id)
            task_id = compat_result.get("task_id") or compat_result.get("id") or task_id
            if not isinstance(task_id, str):
                raise JsonRpcException(-32603, "Internal error", data={"details": "Missing persisted task id"})

            record = await run_in_threadpool(self.task_store.get_task, task_id, self.auth.team_id)
            if record is None:
                raise JsonRpcException(
                    -32603,
                    "Internal error",
                    data={"details": "Persisted task could not be reloaded"},
                )

        task = self._task_from_record(record, include_artifacts=True, history_length=history_length)
        result = {"task": task.model_dump(exclude_none=True)}
        message = self._message_from_record(record, task_id=str(record["id"]), context_id=task.contextId)
        if message is not None:
            result["message"] = message.model_dump(exclude_none=True)
        return result

    async def _handle_send_streaming_message(self, params: Dict[str, Any], request_id: Any) -> StreamingResponse:
        self._require_scope_sets("SendStreamingMessage", SEND_STREAM_SCOPE_SETS)

        task_id, history_length, _return_immediately, compat_params = self._prepare_send_request(params)
        background = asyncio.create_task(
            self.compat_dispatcher.handle_send(compat_params, request_id)
        )
        record = await self._await_task_record(task_id, background)
        redis_client = await get_redis_client()

        async def event_stream() -> AsyncGenerator[str, None]:
            async for chunk in self._stream_task_updates(
                record,
                include_artifacts=True,
                history_length=history_length,
                redis_client=redis_client,
                request_id=request_id,
            ):
                yield chunk
            try:
                await background
            except JsonRpcException:
                pass

        return StreamingResponse(
            event_stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    async def _handle_get_task(self, params: Dict[str, Any]) -> Dict[str, Any]:
        self._require_scope_sets("GetTask", GET_SCOPE_SETS)
        model = self._parse_params(GetTaskParams, params)
        record = await run_in_threadpool(self.task_store.get_task, model.id, self.auth.team_id)
        if record is None:
            raise JsonRpcException(-32001, "Task not found", data={"talos_code": "NOT_FOUND", "taskId": model.id})

        task = self._task_from_record(
            record,
            include_artifacts=model.includeArtifacts,
            history_length=model.historyLength,
        )
        return task.model_dump(exclude_none=True)

    async def _handle_cancel_task(self, params: Dict[str, Any]) -> Dict[str, Any]:
        self._require_scope_sets("CancelTask", CANCEL_SCOPE_SETS)
        model = self._parse_params(CancelTaskParams, params)
        record = await run_in_threadpool(self.task_store.get_task, model.id, self.auth.team_id)
        if record is None:
            raise JsonRpcException(-32001, "Task not found", data={"talos_code": "NOT_FOUND", "taskId": model.id})

        state = cast(str, record.get("status", "queued"))
        if state == "canceled":
            return self._task_from_record(
                record,
                include_artifacts=model.includeArtifacts,
                history_length=model.historyLength,
            ).model_dump(exclude_none=True)

        if state in FINAL_STATES:
            raise JsonRpcException(
                -32000,
                "Task cannot be canceled",
                data={"talos_code": "NOT_CANCELABLE", "state": STATUS_MAP.get(state, state)},
            )

        await run_in_threadpool(
            self.task_store.update_task_status,
            model.id,
            "canceled",
            cast(int, record["version"]),
        )

        updated = await run_in_threadpool(self.task_store.get_task, model.id, self.auth.team_id)
        if updated is None:
            raise JsonRpcException(-32603, "Internal error", data={"details": "Canceled task could not be reloaded"})

        task = self._task_from_record(
            updated,
            include_artifacts=model.includeArtifacts,
            history_length=model.historyLength,
        )
        return task.model_dump(exclude_none=True)

    async def _handle_list_tasks(self, params: Dict[str, Any]) -> Dict[str, Any]:
        self._require_scope_sets("ListTasks", LIST_SCOPE_SETS)

        model = self._parse_params(ListTasksParams, params)
        cursor = self._decode_page_token(model.pageToken)
        status_timestamp_after = self._parse_datetime(model.statusTimestampAfter)
        internal_status = self._normalize_state_filter(model.status)

        records, next_cursor, total_size = await run_in_threadpool(
            self.task_store.list_tasks,
            self.auth.team_id,
            context_id=model.contextId,
            status=internal_status,
            page_size=model.pageSize,
            cursor_updated_at=cursor[0] if cursor else None,
            cursor_task_id=cursor[1] if cursor else None,
            status_timestamp_after=status_timestamp_after,
        )

        tasks = [
            self._task_from_record(
                record,
                include_artifacts=model.includeArtifacts,
                history_length=model.historyLength,
            ).model_dump(exclude_none=True)
            for record in records
        ]

        response = ListTasksResponse(
            tasks=tasks,
            nextPageToken=self._encode_page_token(next_cursor) if next_cursor else None,
            pageSize=model.pageSize,
            totalSize=total_size,
        )
        return response.model_dump(exclude_none=True)

    async def _handle_create_task_push_notification_config(self, params: Dict[str, Any]) -> Dict[str, Any]:
        self._require_scope_sets(
            "CreateTaskPushNotificationConfig",
            PUSH_CONFIG_WRITE_SCOPE_SETS,
        )

        config = self._normalize_push_notification_config(params)
        task_id = cast(str, config["taskId"])
        await self._ensure_task_exists(task_id)
        stored = await run_in_threadpool(
            self.task_store.create_task_push_notification_config,
            task_id,
            self.auth.team_id,
            config,
        )
        return self._mask_push_notification_config(stored)

    async def _handle_get_task_push_notification_config(self, params: Dict[str, Any]) -> Dict[str, Any]:
        self._require_scope_sets(
            "GetTaskPushNotificationConfig",
            PUSH_CONFIG_READ_SCOPE_SETS,
        )

        model = self._parse_params(GetTaskPushNotificationConfigParams, params)
        await self._ensure_task_exists(model.taskId)
        stored = await run_in_threadpool(
            self.task_store.get_task_push_notification_config,
            model.taskId,
            self.auth.team_id,
            model.id,
        )
        if stored is None:
            raise JsonRpcException(-32000, "Push notification config not found", data={"talos_code": "NOT_FOUND"})
        return self._mask_push_notification_config(stored)

    async def _handle_list_task_push_notification_configs(self, params: Dict[str, Any]) -> Dict[str, Any]:
        self._require_scope_sets(
            "ListTaskPushNotificationConfigs",
            PUSH_CONFIG_READ_SCOPE_SETS,
        )

        model = self._parse_params(ListTaskPushNotificationConfigsParams, params)
        await self._ensure_task_exists(model.taskId)
        configs = await run_in_threadpool(
            self.task_store.list_task_push_notification_configs,
            model.taskId,
            self.auth.team_id,
        )
        response = ListTaskPushNotificationConfigsResponse(
            configs=[self._mask_push_notification_config(config) for config in configs]
        )
        return response.model_dump(exclude_none=True)

    async def _handle_delete_task_push_notification_config(self, params: Dict[str, Any]) -> Dict[str, Any]:
        self._require_scope_sets(
            "DeleteTaskPushNotificationConfig",
            PUSH_CONFIG_WRITE_SCOPE_SETS,
        )

        model = self._parse_params(DeleteTaskPushNotificationConfigParams, params)
        await self._ensure_task_exists(model.taskId)
        deleted = await run_in_threadpool(
            self.task_store.delete_task_push_notification_config,
            model.taskId,
            self.auth.team_id,
            model.id,
        )
        if not deleted:
            raise JsonRpcException(-32000, "Push notification config not found", data={"talos_code": "NOT_FOUND"})
        return {"deleted": True, "id": model.id}

    async def _handle_subscribe_to_task(self, params: Dict[str, Any], request_id: Any) -> StreamingResponse:
        self._require_scope_sets("SubscribeToTask", SUBSCRIBE_SCOPE_SETS)

        model = self._parse_params(SubscribeToTaskParams, params)
        record = await run_in_threadpool(self.task_store.get_task, model.id, self.auth.team_id)
        if record is None:
            raise JsonRpcException(-32000, "Task not found", data={"talos_code": "NOT_FOUND", "taskId": model.id})

        if cast(str, record.get("status", "queued")) in FINAL_STATES:
            raise JsonRpcException(
                -32000,
                "Task subscription unavailable for terminal task",
                data={"talos_code": "UNSUPPORTED_OPERATION", "taskId": model.id},
            )

        redis_client = await get_redis_client()
        stream = self._stream_task_updates(
            record,
            include_artifacts=model.includeArtifacts,
            history_length=model.historyLength,
            redis_client=redis_client,
            request_id=request_id,
        )

        return StreamingResponse(
            stream,
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    def _parse_params(self, model_cls: type[Any], params: Dict[str, Any]) -> Any:
        try:
            return model_cls.model_validate(params)
        except ValidationError as exc:
            raise JsonRpcException(-32602, "Invalid params", data={"details": str(exc)}) from exc

    def _require_scope_sets(
        self,
        operation: str,
        scope_sets: Sequence[tuple[str, ...]],
    ) -> None:
        effective_scope_sets = self._effective_scope_sets(scope_sets)
        if self._satisfies_scope_sets(effective_scope_sets):
            return

        required_scopes = [list(scope_set) for scope_set in effective_scope_sets]
        raise JsonRpcException(
            -32000,
            "Permission denied",
            data={
                "talos_code": "RBAC_DENIED",
                "operation": operation,
                "required_scope_sets": required_scopes,
            },
        )

    def _effective_scope_sets(
        self,
        scope_sets: Sequence[tuple[str, ...]],
    ) -> Sequence[tuple[str, ...]]:
        if settings.a2a_protocol_mode != "v1":
            return scope_sets
        return tuple(
            scope_set
            for scope_set in scope_sets
            if not any(scope in LEGACY_V1_SCOPE_FALLBACKS for scope in scope_set)
        )

    def _satisfies_scope_sets(
        self,
        scope_sets: Sequence[tuple[str, ...]],
    ) -> bool:
        return any(
            all(self._has_scope(scope) for scope in scope_set)
            for scope_set in scope_sets
        )

    def _has_scope(self, scope: str) -> bool:
        scopes = getattr(self.auth, "scopes", None)
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

    def _prepare_send_request(
        self,
        params: Dict[str, Any],
    ) -> tuple[str, Optional[int], bool, Dict[str, Any]]:
        model = self._parse_params(SendMessageParams, params)
        message = self._normalize_message(model.message)
        context_id = message.get("contextId") or uuid7()
        history_length = (
            model.configuration.historyLength
            if model.configuration is not None
            else None
        )
        return_immediately = (
            model.configuration.returnImmediately
            if model.configuration is not None
            else False
        )
        task_id = str(message.get("taskId") or uuid7())
        push_notification_configs = []
        if model.configuration is not None:
            raw_push_config = (
                model.configuration.taskPushNotificationConfig
                or model.configuration.pushNotificationConfig
            )
            if raw_push_config is not None:
                push_notification_configs.append(
                    self._normalize_push_notification_config(
                        raw_push_config.model_dump(exclude_none=True),
                        default_task_id=task_id,
                    )
                )

        compat_params = {
            "profile": {
                "profile_id": "a2a-v1",
                "profile_version": "1.0.0",
                "spec_source": "a2a-protocol",
            },
            "task_id": task_id,
            "input": [self._message_to_compat_input(message)],
            "context_id": context_id,
            "message_id": message["messageId"],
            "origin_surface": "a2a_v1",
            "input_redacted": {"messages": [dict(message, taskId=task_id, contextId=context_id)]},
        }
        if push_notification_configs:
            compat_params["push_notification_configs"] = push_notification_configs
        return task_id, history_length, return_immediately, compat_params

    def _normalize_message(self, message: Dict[str, Any]) -> Dict[str, Any]:
        if not isinstance(message, dict):
            raise JsonRpcException(-32602, "Invalid params", data={"details": "message must be an object"})

        raw_parts = message.get("parts")
        if not isinstance(raw_parts, list) or not raw_parts:
            raise JsonRpcException(-32602, "Invalid params", data={"details": "message.parts must be a non-empty array"})

        parts = []
        for raw_part in raw_parts:
            text = self._extract_text_from_part(raw_part)
            if text is None:
                continue
            parts.append({"text": text})

        if not parts:
            raise JsonRpcException(-32602, "Invalid params", data={"details": "only text parts are supported in PR2"})

        normalized: Dict[str, Any] = {
            "messageId": str(message.get("messageId") or uuid7()),
            "role": self._normalize_role(message.get("role")),
            "parts": parts,
        }
        if message.get("taskId"):
            normalized["taskId"] = str(message["taskId"])
        if message.get("contextId"):
            normalized["contextId"] = str(message["contextId"])
        if isinstance(message.get("metadata"), dict):
            normalized["metadata"] = message["metadata"]
        return normalized

    def _normalize_role(self, raw_role: Any) -> str:
        if not isinstance(raw_role, str):
            raise JsonRpcException(-32602, "Invalid params", data={"details": "message.role must be a string"})

        normalized = ROLE_MAP.get(raw_role, ROLE_MAP.get(raw_role.upper()))
        if normalized is None:
            raise JsonRpcException(-32602, "Invalid params", data={"details": f"unsupported message role: {raw_role}"})
        return normalized

    def _message_to_compat_input(self, message: Dict[str, Any]) -> Dict[str, Any]:
        role = "assistant" if message["role"] == "agent" else message["role"]
        return {
            "role": role,
            "content": [{"text": part["text"]} for part in message["parts"]],
        }

    def _task_from_record(
        self,
        record: Dict[str, Any],
        *,
        include_artifacts: bool,
        history_length: Optional[int],
    ) -> Task:
        task_id = str(record["id"])
        context_id = self._context_id_from_record(record)
        artifacts = self._artifacts_from_record(record) if include_artifacts else None
        history = self._history_from_record(record, task_id, context_id, history_length)
        metadata = {
            "originSurface": record.get("origin_surface") or record.get("request_meta", {}).get("origin_surface", "a2a"),
            "requestId": record.get("request_id"),
            "version": record.get("version"),
        }
        created_at = record.get("created_at")
        updated_at = record.get("updated_at") or created_at
        if created_at is not None:
            metadata["createdAt"] = self._isoformat(created_at)
        if updated_at is not None:
            metadata["lastModified"] = self._isoformat(updated_at)

        return Task(
            id=task_id,
            version="1.0",
            contextId=context_id,
            status=self._status_from_record(record, task_id, context_id),
            history=history or None,
            artifacts=artifacts or None,
            metadata=metadata,
        )

    def _status_from_record(self, record: Dict[str, Any], task_id: str, context_id: str) -> TaskStatus:
        raw_state = cast(str, record.get("status", "queued"))
        state = STATUS_MAP.get(raw_state, raw_state)
        message_text: Optional[str] = None

        if raw_state == "queued":
            message_text = "Task submitted"
        elif raw_state == "running":
            message_text = "Task in progress"
        elif raw_state == "failed":
            message_text = self._extract_error_text(record.get("error")) or "Task failed"
        elif raw_state == "canceled":
            message_text = "Task canceled"

        status_message = None
        if message_text:
            status_message = Message(
                messageId=f"{task_id}:status:{state}",
                role="agent",
                taskId=task_id,
                contextId=context_id,
                parts=[TextPart(text=message_text)],
            )

        return TaskStatus(
            state=state,
            timestamp=self._isoformat(record.get("updated_at") or record.get("created_at")),
            message=status_message,
        )

    def _message_from_record(self, record: Dict[str, Any], *, task_id: str, context_id: str) -> Optional[Message]:
        result = record.get("result")
        if not isinstance(result, dict):
            return None

        artifact_parts = self._artifacts_from_record(record)
        if artifact_parts:
            return Message(
                messageId=f"{task_id}:message",
                role="agent",
                taskId=task_id,
                contextId=context_id,
                parts=artifact_parts[0].parts,
                metadata={"artifactId": artifact_parts[0].artifactId},
            )

        output = result.get("output")
        if isinstance(output, list):
            parts = self._parts_from_output(output)
            if parts:
                return Message(
                    messageId=f"{task_id}:message",
                    role="agent",
                    taskId=task_id,
                    contextId=context_id,
                    parts=parts,
                )
        return None

    def _artifacts_from_record(self, record: Dict[str, Any]) -> list[Artifact]:
        result = record.get("result")
        if not isinstance(result, dict):
            return []

        compat_artifacts = result.get("artifacts")
        if isinstance(compat_artifacts, list):
            artifacts = []
            for index, raw_artifact in enumerate(compat_artifacts):
                if not isinstance(raw_artifact, dict):
                    continue
                parts = self._parts_from_compat_artifact(raw_artifact)
                if not parts:
                    continue
                artifacts.append(
                    Artifact(
                        artifactId=str(raw_artifact.get("artifact_id") or raw_artifact.get("artifactId") or f"{record['id']}:artifact:{index}"),
                        name=cast(Optional[str], raw_artifact.get("name")),
                        description=cast(Optional[str], raw_artifact.get("description")),
                        parts=parts,
                        metadata={"mediaType": raw_artifact.get("type")} if raw_artifact.get("type") else None,
                    )
                )
            return artifacts

        output = result.get("output")
        if isinstance(output, list):
            parts = self._parts_from_output(output)
            if parts:
                return [
                    Artifact(
                        artifactId=f"{record['id']}:output",
                        name="output",
                        parts=parts,
                    )
                ]

        return []

    def _history_from_record(
        self,
        record: Dict[str, Any],
        task_id: str,
        context_id: str,
        history_length: Optional[int],
    ) -> list[Message]:
        input_redacted = record.get("input_redacted")
        if not isinstance(input_redacted, dict):
            return []

        raw_messages = input_redacted.get("messages")
        if not isinstance(raw_messages, list):
            return []

        if history_length == 0:
            return []

        selected_messages = raw_messages if history_length is None else raw_messages[-history_length:]
        messages: list[Message] = []
        for raw_message in selected_messages:
            if not isinstance(raw_message, dict):
                continue
            parts = []
            for raw_part in cast(Sequence[Any], raw_message.get("parts", [])):
                text = self._extract_text_from_part(raw_part)
                if text is None:
                    continue
                parts.append(TextPart(text=text))
            if not parts:
                continue
            messages.append(
                Message(
                    messageId=str(raw_message.get("messageId") or uuid7()),
                    role=self._normalize_role(raw_message.get("role", "user")),
                    parts=parts,
                    taskId=str(raw_message.get("taskId") or task_id),
                    contextId=str(raw_message.get("contextId") or context_id),
                    metadata=cast(Optional[Dict[str, Any]], raw_message.get("metadata")),
                )
            )
        return messages

    def _parts_from_compat_artifact(self, artifact: Dict[str, Any]) -> list[TextPart]:
        content = artifact.get("content")
        if isinstance(content, dict):
            text = self._extract_text_from_part(content)
            if text is not None:
                return [TextPart(text=text)]

        if isinstance(content, list):
            parts = []
            for raw_part in content:
                text = self._extract_text_from_part(raw_part)
                if text is None:
                    continue
                parts.append(TextPart(text=text))
            return parts

        return []

    def _parts_from_output(self, output: Sequence[Any]) -> list[TextPart]:
        parts = []
        for message in output:
            if not isinstance(message, dict):
                continue
            for raw_part in cast(Sequence[Any], message.get("content", [])):
                text = self._extract_text_from_part(raw_part)
                if text is None:
                    continue
                parts.append(TextPart(text=text))
        return parts

    def _context_id_from_record(self, record: Dict[str, Any]) -> str:
        request_meta = record.get("request_meta")
        if isinstance(request_meta, dict) and request_meta.get("context_id"):
            return str(request_meta["context_id"])
        return str(record["id"])

    def _extract_error_text(self, error: Any) -> Optional[str]:
        if isinstance(error, str):
            return error
        if isinstance(error, dict):
            for key in ("details", "message", "code", "talos_code"):
                value = error.get(key)
                if isinstance(value, str) and value:
                    return value
            from app.domain.a2a.canonical import canonical_json_bytes
            return canonical_json_bytes(error).decode('utf-8')
        return None

    def _extract_text_from_part(self, raw_part: Any) -> Optional[str]:
        if not isinstance(raw_part, dict):
            return None

        if raw_part.get("kind") == "text" and isinstance(raw_part.get("text"), str):
            return raw_part["text"]
        if raw_part.get("type") == "text" and isinstance(raw_part.get("text"), str):
            return raw_part["text"]
        if isinstance(raw_part.get("text"), str):
            return raw_part["text"]
        return None

    async def _stream_task_updates(
        self,
        record: Dict[str, Any],
        *,
        include_artifacts: bool,
        history_length: Optional[int],
        redis_client: Any,
        request_id: Any,
    ) -> AsyncGenerator[str, None]:
        task_id = str(record["id"])
        task = self._task_from_record(
            record,
            include_artifacts=include_artifacts,
            history_length=history_length,
        )
        initial = StreamResponse(task=task)
        yield self._format_sse(
            event_id=f"{task_id}:initial",
            payload=self._jsonrpc_result(request_id, initial.model_dump(exclude_none=True)),
        )

        after_cursor = f"{task_id}:{record['version']}"
        async for raw_chunk in stream_task_events(
            task_id=task_id,
            team_id=self.auth.team_id,
            task_store=self.task_store,
            redis_client=redis_client,
            request_id=request_id,
            after_cursor=after_cursor,
        ):
            if raw_chunk.startswith(":"):
                yield raw_chunk
                continue

            payload = self._parse_sse_payload(raw_chunk)
            if payload is None:
                yield raw_chunk
                continue

            updated = await run_in_threadpool(self.task_store.get_task, task_id, self.auth.team_id)
            if updated is None:
                break

            event_id = payload.get("event_id", f"{task_id}:{updated.get('version', 0)}")
            if include_artifacts and payload.get("status") == "completed":
                for index, artifact in enumerate(self._artifacts_from_record(updated)):
                    artifact_response = StreamResponse(
                        artifactUpdate=TaskArtifactUpdateEvent(
                            taskId=task_id,
                            contextId=self._context_id_from_record(updated),
                            artifact=artifact,
                            append=False,
                            lastChunk=index == len(self._artifacts_from_record(updated)) - 1,
                            metadata={
                                "eventId": event_id,
                                "version": updated.get("version"),
                            },
                        )
                    )
                    yield self._format_sse(
                        event_id=f"{event_id}:artifact:{index}",
                        payload=self._jsonrpc_result(
                            request_id,
                            artifact_response.model_dump(exclude_none=True),
                        ),
                    )

            status_response = StreamResponse(
                statusUpdate=TaskStatusUpdateEvent(
                    taskId=task_id,
                    contextId=self._context_id_from_record(updated),
                    status=self._status_from_record(
                        updated,
                        task_id,
                        self._context_id_from_record(updated),
                    ),
                    metadata={
                        "eventId": event_id,
                        "final": cast(str, updated.get("status", "queued")) in FINAL_STATES,
                        "version": updated.get("version"),
                    },
                )
            )
            yield self._format_sse(
                event_id=str(event_id),
                payload=self._jsonrpc_result(
                    request_id,
                    status_response.model_dump(exclude_none=True),
                ),
            )

    def _format_sse(self, *, event_id: str, payload: Dict[str, Any]) -> str:
        return f"id: {event_id}\ndata: {json.dumps(payload)}\n\n"

    def _jsonrpc_result(self, request_id: Any, result: Dict[str, Any]) -> Dict[str, Any]:
        return {"jsonrpc": "2.0", "id": request_id, "result": result}

    def _swallow_background_exception(self, task: "asyncio.Task[Any]") -> None:
        try:
            task.exception()
        except Exception:
            pass

    def _parse_sse_payload(self, raw_chunk: str) -> Optional[Dict[str, Any]]:
        data_line = None
        for line in raw_chunk.splitlines():
            if line.startswith("data: "):
                data_line = line[6:]
                break
        if data_line is None:
            return None
        try:
            parsed = json.loads(data_line)
        except json.JSONDecodeError:
            return None
        return parsed if isinstance(parsed, dict) else None

    async def _await_task_record(
        self,
        task_id: str,
        background: "asyncio.Task[Dict[str, Any]]",
    ) -> Dict[str, Any]:
        for _ in range(100):
            record = await run_in_threadpool(self.task_store.get_task, task_id, self.auth.team_id)
            if record is not None:
                return record
            if background.done():
                exc = background.exception()
                if exc:
                    if isinstance(exc, JsonRpcException):
                        raise exc
                    raise JsonRpcException(-32603, "Internal error", data={"details": str(exc)}) from exc
            await asyncio.sleep(0.01)

        if background.done():
            exc = background.exception()
            if exc:
                if isinstance(exc, JsonRpcException):
                    raise exc
                raise JsonRpcException(-32603, "Internal error", data={"details": str(exc)}) from exc

        raise JsonRpcException(
            -32603,
            "Internal error",
            data={"details": f"timed out waiting for task {task_id} to be created"},
        )

    def _normalize_push_notification_config(
        self,
        params: Dict[str, Any],
        *,
        default_task_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        payload = dict(params)
        if isinstance(payload.get("taskPushNotificationConfig"), dict):
            payload = dict(payload["taskPushNotificationConfig"])
        if isinstance(payload.get("pushNotificationConfig"), dict):
            payload = dict(payload["pushNotificationConfig"])

        task_id = payload.get("taskId") or default_task_id
        if not isinstance(task_id, str) or not task_id:
            raise JsonRpcException(-32602, "Invalid params", data={"details": "taskId is required"})
        payload["taskId"] = task_id
        payload["id"] = str(payload.get("id") or uuid7())

        try:
            model = TaskPushNotificationConfig.model_validate(payload)
        except ValidationError as exc:
            raise JsonRpcException(-32602, "Invalid params", data={"details": str(exc)}) from exc
        return model.model_dump(exclude_none=True)

    def _mask_push_notification_config(self, config: Dict[str, Any]) -> Dict[str, Any]:
        masked = dict(config)
        auth = masked.get("authentication")
        if isinstance(auth, dict) and auth.get("credentials"):
            auth = dict(auth)
            auth["credentials"] = "[REDACTED]"
            masked["authentication"] = auth
        return masked

    async def _ensure_task_exists(self, task_id: str) -> None:
        if not task_id:
            raise JsonRpcException(-32602, "Invalid params", data={"details": "taskId is required"})
        task = await run_in_threadpool(self.task_store.get_task, task_id, self.auth.team_id)
        if task is None:
            raise JsonRpcException(-32000, "Task not found", data={"talos_code": "NOT_FOUND", "taskId": task_id})

    def _encode_page_token(self, cursor: Optional[tuple[datetime, str]]) -> Optional[str]:
        if cursor is None:
            return None
        from app.domain.a2a.canonical import canonical_json_bytes
        payload = canonical_json_bytes(
            {"updated_at": self._isoformat(cursor[0]), "id": cursor[1]}
        )
        return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")

    def _decode_page_token(self, token: Optional[str]) -> Optional[tuple[datetime, str]]:
        if not token:
            return None

        padded = token + "=" * ((4 - len(token) % 4) % 4)
        try:
            decoded = base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8")
            payload = json.loads(decoded)
        except Exception as exc:
            raise JsonRpcException(-32602, "Invalid params", data={"details": f"invalid pageToken: {exc}"}) from exc

        if not isinstance(payload, dict) or not isinstance(payload.get("id"), str):
            raise JsonRpcException(-32602, "Invalid params", data={"details": "pageToken missing required cursor fields"})

        updated_at = self._parse_datetime(payload.get("updated_at"))
        if updated_at is None:
            raise JsonRpcException(-32602, "Invalid params", data={"details": "pageToken missing valid updated_at"})
        return updated_at, payload["id"]

    def _normalize_state_filter(self, state: Optional[str]) -> Optional[str]:
        if state is None:
            return None

        state_map = {
            "submitted": "queued",
            "working": "running",
            "completed": "completed",
            "failed": "failed",
            "canceled": "canceled",
            "input_required": "input_required",
            "rejected": "rejected",
            "auth_required": "auth_required",
            "TASK_STATE_SUBMITTED": "queued",
            "TASK_STATE_WORKING": "running",
            "TASK_STATE_COMPLETED": "completed",
            "TASK_STATE_FAILED": "failed",
            "TASK_STATE_CANCELED": "canceled",
            "TASK_STATE_INPUT_REQUIRED": "input_required",
            "TASK_STATE_REJECTED": "rejected",
            "TASK_STATE_AUTH_REQUIRED": "auth_required",
        }
        normalized = state_map.get(state)
        if normalized is None:
            raise JsonRpcException(-32602, "Invalid params", data={"details": f"unsupported state filter: {state}"})
        return normalized

    def _parse_datetime(self, value: Any) -> Optional[datetime]:
        if value is None:
            return None
        if isinstance(value, datetime):
            if value.tzinfo is None:
                return value.replace(tzinfo=timezone.utc)
            return value.astimezone(timezone.utc)
        if isinstance(value, str):
            try:
                parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError as exc:
                raise JsonRpcException(-32602, "Invalid params", data={"details": f"invalid timestamp: {value}"}) from exc
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(timezone.utc)
        raise JsonRpcException(-32602, "Invalid params", data={"details": f"invalid timestamp type: {type(value).__name__}"})

    def _isoformat(self, value: Any) -> str:
        if isinstance(value, datetime):
            if value.tzinfo is None:
                value = value.replace(tzinfo=timezone.utc)
            return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
        if isinstance(value, str):
            return value
        return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
