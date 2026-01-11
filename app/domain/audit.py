import logging
import json
import uuid
from datetime import datetime, timezone
import hashlib
from typing import Dict, Any, Optional
from app.domain.registry import SurfaceItem
# from app.middleware.auth_public import AuthContext # Avoid cycle, receive explicit params

audit_logger = logging.getLogger("talos.audit")
audit_logger.setLevel(logging.INFO)

class AuditLogger:
    def log_event(
        self,
        surface: SurfaceItem,
        principal: Dict[str, Any], # {principal_id, team_id, auth_mode, signer_key_id}
        http_info: Dict[str, Any], # {method, path, status_code}
        status: str, # success, failure, denied
        request_id: str,
        metadata: Dict[str, Any],
        resource: Optional[Dict[str, Any]] = None,
        correlation_id: Optional[str] = None
    ):
        
        # 1. Sanitize Metadata
        safe_meta = self._sanitize(metadata, surface)
        
        # 2. Build Event Structure
        event = {
            "schema_id": "talos.audit_event",
            "schema_version": "v1",
            "event_id": str(uuid.uuid4()),
            "ts": datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z'),
            "request_id": request_id,
            "surface_id": surface.id,
            "action": surface.audit_action,
            "status": status,
            "principal": principal,
            "http": http_info,
            "data_classification": surface.data_classification,
            "meta": safe_meta
        }
        
        if resource:
            event["resource"] = resource
            
        if correlation_id:
            event["correlation_id"] = correlation_id
            
        # 3. Canonicalize & Hash
        canonical_bytes = self._canonical_json_bytes(event)
        event_hash = hashlib.sha256(canonical_bytes).hexdigest()
        
        event["event_hash"] = event_hash
        
        # 4. Emit
        # For MVP: Log the fully constructed event
        audit_logger.info(json.dumps(event))

    def _sanitize(self, metadata: Dict[str, Any], surface: SurfaceItem) -> Dict[str, Any]:
        """Strict allowlist filtering."""
        if not metadata:
            return {}
            
        safe = {}
        allowlist = set(surface.audit_meta_allowlist)
        
        for k, v in metadata.items():
            if k not in allowlist:
                continue # DROP implicit
            
            # If authorized key, check classification for redaction
            if surface.data_classification == 'sensitive':
                # REDACT value
                # Exception: maybe allow some IDs? 
                # Spec: "redact values by ... 'REDACTED' or sha256"
                # Decision: Default REDACTED.
                safe[k] = "[REDACTED]"
            else:
                # public / metadata: Allow value
                # Still scrub known secrets just in case they snuck into an allowed field?
                # Spec says "do not permit arbitrary keys". We already filtered by allowlist.
                # Assuming allowlist[k] is safe.
                safe[k] = v
                
        return safe

    def _canonical_json_bytes(self, event: Dict[str, Any]) -> bytes:
        """RFC 8785 (JCS) style canonicalization (simplified).
        Sort keys, no whitespace.
        """
        # Ensure 'event_hash' is NOT in the input
        clean = {k: v for k, v in event.items() if k != "event_hash"}
        return json.dumps(clean, sort_keys=True, separators=(',', ':')).encode('utf-8')

_audit_logger_instance = AuditLogger()

def get_audit_logger() -> AuditLogger:
    return _audit_logger_instance
