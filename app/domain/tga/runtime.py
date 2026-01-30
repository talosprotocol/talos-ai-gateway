"""TGA Runtime Loop for Phase 9.3.4.

Implements crash-safe TGA execution with Moore machine state transitions.
Recovery reconstructs state from append-only log without double-execution.
"""
import hashlib
from app.utils.id import uuid7
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional
from enum import Enum

from app.domain.mcp.tool_guard import ToolGuard
from app.adapters.mcp.client import McpClient
from app.domain.tga.state_store import (
    TgaStateStore,
    ExecutionLogEntry,
    ExecutionState,
    ExecutionStateEnum,
    StateStoreError,
    ZERO_DIGEST,
    get_state_store,
)

logger = logging.getLogger(__name__)


class RuntimeError(Exception):
    """Runtime execution error."""
    def __init__(self, message: str, code: str):
        super().__init__(message)
        self.code = code


@dataclass
class ExecutionPlan:
    """Plan for TGA execution."""
    trace_id: str
    plan_id: str
    tool_server: str
    tool_name: str
    tool_args: Dict[str, Any]
    action_request: Dict[str, Any]
    supervisor_decision_fn: Optional[Callable] = None
    tool_dispatch_fn: Optional[Callable] = None


@dataclass
class ExecutionResult:
    """Result of TGA execution."""
    trace_id: str
    final_state: ExecutionStateEnum
    tool_effect: Optional[Dict[str, Any]] = None
    error: Optional[str] = None


@dataclass
class RecoveryResult:
    """Result of recovery operation."""
    trace_id: str
    recovered_state: ExecutionStateEnum
    recovered_from_seq: int
    re_dispatched: bool
    tool_effect: Optional[Dict[str, Any]] = None
    tool_call_payload: Optional[Dict[str, Any]] = None


