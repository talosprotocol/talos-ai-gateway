import logging
import json
import secrets
import os
import time
from datetime import datetime, timezone
import hashlib
import hmac
from typing import Dict, Any, Optional, List
from app.domain.registry import SurfaceItem
import asyncio
from app.domain.sink import AuditSink, StdOutSink

from app.utils.id import uuid7

class AuditLogger:
    def __init__(self, sink: Optional[AuditSink] = None):
        self.sink: AuditSink = sink or StdOutSink()
        self.ip_hmac_key: str = os.environ.get("AUDIT_IP_HMAC_KEY") or ""
        self.ip_hmac_key_id: str = os.environ.get("AUDIT_IP_HMAC_KEY_ID", "dev-key-v1")
        self.trusted_proxies: List[str] = []
        
        # Security hardening for production
        is_prod = os.getenv("ENV", "development").lower() == "production"
        if not self.ip_hmac_key:
            if is_prod:
                raise RuntimeError("AUDIT_IP_HMAC_KEY must be set in production")
            self.ip_hmac_key = "dev-ip-key-secret-32-chars-long-!!!" # Standardized dev fallback
            logging.warning("Using insecure default AUDIT_IP_HMAC_KEY")

        trusted = os.environ.get("TRUSTED_PROXIES")
        if trusted:
            self.trusted_proxies = [p.strip() for p in trusted.split(",")]
        else:
            # Default to local only
            self.trusted_proxies = ["127.0.0.1", "::1"]
            if is_prod:
                logging.warning("No TRUSTED_PROXIES configured. Audit client IPs might be lost.")

    async def log_event_async(
        self,
        surface: SurfaceItem,
        principal: Dict[str, Any],
        http_info: Dict[str, Any],
        outcome: str,
        request_id: str,
        metadata: Dict[str, Any],
        resource: Optional[Dict[str, Any]] = None
    ):
        event = self._build_event(surface, principal, http_info, outcome, request_id, metadata, resource)
        await self.sink.emit(event)
        
    def log_event(
        self,
        surface: SurfaceItem,
        principal: Dict[str, Any],
        http_info: Dict[str, Any],
        outcome: str,
        request_id: str,
        metadata: Dict[str, Any],
        resource: Optional[Dict[str, Any]] = None
    ):
        """Synchronous fire-and-forget logging."""
        event = self._build_event(surface, principal, http_info, outcome, request_id, metadata, resource)
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(self.sink.emit(event))
        except RuntimeError:
            try:
                asyncio.run(self.sink.emit(event))
            except Exception as e:
                logging.error(f"AUDIT LOGGING FAILURE: {e}")

    def _build_event(self, surface: SurfaceItem, principal: Dict, http_info: Dict, outcome, request_id, metadata, resource=None) -> Dict[str, Any]:
        # 1. Sanitize Metadata (Scalar Only, Allowlist)
        safe_meta = self._sanitize(metadata, surface)
        
        # 2. Prepare HTTP Info (Path Template & IP Hashing)
        http_clean = {
            "method": http_info.get("method", "UNKNOWN").upper(),
            "path": surface.path_template or "/UNKNOWN",
            "status_code": http_info.get("status_code", 0)
        }
        
        # 2. IP Privacy: Strictly honor trusted proxies
        client_ip = http_info.get("client_ip")
        is_trusted = http_info.get("is_trusted", False) # Middleware must set this
        
        if client_ip and is_trusted and client_ip not in ("unknown", "127.0.0.1", "::1", "localhost"):
            # Ensure key is present (should be set in __init__)
            key = self.ip_hmac_key or "placeholder-key-safe-to-ignore"
            key_bytes = key.encode('utf-8')
            ip_bytes = client_ip.encode('utf-8')
            hmac_hex = hmac.new(key_bytes, ip_bytes, hashlib.sha256).hexdigest()
            
            http_clean["client_ip_hash"] = hmac_hex
            http_clean["client_ip_hash_alg"] = "hmac-sha256"
            http_clean["client_ip_hash_key_id"] = self.ip_hmac_key_id
        # Else: omitted per Locked Rule 4
        
        # 3. Principal Logic (Absent not Null)
        principal_clean = {"auth_mode": principal.get("auth_mode", "anonymous")}
        auth_mode = principal_clean["auth_mode"]
        
        if auth_mode == "signed":
            principal_clean["principal_id"] = principal.get("principal_id")
            principal_clean["team_id"] = principal.get("team_id")
            principal_clean["signer_key_id"] = principal.get("signer_key_id")
        elif auth_mode == "bearer":
            principal_clean["principal_id"] = principal.get("principal_id")
            principal_clean["team_id"] = principal.get("team_id")
        else: # anonymous
            principal_clean["auth_mode"] = "anonymous"
            principal_clean["principal_id"] = "anonymous"

        # 4. Build Structure
        # ts format: RFC3339 UTC with millisecond precision (exactly 3 digits)
        ts = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%f')[:-3] + 'Z'
        
        event = {
            "schema_id": "talos.audit_event",
            "schema_version": "v1",
            "event_id": uuid7(),
            "ts": ts,
            "request_id": request_id,
            "surface_id": surface.id,
            "outcome": outcome,
            "principal": principal_clean,
            "http": http_clean
            # Meta is added conditionally
        }
        
        if safe_meta:
            event["meta"] = safe_meta
        
        if resource and resource.get("resource_type") and resource.get("resource_id"):
            event["resource"] = {
                "resource_type": resource["resource_type"],
                "resource_id": resource["resource_id"]
            }
            
        # 5. Canonicalize & Hash (Normative Pipeline)
        canonical_bytes = self._canonical_json_bytes(event)
        event["event_hash"] = hashlib.sha256(canonical_bytes).hexdigest()
        return event

    def _sanitize(self, metadata: Dict[str, Any], surface: SurfaceItem) -> Dict[str, Any]:
        """Strict allowlist filtering + Scalar enforcement + Redaction Telemetry."""
        if not metadata:
            return {}
            
        safe: Dict[str, Any] = {}
        redacted_keys = []
        allowlist = set(surface.audit_meta_allowlist or [])
        
        MAX_SAFE_INT = 9007199254740991
        MIN_SAFE_INT = -9007199254740991
        
        RESERVED_KEYS = {"meta_redaction_applied", "meta_redacted_keys"}

        for k, v in metadata.items():
            if k in RESERVED_KEYS:
                # Silently drop reserved keys if user tries to inject them
                continue

            if k not in allowlist:
                redacted_keys.append(k)
                continue
            
            # Enforce Scalar Types: string, number, boolean, null
            if isinstance(v, (str, float, bool)) or v is None:
                # Max string length enforcement (1024 as per locked spec)
                if isinstance(v, str) and len(v) > 1024:
                    safe[k] = v[:1021] + "..."
                    redacted_keys.append(f"{k} (truncated)")
                else:
                    safe[k] = v
            elif isinstance(v, int):
                # Integer Range Check
                if MIN_SAFE_INT <= v <= MAX_SAFE_INT:
                    safe[k] = v
                else:
                    redacted_keys.append(f"{k} (unsafe integer)")
            else:
                redacted_keys.append(f"{k} (invalid type)")
                    
        if redacted_keys:
            safe["meta_redacted_keys"] = sorted(redacted_keys) # Sorted List
            # Emit telemetry via logs (can be scraped by Loki/Fluentd)
            logging.warning(f"AUDIT_META_REDACTION: surface={surface.id} keys={redacted_keys}")
                    
        return safe

    def _canonical_json_bytes(self, event: Dict[str, Any]) -> bytes:
        """RFC 8785 (JCS) style canonicalization."""
        from app.domain.a2a.canonical import canonical_json_bytes
        # Ensure 'event_hash' is NOT in the input
        clean = {k: v for k, v in event.items() if k != "event_hash"}
        return canonical_json_bytes(clean)

