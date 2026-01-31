"""JSON File-based Store Implementations (DEV_MODE only)."""
import json
import os
import logging
from datetime import datetime, timezone
from typing import List, Optional, Dict, Any, cast

from app.domain.interfaces import UpstreamStore, ModelGroupStore, McpStore, AuditStore, RoutingPolicyStore, PrincipalStore, UsageStore
from app.domain.secrets.ports import SecretStore
from app import config_loader

logger = logging.getLogger(__name__)

# ... existing UpstreamJsonStore ...
class UpstreamJsonStore(UpstreamStore):
    def list_upstreams(self) -> List[Dict[str, Any]]:
        config = config_loader.get_config()
        upstreams = config.get("upstreams", {})
        if isinstance(upstreams, list):
             return upstreams
        return list(upstreams.values())

    def get_upstream(self, upstream_id: str) -> Optional[Dict[str, Any]]:
        upstreams = config_loader.get_upstreams()
        if isinstance(upstreams, list):
            for u in upstreams:
                if u.get('id') == upstream_id:
                    return u
            return None
        return upstreams.get(upstream_id)

    def create_upstream(self, upstream: Dict[str, Any]) -> None:
        config = config_loader.get_config()
        upstreams = config.get("upstreams", {})
        if isinstance(upstreams, list):
             upstreams.append(upstream)
        else:
             upstreams[upstream['id']] = upstream
        config["upstreams"] = upstreams
        config_loader.save_config(config)

    def update_upstream(self, upstream_id: str, updates: Dict[str, Any], expected_version: Optional[int] = None) -> Dict[str, Any]:
        config = config_loader.get_config()
        upstreams = config.get("upstreams", {})
        
        target = None
        if isinstance(upstreams, list):
            for u in upstreams:
                if u.get('id') == upstream_id:
                    target = u
                    break
        else:
            target = upstreams.get(upstream_id)
            
        if not target:
            raise KeyError(f"Upstream {upstream_id} not found")
            
        target.update(updates)
        config_loader.save_config(config)
        return cast(Dict[str, Any], target)

    def delete_upstream(self, upstream_id: str) -> None:
        config = config_loader.get_config()
        upstreams = config.get("upstreams", {})
        if isinstance(upstreams, list):
            config["upstreams"] = [u for u in upstreams if u.get('id') != upstream_id]
        else:
            if upstream_id in upstreams:
                del upstreams[upstream_id]
        config_loader.save_config(config)


class ModelGroupJsonStore(ModelGroupStore):
    def list_model_groups(self) -> List[Dict[str, Any]]:
        groups = config_loader.get_model_groups()
        if isinstance(groups, list):
            return groups
        return list(groups.values())

    def get_model_group(self, group_id: str) -> Optional[Dict[str, Any]]:
        groups = config_loader.get_model_groups()
        if isinstance(groups, list):
             for g in groups:
                 if g.get('id') == group_id:
                     return g
             return None
        return groups.get(group_id)

    def create_model_group(self, group: Dict[str, Any]) -> None:
        config = config_loader.get_config()
        groups = config.get("model_groups", {})
        if isinstance(groups, list):
            groups.append(group)
        else:
            groups[group['id']] = group
        config["model_groups"] = groups
        config_loader.save_config(config)

    def update_model_group(self, group_id: str, updates: Dict[str, Any], expected_version: Optional[int] = None) -> Dict[str, Any]:
        config = config_loader.get_config()
        groups = config.get("model_groups", {})
        target = None
        if isinstance(groups, list):
            for g in groups:
                if g.get('id') == group_id:
                    target = g
                    break
        else:
            target = groups.get(group_id)
            
        if not target:
             raise KeyError(f"ModelGroup {group_id} not found")
        
        target.update(updates)
        config_loader.save_config(config)
        return cast(Dict[str, Any], target)

    def delete_model_group(self, group_id: str) -> None:
        config = config_loader.get_config()
        groups = config.get("model_groups", {})
        if isinstance(groups, list):
            config["model_groups"] = [g for g in groups if g.get('id') != group_id]
        else:
            if group_id in groups:
                del groups[group_id]
        config_loader.save_config(config)


