"""JSON File-based Store Implementations (DEV_MODE only)."""
import json
import os
import uuid
import logging
from datetime import datetime
from typing import List, Optional, Dict, Any
from pathlib import Path

from app.domain.interfaces import UpstreamStore, ModelGroupStore, SecretStore, McpStore, AuditStore, RoutingPolicyStore
from app import config_loader

logger = logging.getLogger(__name__)

class UpstreamJsonStore(UpstreamStore):
    def list_upstreams(self) -> List[Dict[str, Any]]:
        config = config_loader.get_config()
        # Convert dict {id: upstream} to list
        # But wait, config_loader checks 'gateway.json'. Currently it might be list or dict?
        # Looking at config_loader logic: return {"upstreams": {}, ...} implies dict.
        # But gateway.json example usually has keys. 
        # Actually in router.py logic, it might be using it as dict.
        # We will assume config['upstreams'] is dict {id: upstream_obj} or list?
        # Let's inspect gateway.json structure if possible, but assuming Dict[str, dict] based on config_loader return type hint.
        upstreams = config.get("upstreams", {})
        if isinstance(upstreams, list):
             return upstreams # Already list
        return list(upstreams.values())

    def get_upstream(self, upstream_id: str) -> Optional[Dict[str, Any]]:
        upstreams = config_loader.get_upstreams()
        # If it's a list, find by id
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
        # In dict mode, it's reference update. In list mode, reference update works too.
        config_loader.save_config(config)
        return target

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
        return target

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
        # JSON config stores only LATEST version usually in simple dict mode
        policies = config_loader.get_routing_policies()
        target = None
        if isinstance(policies, list):
            for p in policies:
                if p.get('id') == policy_id: # Legacy key was 'id' not 'policy_id'
                    target = p
                    break
        else:
            target = policies.get(policy_id)
        
        if not target:
            return None
            
        if version is not None and target.get('version') != version:
            return None # Cannot fetch old version in simple JSON store if overwritten
            
        return target

    def create_policy(self, policy: Dict[str, Any]) -> None:
        config = config_loader.get_config()
        policies = config.get("routing_policies", {})
        
        # Policy dict keys: id (legacy) or policy_id (new)?
        # Router logic uses 'id'.
        pid = policy.get('id') or policy.get('policy_id')
        if not pid: 
             raise ValueError("Policy ID missing")
        policy['id'] = pid # Ensure 'id' exists for legacy compat
        
        if isinstance(policies, list):
             # Update if exists or append
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
                return json.load(f)
        except Exception as e:
            logger.error(f"Failed to load secrets: {e}")
            return {}

    def _save(self):
        with open(self.file_path, 'w') as f:
            json.dump(self._cache, f, indent=2)

    def list_secrets(self) -> List[Dict[str, Any]]:
        # Mock metadata
        return [{"name": k, "created_at": datetime.utcnow().isoformat(), "version": 1} for k in self._cache.keys()]

    def get_secret_value(self, name: str) -> Optional[str]:
        return self._cache.get(name)

    def set_secret(self, name: str, value: str) -> None:
        self._cache[name] = value
        self._save()

    def delete_secret(self, name: str) -> bool:
        if name in self._cache:
            del self._cache[name]
            self._save()
            return True
        return False


class McpJsonStore(McpStore):
    def __init__(self, file_path: str = "mcp_config.json"):
        self.file_path = file_path
        self._cache = self._load()

    def _load(self) -> Dict[str, Any]:
        if not os.path.exists(self.file_path):
            return {"servers": [], "policies": []}
        try:
            with open(self.file_path, 'r') as f:
                data = json.load(f)
                return data
        except Exception:
            return {"servers": [], "policies": []}
            
    def _save(self):
        with open(self.file_path, 'w') as f:
            json.dump(self._cache, f, indent=2)

    def list_servers(self) -> List[Dict[str, Any]]:
        return self._cache.get("servers", [])

    def get_server(self, server_id: str) -> Optional[Dict[str, Any]]:
        for s in self._cache.get("servers", []):
            if s.get('id') == server_id:
                return s
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
                return s
        raise KeyError("Server not found")

    def delete_server(self, server_id: str) -> None:
        servers = self._cache.get("servers", [])
        self._cache["servers"] = [s for s in servers if s.get('id') != server_id]
        self._save()

    def list_policies(self, team_id: str) -> List[Dict[str, Any]]:
        # Simple filter
        return [p for p in self._cache.get("policies", []) if p.get('team_id') == team_id]

    def upsert_policy(self, policy: Dict[str, Any]) -> None:
        # Check existing
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

from app.domain.interfaces import UsageStore

class UsageJsonStore(UsageStore):
    def record_usage(self, event: Dict[str, Any]) -> None:
        logger.info(f"[USAGE] {event}")

    def get_stats(self, window_hours: int = 24) -> Dict[str, Any]:
        return {
            "requests_total": 100, # Mock some data
            "tokens_total": 50000,
            "cost_usd": 0.75,
            "latency_avg_ms": 250.0,
            "window_hours": window_hours,
            "note": "Mock stats for JSON mode"
        }

class AuditJsonStore(AuditStore):
    def append_event(self, event: Dict[str, Any]) -> None:
        # Convert datetime to ISO string for JSON serialization
        data = event.copy()
        for k, v in data.items():
            if isinstance(v, datetime):
                data[k] = v.isoformat()
                
        logger.info(f"AUDIT: {json.dumps(data)}")
        # Optionally append to file
        with open("audit.log", "a") as f:
            f.write(json.dumps(data) + "\n")

    def list_events(self, filters: Dict[str, Any], limit: int = 100) -> List[Dict[str, Any]]:
        events = []
        if os.path.exists("audit.log"):
            with open("audit.log", "r") as f:
                for line in f:
                    try:
                        events.append(json.loads(line))
                    except:
                        pass
        # Filter logic omitted for brevity in Dev Mode
        return events[-limit:]