class TgaRuntime:
    """
    Crash-safe TGA runtime with append-only state persistence.
    ...
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
    
    async def execute_plan(
        self, 
        plan: ExecutionPlan,
        principal: Optional[Dict[str, Any]] = None
    ) -> ExecutionResult:
        """Execute a TGA plan with crash-safe persistence."""
        trace_id = plan.trace_id
        
        try:
            # 1. Acquire lock
            await self.store.acquire_trace_lock(trace_id)
            
            # Check if already exists
            existing = await self.store.load_state(trace_id)
            if existing:
                logger.info(f"Trace {trace_id} already exists at {existing.current_state}")
                return await self._resume_execution(plan, existing)
            
            # 2. Genesis: persist action_request, append PENDING
            ar_digest = self._compute_digest(plan.action_request)
            genesis_entry = self._make_entry(
                trace_id=trace_id,
                seq=1,
                prev_digest=ZERO_DIGEST,
                from_state=ExecutionStateEnum.PENDING,
                to_state=ExecutionStateEnum.PENDING,
                artifact_type="action_request",
                artifact_id=plan.action_request.get("action_request_id", plan.plan_id),
                artifact_digest=ar_digest,
                artifact_payload=plan.action_request
            )
            await self.store.append_log_entry(genesis_entry)
            
            # 3. Get supervisor decision
            if plan.supervisor_decision_fn:
                decision = await plan.supervisor_decision_fn(plan.action_request)
            else:
                # Mock approve for testing
                decision = {"approved": True, "capability": {}}
            
            sd_id = decision.get("decision_id", self._generate_id())
            sd_digest = self._compute_digest(decision)

            # --- Phase 9.2: Classification & Guarding ---
            # Verified against Tool Registry before transitioning to EXECUTING
            if self.tool_guard:
                # Construct TGA principal for auditing
                tga_principal = principal or {
                    "auth_mode": "tga",
                    "principal_id": "system:tga",
                    "team_id": "system"
                }
                
                # For TGA, we use trace_id as basis for idempotency if not provided
                idempotency_key = f"tga-{trace_id[:16]}"
                
                await self.tool_guard.validate_call(
                    server_id=plan.tool_server,
                    tool_name=plan.tool_name,
                    capability_read_only=False, # TGA is always deliberate
                    idempotency_key=idempotency_key,
                    tool_args=plan.tool_args,
                    audit_logger=self.audit_logger,
                    principal=tga_principal,
                    request_id=trace_id
                )
            
            if not decision.get("approved"):
                # DENIED
                denied_entry = self._make_entry(
                    trace_id=trace_id,
                    seq=2,
                    prev_digest=genesis_entry.entry_digest,
                    from_state=ExecutionStateEnum.PENDING,
                    to_state=ExecutionStateEnum.DENIED,
                    artifact_type="supervisor_decision",
                    artifact_id=sd_id,
                    artifact_digest=sd_digest,
                    artifact_payload=decision
                )
                await self.store.append_log_entry(denied_entry)
                return ExecutionResult(
                    trace_id=trace_id,
                    final_state=ExecutionStateEnum.DENIED,
                    error="Supervisor denied the action"
                )
            
            # AUTHORIZED
            auth_entry = self._make_entry(
                trace_id=trace_id,
                seq=2,
                prev_digest=genesis_entry.entry_digest,
                from_state=ExecutionStateEnum.PENDING,
                to_state=ExecutionStateEnum.AUTHORIZED,
                artifact_type="supervisor_decision",
                artifact_id=sd_id,
                artifact_digest=sd_digest,
                artifact_payload=decision
            )
            await self.store.append_log_entry(auth_entry)
            
            # 4. Create tool_call, append EXECUTING
            tool_call: Dict[str, Any] = self._create_tool_call(plan, decision)
            tc_id = str(tool_call.get("tool_call_id", self._generate_id()))
            tc_digest = self._compute_digest(tool_call)
            tc_idempotency_key = tool_call.get("idempotency_key")
            
            exec_entry = self._make_entry(
                trace_id=trace_id,
                seq=3,
                prev_digest=auth_entry.entry_digest,
                from_state=ExecutionStateEnum.AUTHORIZED,
                to_state=ExecutionStateEnum.EXECUTING,
                artifact_type="tool_call",
                artifact_id=tc_id,
                artifact_digest=tc_digest,
                tool_call_id=tc_id,
                idempotency_key=tc_idempotency_key,
                artifact_payload=tool_call
            )
            await self.store.append_log_entry(exec_entry)
            
            # Note: Phase 9.2 ToolGuard check was performed above before transition to AUTHORIZED.
            # Redundant legacy classifier logic removed.

            # 5. Dispatch to connector (idempotent)
            return await self._dispatch_and_complete(
                plan, tool_call, exec_entry, trace_id, tc_id, tc_idempotency_key
            )
            
        finally:
            await self.store.release_trace_lock(trace_id)

    async def _dispatch_and_complete(
        self, plan, tool_call, exec_entry, trace_id, tc_id, idempotency_key
    ) -> ExecutionResult:
        """Helper to dispatch tool and persist completion."""
        if plan.tool_dispatch_fn:
            tool_effect = await plan.tool_dispatch_fn(tool_call)
        else:
            tool_effect = {"outcome": {"status": "SUCCESS"}}
        
        te_id = tool_effect.get("tool_effect_id", self._generate_id())
        te_digest = self._compute_digest(tool_effect)
        
        outcome_status = tool_effect.get("outcome", {}).get("status", "SUCCESS")
        final_state = (
            ExecutionStateEnum.COMPLETED 
            if outcome_status == "SUCCESS" 
            else ExecutionStateEnum.FAILED
        )
        
        effect_entry = self._make_entry(
            trace_id=trace_id,
            seq=4,
            prev_digest=exec_entry.entry_digest,
            from_state=ExecutionStateEnum.EXECUTING,
            to_state=final_state,
            artifact_type="tool_effect",
            artifact_id=te_id,
            artifact_digest=te_digest,
            tool_call_id=tc_id,
            idempotency_key=idempotency_key,
            artifact_payload=tool_effect
        )
        await self.store.append_log_entry(effect_entry)
        
        return ExecutionResult(
            trace_id=trace_id,
            final_state=final_state,
            tool_effect=tool_effect
        )
    
    async def recover(self, trace_id: str) -> RecoveryResult:
        """Recover from crash by replaying log and resuming execution."""
        try:
            await self.store.acquire_trace_lock(trace_id)
            return await self._recover_impl(trace_id)
        finally:
            await self.store.release_trace_lock(trace_id)

    async def _recover_impl(self, trace_id: str) -> RecoveryResult:
        """Internal recovery logic (assumes lock held)."""
        state = await self.store.load_state(trace_id)
        if not state:
            raise RuntimeError(f"No state found for trace {trace_id}", "STATE_RECOVERY_FAILED")
        
        entries = await self.store.list_log_entries(trace_id)
        if not entries:
            raise RuntimeError(f"No log entries for trace {trace_id}", "STATE_RECOVERY_FAILED")
        
        # Security Invariant I5: Verify hash chain and entries
        prev_digest = ZERO_DIGEST
        for entry in entries:
            if entry.prev_entry_digest != prev_digest:
                raise RuntimeError(
                    f"Hash chain broken at sequence {entry.sequence_number}",
                    "STATE_CHECKSUM_MISMATCH"
                )
            computed = entry.compute_digest()
            if entry.entry_digest != computed:
                raise RuntimeError(
                    f"Entry digest mismatch at sequence {entry.sequence_number}",
                    "STATE_CHECKSUM_MISMATCH"
                )
            prev_digest = entry.entry_digest
            
        last_entry = entries[-1]
        
        # Verify derived state matches last log entry
        if state.last_entry_digest != last_entry.entry_digest:
             raise RuntimeError(
                "Derived state digest mismatch with last log entry",
                "STATE_CHECKSUM_MISMATCH"
            )
        
        if state.current_state == ExecutionStateEnum.EXECUTING:
            # Reconstruct what happened
            tc_entry = next((e for e in entries if e.artifact_type == "tool_call"), None)
            if not tc_entry:
                raise RuntimeError("EXECUTING state but no tool_call entry", "STATE_RECOVERY_FAILED")
            
            # Check if we have a tool_effect recorded
            te_entry = next((e for e in entries if e.artifact_type == "tool_effect"), None)
            
            if te_entry is None:
                # CRASH during execution: re-dispatch
                logger.info(f"Recovery: re-dispatching tool_call {tc_entry.tool_call_id}")
                return RecoveryResult(
                    trace_id=trace_id,
                    recovered_state=state.current_state,
                    recovered_from_seq=last_entry.sequence_number,
                    re_dispatched=True,
                    tool_effect=None,
                    tool_call_payload=tc_entry.artifact_payload
                )
            else:
                # We have the effect, but maybe state didn't transition?
                # Actually if to_state was COMPLETED/FAILED, current_state would be that.
                # If current_state is EXECUTING but te_entry exists, something is weird.
                # But in our Moore machine, COMPLETED/FAILED happens with tool_effect entry.
                pass
        
        return RecoveryResult(
            trace_id=trace_id,
            recovered_state=state.current_state,
            recovered_from_seq=last_entry.sequence_number,
            re_dispatched=False
        )
    
    async def _resume_execution(self, plan: ExecutionPlan, state: ExecutionState) -> ExecutionResult:
        """Resume execution from existing state."""
        if state.current_state in (ExecutionStateEnum.COMPLETED, ExecutionStateEnum.FAILED, ExecutionStateEnum.DENIED):
            # Already done, return cached effect if possible
            entries = await self.store.list_log_entries(plan.trace_id)
            te_entry = next((e for e in reversed(entries) if e.artifact_type == "tool_effect"), None)
            return ExecutionResult(
                trace_id=plan.trace_id, 
                final_state=state.current_state,
                tool_effect=te_entry.artifact_payload if te_entry else None
            )
        
        # Use internal recover implementation as we already hold the lock
        recovery = await self._recover_impl(plan.trace_id)
        
        if recovery.re_dispatched and recovery.tool_call_payload:
            # Finish the dispatch
            entries = await self.store.list_log_entries(plan.trace_id)
            exec_entry = next((e for e in reversed(entries) if e.to_state == ExecutionStateEnum.EXECUTING), None)
            
            if not exec_entry:
                raise RuntimeError("Failed to find EXECUTING entry during resume", "STATE_RECOVERY_FAILED")
                
            return await self._dispatch_and_complete(
                plan, 
                recovery.tool_call_payload, 
                exec_entry, 
                plan.trace_id, 
                exec_entry.tool_call_id, 
                exec_entry.idempotency_key
            )
            
        return ExecutionResult(trace_id=plan.trace_id, final_state=recovery.recovered_state)
    
    def _make_entry(
        self,
        trace_id: str,
        seq: int,
        prev_digest: str,
        from_state: ExecutionStateEnum,
        to_state: ExecutionStateEnum,
        artifact_type: str,
        artifact_id: str,
        artifact_digest: str,
        tool_call_id: Optional[str] = None,
        idempotency_key: Optional[str] = None,
        artifact_payload: Optional[Dict[str, Any]] = None
    ) -> ExecutionLogEntry:
        """Create a log entry with computed digest."""
        entry = ExecutionLogEntry(
            schema_id="talos.tga.execution_log_entry",
            schema_version="v1",
            trace_id=trace_id,
            sequence_number=seq,
            prev_entry_digest=prev_digest,
            entry_digest="",
            ts=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z",
            from_state=from_state,
            to_state=to_state,
            artifact_type=artifact_type,
            artifact_id=artifact_id,
            artifact_digest=artifact_digest,
            tool_call_id=tool_call_id,
            idempotency_key=idempotency_key,
            artifact_payload=artifact_payload
        )
        entry.entry_digest = entry.compute_digest()
        return entry
    
    def _compute_digest(self, data: Dict[str, Any]) -> str:
        """Compute SHA-256 digest of canonical JSON."""
        canonical = json.dumps(data, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    
    def _generate_id(self) -> str:
        """Generate a proper time-ordered UUID v7."""
        return str(uuid7())
    
    def _create_tool_call(
        self, 
        plan: ExecutionPlan, 
        decision: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Create a tool_call artifact from plan and decision."""
        import uuid
        return {
            "tool_call_id": uuid7(),
            "trace_id": plan.trace_id,
            "plan_id": plan.plan_id,
            "capability": decision.get("capability", {}),
            "call": plan.action_request.get("call", {}),
            "idempotency_key": f"idem-{plan.trace_id[:8]}"
        }


# Singleton
_runtime_instance: Optional[TgaRuntime] = None


def get_runtime() -> TgaRuntime:
    """Get or create the TGA runtime singleton."""
    global _runtime_instance
    if _runtime_instance is None:
        _runtime_instance = TgaRuntime()
    return _runtime_instance