class RoutingPolicyJsonStore(RoutingPolicyStore):
    def list_policies(self) -> List[Dict[str, Any]]:
        policies = config_loader.get_routing_policies()
        if isinstance(policies, list):
             return policies
        return list(policies.values())

    def get_policy(self, policy_id: str, version: Optional[int] = None) -> Optional[Dict[str, Any]]:
        policies = config_loader.get_routing_policies()
        target = None
        if isinstance(policies, list):
            for p in policies:
                if p.get('id') == policy_id:
                    target = p
                    break
        else:
            target = policies.get(policy_id)
        
        if not target:
            return None
        if version is not None and target.get('version') != version:
            return None
        return target

    def create_policy(self, policy: Dict[str, Any]) -> None:
        config = config_loader.get_config()
        policies = config.get("routing_policies", {})
        
        pid = policy.get('id') or policy.get('policy_id')
        if not pid: 
             raise ValueError("Policy ID missing")
        policy['id'] = pid
        
        if isinstance(policies, list):
             existing = False
             for i, p in enumerate(policies):
                 if p.get('id') == pid:
                     policies[i] = policy
                     existing = True
                     break
             if not existing:
                 policies.append(policy)
        else:
             policies[pid] = policy
        
        config["routing_policies"] = policies
        config_loader.save_config(config)

    def delete_policy(self, policy_id: str) -> None:
        config = config_loader.get_config()
        policies = config.get("routing_policies", {})
        if isinstance(policies, list):
             new_policies = [p for p in policies if p.get('id') != policy_id and p.get('policy_id') != policy_id]
             config["routing_policies"] = new_policies
        else:
             if policy_id in policies:
                 del policies[policy_id]
        config_loader.save_config(config)


class SecretJsonStore(SecretStore):
    def __init__(self, file_path: str = "secrets.json"):
        self.file_path = file_path
        self._cache = self._load()

    def _load(self) -> Dict[str, str]:
        if not os.path.exists(self.file_path):
            return {}
        try:
            with open(self.file_path, 'r') as f:
                return cast(Dict[str, str], json.load(f))
        except Exception:
            return {}

    def _save(self) -> None:
        with open(self.file_path, 'w') as f:
            json.dump(self._cache, f, indent=2)

    def list_secrets(self) -> List[Dict[str, Any]]:
        return [{"name": k, "created_at": datetime.now(timezone.utc).isoformat(), "version": 1} for k in self._cache.keys()]

    def get_secret_value(self, name: str) -> Optional[str]:
        return self._cache.get(name)

    def set_secret(self, name: str, value: str, expected_kek_id: Optional[str] = None) -> bool:
        self._cache[name] = value
        self._save()
        return True

    def delete_secret(self, name: str) -> bool:
        if name in self._cache:
            del self._cache[name]
            self._save()
            return True
        return False

    def get_stale_counts(self) -> Dict[str, int]:
        # JSON store doesn't track KEK IDs per secret yet.
        return {"unknown": len(self._cache)}

    def get_secrets_batch(self, batch_size: int, cursor: Optional[str] = None) -> List[Dict[str, Any]]:
        names = sorted(self._cache.keys())
        start_idx = 0
        if cursor:
            try:
                start_idx = names.index(cursor) + 1
            except ValueError:
                pass
        
        batch_names = names[start_idx:start_idx + batch_size]
        return [{"name": name, "version": 1} for name in batch_names]


