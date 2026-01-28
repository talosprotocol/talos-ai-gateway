"""
Tool Guard Domain Service.
Implements Secondary Enforcement for MCP Tool Calls in the Gateway.
"""
import json
import logging
import hashlib
from typing import Dict, Any, Optional, Tuple
from dataclasses import dataclass
from pathlib import Path
from enum import Enum

from app.domain.audit import AuditLogger
from app.domain.registry import SurfaceItem

logger = logging.getLogger(__name__)

class GuardError(Exception):
    """Tool Guard violation."""
    def __init__(self, message: str, code: str):
        super().__init__(message)
        self.code = code

class ToolClass(str, Enum):
    READ = "read"
    WRITE = "write"

@dataclass
class GuardPolicy:
    tool_server: str
    tool_name: str
    tool_class: ToolClass
    requires_idempotency_key: bool
    read_replay_safe: bool = True

class ToolGuard:
    """
    Gateway-side enforcement of MCP policies.
    Acts as a secondary defense line before request reaches Connector.
    """
    
    def __init__(self, registry_path: str):
        self._policies: Dict[Tuple[str, str], GuardPolicy] = {}
        if registry_path and Path(registry_path).exists():
            self._load_registry(registry_path)
        else:
            logger.warning(f"ToolGuard initialized without registry at {registry_path}")

    def _load_registry(self, path: str):
        try:
            with open(path, 'r') as f:
                data = json.load(f)
            
            for t in data.get("tools", []):
                server = t["tool_server"]
                name = t["tool_name"]
                t_class = ToolClass(t["tool_class"])
                req_idem = t.get("requires_idempotency_key", False)
                replay_safe = t.get("read_replay_safe", True) # Default true
                
                self._policies[(server, name)] = GuardPolicy(
                    tool_server=server,
                    tool_name=name,
                    tool_class=t_class,
                    requires_idempotency_key=req_idem,
                    read_replay_safe=replay_safe
                )
            logger.info(f"ToolGuard loaded {len(self._policies)} policies")
        except Exception as e:
            logger.error(f"Failed to load tool registry: {e}")
            raise

    async def validate_call(
        self,
        server_id: str,
        tool_name: str,
        capability_read_only: bool,
        idempotency_key: Optional[str],
        tool_args: Dict[str, Any],
        audit_logger: Optional[AuditLogger],
        principal: Dict[str, Any],
        request_id: str
    ) -> GuardPolicy:
        """
        Validate tool call against registry and policy.
        Computes normative request digest for audit correlation.
        Emits 'tool_guard.check' audit event.
        Returns the resolved policy.
        """
        outcome = "allow"
        denial_reason = ""
        request_digest = ""
        policy = None # Initialize
        
        # Normative Envelope Construction & Hashing
        try:
            envelope = {
                "tool_server": server_id,
                "tool_name": tool_name,
                "capability": {"read_only": capability_read_only},
                "args": tool_args
                # principal_id excluded from digest per spec
            }
            # JCS (RFC 8785) - Sort keys, no spaces
            canonical_req = json.dumps(envelope, sort_keys=True, separators=(",", ":"))
            request_digest = hashlib.sha256(canonical_req.encode("utf-8")).hexdigest()
        except Exception as e:
            logger.error(f"Failed to compute request digest: {e}")
            outcome = "deny"
            denial_reason = "DIGEST_COMPUTATION_FAILED"
            # Continue to logging...
        
        try:
            if outcome == "deny":
                 raise GuardError("Digest computation failed", "SYSTEM_ERROR")

            # 1. Registry Check
            policy = self._policies.get((server_id, tool_name))
            if not policy:
                raise GuardError(f"Tool {server_id}:{tool_name} not in registry", "UNCLASSIFIED")
                
            # 2. Class vs Capability
            # Decision Table: if cap=read_only AND tool=write -> deny
            if capability_read_only and policy.tool_class == ToolClass.WRITE:
                 raise GuardError(
                    f"Write tool '{tool_name}' blocked by read-only capability",
                    "CAPABILITY_MISMATCH"
                )
            
            # 3. Idempotency Check
            if policy.requires_idempotency_key and not idempotency_key:
                raise GuardError(
                    f"Tool '{tool_name}' requires idempotency key",
                    "IDEMPOTENCY_MISSING"
                )
                
        except GuardError as e:
            outcome = "deny"
            denial_reason = f"{e.code}: {str(e)}"
            raise
        except Exception as e:
            outcome = "deny" 
            denial_reason = f"SYSTEM_ERROR: {str(e)}"
            raise GuardError(f"System error during guard check: {e}", "SYSTEM_ERROR")
        finally:
            # Audit
            if audit_logger:
                # Construct ad-hoc surface for the event
                surface = SurfaceItem(
                    id="tool_guard.check",
                    type="internal",
                    required_scopes=[],
                    attestation_required=False,
                    audit_action="tool_guard_check",
                    data_classification="system",
                    audit_meta_allowlist=[
                        "tool_server", "tool_name", "tool_class", 
                        "denial_reason", "policy_verdict", "request_digest"
                    ]
                )
                
                await audit_logger.log_event_async(
                    surface=surface,
                    principal=principal,
                    http_info={}, # Internal check
                    outcome=outcome,
                    request_id=request_id,
                    metadata={
                        "tool_server": server_id,
                        "tool_name": tool_name,
                        "tool_class": policy.tool_class.value if policy and policy.tool_class else "unknown",
                        "denial_reason": denial_reason,
                        "policy_verdict": outcome,
                        "request_digest": request_digest
                    }
                )
        return policy
