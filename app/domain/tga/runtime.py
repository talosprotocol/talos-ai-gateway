"""TGA Runtime Loop bridged to the standalone talos-governance-agent package."""
import logging
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional
from datetime import datetime, timezone

from talos_governance_agent.domain.runtime import TgaRuntime as StandaloneTgaRuntime
from talos_governance_agent.domain.models import ExecutionStateEnum
from talos_governance_agent.domain.runtime import ExecutionPlan as StandaloneExecutionPlan

from app.domain.mcp.tool_guard import ToolGuard
from app.adapters.mcp.client import McpClient
from app.domain.tga.state_store import get_state_store, TgaStateStore
from app.settings import settings

logger = logging.getLogger(__name__)

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
    tool_server: str
    tool_name: str
    tool_args: Dict[str, Any]
    action_request: Dict[str, Any]
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
                # In a consolidated world, everything should have a JWS eventually.
                logger.warning(f"Executing TGA plan {trace_id} without JWS - using mock authorization")
                # For now, we manually drive the standalone's store to maintain state machine parity
                existing = await self.store.load_state(trace_id)
                if existing and existing.current_state in (ExecutionStateEnum.COMPLETED, ExecutionStateEnum.FAILED, ExecutionStateEnum.DENIED):
                     return ExecutionResult(trace_id=trace_id, final_state=existing.current_state)
                
                # Mock authorization entry
                # (Normally authorize_tool_call does this)
                pass

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

# Singleton
_runtime_instance: Optional[TgaRuntime] = None

def get_runtime() -> TgaRuntime:
    global _runtime_instance
    if _runtime_instance is None:
        _runtime_instance = TgaRuntime()
    return _runtime_instance