class McpJsonStore(McpStore):
    def __init__(self, file_path: str = "mcp_config.json"):
        self.file_path = file_path
        self._cache = self._load()

    def _load(self) -> Dict[str, Any]:
        if not os.path.exists(self.file_path):
            return {"servers": [], "policies": []}
        try:
            with open(self.file_path, 'r') as f:
                return cast(Dict[str, Any], json.load(f))
        except Exception:
            return {"servers": [], "policies": []}
            
    def _save(self) -> None:
        with open(self.file_path, 'w') as f:
            json.dump(self._cache, f, indent=2)

    def list_servers(self) -> List[Dict[str, Any]]:
        return cast(List[Dict[str, Any]], self._cache.get("servers", []))

    def get_server(self, server_id: str) -> Optional[Dict[str, Any]]:
        for s in self._cache.get("servers", []):
            if s.get('id') == server_id:
                return cast(Dict[str, Any], s)
        return None

    def create_server(self, server: Dict[str, Any]) -> None:
        self._cache.setdefault("servers", []).append(server)
        self._save()

    def update_server(self, server_id: str, updates: Dict[str, Any], expected_version: Optional[int] = None) -> Dict[str, Any]:
        servers = self._cache.get("servers", [])
        for s in servers:
            if s.get('id') == server_id:
                s.update(updates)
                self._save()
                return cast(Dict[str, Any], s)
        raise KeyError("Server not found")

    def delete_server(self, server_id: str) -> None:
        servers = self._cache.get("servers", [])
        self._cache["servers"] = [s for s in servers if s.get('id') != server_id]
        self._save()

    def list_policies(self, team_id: str) -> List[Dict[str, Any]]:
        return [p for p in self._cache.get("policies", []) if p.get('team_id') == team_id]

    def upsert_policy(self, policy: Dict[str, Any]) -> None:
        policies = self._cache.setdefault("policies", [])
        existing = False
        for i, p in enumerate(policies):
            if p.get('id') == policy.get('id'):
                policies[i] = policy
                existing = True
                break
        if not existing:
            policies.append(policy)
        self._save()

    def delete_policy(self, policy_id: str) -> None:
        policies = self._cache.get("policies", [])
        self._cache["policies"] = [p for p in policies if p.get('id') != policy_id]
        self._save()


# Live In-Memory Usage Tracker (Singleton Pattern)
_live_usage_stats = {
    "requests": 0,
    "tokens": 0,
    "cost": 0.0,
    "latency_acc": 0.0
}

class UsageJsonStore(UsageStore):
    def record_usage(self, event: Dict[str, Any]) -> None:
        logger.info(f"[USAGE] {event}")
        _live_usage_stats["requests"] += 1
        _live_usage_stats["tokens"] += (event.get("input_tokens", 0) + event.get("output_tokens", 0))
        # Cost aggregation would need pricing model, ignoring for simple counter
        _live_usage_stats["latency_acc"] += event.get("latency_ms", 0)

    def get_stats(self, window_hours: int = 24) -> Dict[str, Any]:
        req = _live_usage_stats["requests"]
        return {
            "requests_total": req,
            "tokens_total": _live_usage_stats["tokens"],
            "cost_usd": _live_usage_stats["cost"],
            "latency_avg_ms": (_live_usage_stats["latency_acc"] / req) if req > 0 else 0,
            "window_hours": window_hours,
            "note": "Live In-Memory Stats"
        }

class AuditJsonStore(AuditStore):
    def append_event(self, event: Dict[str, Any]) -> None:
        data = event.copy()
        for k, v in data.items():
            if isinstance(v, datetime):
                data[k] = v.isoformat()
        logger.info(f"AUDIT: {json.dumps(data)}")

    def list_events(self, filters: Dict[str, Any], limit: int = 100) -> List[Dict[str, Any]]:
        # For dev mode without file persistence, return empty or implement log parsing
        return []

    def get_dashboard_stats(self, window_hours: int = 24) -> Dict[str, Any]:
        # Generate basic live stats if possible, or minimal valid struct
        return {
            "requests_24h": _live_usage_stats["requests"],
            "denial_reason_counts": {},
            "request_volume_series": []
        }

class JsonPrincipalStore(PrincipalStore):
    def __init__(self, file_path: str = "principals.json"):
        self.file_path = file_path
        self._cache = self._load()

    def _load(self) -> Dict[str, Any]:
        if not os.path.exists(self.file_path):
             # Default dev-principal if missing
            return {
                "dev-id": {
                    "id": "dev-id",
                    "org_id": "org-dev",
                    "role": "admin",
                    "api_keys": [{"id": "key-dev", "hash": "..."}] 
                }
            }
        try:
            with open(self.file_path, 'r') as f:
                return cast(Dict[str, Any], json.load(f))
        except Exception:
            return {}

    def get_principal(self, pid: str) -> Optional[Dict[str, Any]]:
        return self._cache.get(pid)
