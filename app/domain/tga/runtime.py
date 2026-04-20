"""TGA Runtime Loop bridged to the standalone talos-governance-agent package."""
import logging
import hashlib
import json
import base64
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional
from datetime import datetime, timezone

from talos_governance_agent.domain.runtime import TgaRuntime as StandaloneTgaRuntime
from talos_governance_agent.domain.models import ExecutionStateEnum, ExecutionLogEntry, ArtifactType
from talos_governance_agent.domain.runtime import ExecutionPlan as StandaloneExecutionPlan, RecoveryResult

from app.domain.mcp.tool_guard import ToolGuard
from app.adapters.mcp.client import McpClient
from app.domain.tga.state_store import get_state_store, TgaStateStore
from app.settings import settings

logger = logging.getLogger(__name__)

ZERO_DIGEST = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"

class RuntimeError(Exception):
    """Runtime execution error."""
    def __init__(self, message: str, code: str):
        super().__init__(message)
        self.code = code

@dataclass
class ExecutionPlan:
    """Legacy ExecutionPlan wrapper for compatibility."""
    trace_id: str
    plan_id: str
    action_request: Dict[str, Any]
    tool_server: str = "default-server"
    tool_name: str = "default-tool"
    tool_args: Dict[str, Any] = field(default_factory=dict)
    capability_jws: Optional[str] = None # Added for standalone parity
    supervisor_decision_fn: Optional[Callable] = None
    tool_dispatch_fn: Optional[Callable] = None

@dataclass
class ExecutionResult:
    """Result of TGA execution."""
    trace_id: str
    final_state: ExecutionStateEnum
    tool_effect: Optional[Dict[str, Any]] = None
    error: Optional[str] = None

class TgaRuntime:
    """
    TGA runtime that delegates to the standalone talos-governance-agent.
    """
    
    def __init__(
        self, 
        store: Optional[TgaStateStore] = None,
        tool_guard: Optional[ToolGuard] = None,
        audit_logger: Optional[Any] = None,
        mcp_client: Optional[McpClient] = None
    ):
        self.store = store or get_state_store()
        self.tool_guard = tool_guard
        self.audit_logger = audit_logger
        self.mcp_client = mcp_client or McpClient()
        
        # Initialize standalone runtime
        pub_key = settings.TGA_SUPERVISOR_PUBLIC_KEY or "dev-key"
        self.standalone = StandaloneTgaRuntime(self.store, pub_key)
    
    async def execute_plan(
        self, 
        plan: ExecutionPlan,
        principal: Optional[Dict[str, Any]] = None
    ) -> ExecutionResult:
        """Execute a TGA plan using the consolidated standalone runtime."""
        trace_id = plan.trace_id
        
        try:
            # 1. Authorize (Cold Path or Resume)
            if plan.capability_jws:
                # Use real JWS if provided
                entry = await self.standalone.authorize_tool_call(
                    capability_jws=plan.capability_jws,
                    tool_server=plan.tool_server,
                    tool_name=plan.tool_name,
                    args=plan.tool_args
                )
            else:
                # Fallback for internal gateway plans without JWS (LEGACY PATH)
                logger.warning(f"Executing TGA plan {trace_id} without JWS - using mock authorization")
                
                # Check for existing state
                state = await self.store.load_state(trace_id)
                if state:
                    if state.current_state in (ExecutionStateEnum.COMPLETED, ExecutionStateEnum.FAILED, ExecutionStateEnum.DENIED):
                         return ExecutionResult(trace_id=trace_id, final_state=state.current_state)
                    # Trace exists, assume it was already authorized or in-progress
                else:
                    # NEW TRACE: Manually drive state machine to EXECUTING
                    # We need valid UUIDv7 for principal_id in strict standalone mode
                    principal_id = principal.get("principal_id") if principal else "01936a8b-4c2d-7000-8000-ffffffffffff"
                    # Handle cases where principal_id is already a non-UUID name
                    if not re.match(r"^[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$", principal_id):
                        principal_id = "01936a8b-4c2d-7000-8000-ffffffffffff"

                    def compute_art_digest(data: Any) -> str:
                        canonical = json.dumps(data, sort_keys=True, separators=(",", ":"))
                        digest = hashlib.sha256(canonical.encode("utf-8")).digest()
                        return base64.urlsafe_b64encode(digest).decode("utf-8").rstrip("=")

                    now_dt = datetime.now(timezone.utc)
                    ts = now_dt.isoformat().replace("+00:00", "Z")
                    
                    # 1. Action Request (Genesis)
                    ar_entry = ExecutionLogEntry(
                        trace_id=trace_id,
                        principal_id=principal_id,
                        sequence_number=1,
                        prev_entry_digest=ZERO_DIGEST,
                        from_state=ExecutionStateEnum.PENDING,
                        to_state=ExecutionStateEnum.PENDING,
                        artifact_type=ArtifactType.ACTION_REQUEST,
                        artifact_id=plan.plan_id,
                        artifact_digest=compute_art_digest({"implicit": True}),
                        ts=ts,
                        entry_digest=ZERO_DIGEST
                    )
                    ar_entry.entry_digest = ar_entry.compute_digest()
                    await self.store.append_log_entry(ar_entry)
                    
                    # 2. Mock Supervisor Decision
                    decision = {"approved": True}
                    if plan.supervisor_decision_fn:
                        decision = await plan.supervisor_decision_fn(plan.action_request)
                    
                    approved = decision.get("approved", True)
                    target_state = ExecutionStateEnum.AUTHORIZED if approved else ExecutionStateEnum.DENIED

                    dec_entry = ExecutionLogEntry(
                        trace_id=trace_id,
                        principal_id=principal_id,
                        sequence_number=2,
                        prev_entry_digest=ar_entry.entry_digest,
                        from_state=ExecutionStateEnum.PENDING,
                        to_state=target_state,
                        artifact_type=ArtifactType.SUPERVISOR_DECISION,
                        artifact_id=decision.get("decision_id", f"01936a8b-4c2d-7000-8000-00000000d{trace_id[:8].replace('-', '')[:4]}"),
                        artifact_digest=compute_art_digest(decision),
                        ts=ts,
                        entry_digest=ZERO_DIGEST
                    )
                    dec_entry.entry_digest = dec_entry.compute_digest()
                    await self.store.append_log_entry(dec_entry)
                    
                    if not approved:
                        return ExecutionResult(
                            trace_id=trace_id, 
                            final_state=ExecutionStateEnum.DENIED,
                            error="Supervisor denied the action"
                        )

                    # 3. Tool Call -> EXECUTING
                    call_entry = ExecutionLogEntry(
                        trace_id=trace_id,
                        principal_id=principal_id,
                        sequence_number=3,
                        prev_entry_digest=dec_entry.entry_digest,
                        from_state=ExecutionStateEnum.AUTHORIZED,
                        to_state=ExecutionStateEnum.EXECUTING,
                        artifact_type=ArtifactType.TOOL_CALL,
                        artifact_id=f"01936a8b-4c2d-7000-8000-00000000c{trace_id[:8].replace('-', '')[:4]}",
                        artifact_digest=compute_art_digest({"server": plan.tool_server, "name": plan.tool_name}),
                        ts=ts,
                        entry_digest=ZERO_DIGEST
                    )
                    call_entry.entry_digest = call_entry.compute_digest()
                    await self.store.append_log_entry(call_entry)

            # 2. Phase 9.2: Classification & Guarding (Gateway Specific)
            if self.tool_guard:
                tga_principal = principal or {
                    "auth_mode": "tga",
                    "principal_id": "system:tga",
                    "team_id": "system"
                }
                
                await self.tool_guard.validate_call(
                    server_id=plan.tool_server,
                    tool_name=plan.tool_name,
                    capability_read_only=False,
                    idempotency_key=f"tga-{trace_id[:16]}",
                    tool_args=plan.tool_args,
                    audit_logger=self.audit_logger,
                    principal=tga_principal,
                    request_id=trace_id
                )

            # 3. Dispatch to connector
            if plan.tool_dispatch_fn:
                # Use tool_call_id from the log entry if possible
                tool_call = {
                    "tool_call_id": trace_id, # Simplified
                    "call": {"server": plan.tool_server, "name": plan.tool_name, "args": plan.tool_args}
                }
                tool_effect = await plan.tool_dispatch_fn(tool_call)
            else:
                tool_effect = {"outcome": {"status": "SUCCESS"}}
            
            # 4. Record effect via standalone runtime
            entry = await self.standalone.record_tool_effect(trace_id, tool_effect)
            
            return ExecutionResult(
                trace_id=trace_id,
                final_state=entry.to_state,
                tool_effect=tool_effect
            )
            
        except Exception as e:
            logger.error(f"TGA Execution failed: {e}")
            return ExecutionResult(
                trace_id=trace_id,
                final_state=ExecutionStateEnum.FAILED,
                error=str(e)
            )

    async def recover(self, trace_id: str) -> RecoveryResult:
        """Recover a TGA plan from the store."""
        return await self.standalone.recover(trace_id)

# Singleton
_runtime_instance: Optional[TgaRuntime] = None

def get_runtime() -> TgaRuntime:
    global _runtime_instance
    if _runtime_instance is None:
        _runtime_instance = TgaRuntime()
    return _runtime_instance
