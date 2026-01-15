"""Postgres Store Implementations."""
import logging
from datetime import datetime, timedelta, timezone
from typing import List, Optional, Dict, Any
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import desc
from app.domain.interfaces import UpstreamStore, ModelGroupStore, SecretStore, McpStore, AuditStore, RoutingPolicyStore, PrincipalStore
from app.adapters.postgres.models import (
    LlmUpstream, ModelGroup, Secret, McpServer, McpPolicy, AuditEvent, 
    Deployment, Principal, RoutingPolicy, UsageEvent
)

logger = logging.getLogger(__name__)

def to_dict(obj):
    if not obj:
        return None
    d = {c.name: getattr(obj, c.name) for c in obj.__table__.columns}
    return d

class PostgresUpstreamStore(UpstreamStore):
    def __init__(self, db: Session):
        self.db = db

    def list_upstreams(self) -> List[Dict[str, Any]]:
        objs = self.db.query(LlmUpstream).all()
        return [to_dict(o) for o in objs]

    def get_upstream(self, upstream_id: str) -> Optional[Dict[str, Any]]:
        obj = self.db.query(LlmUpstream).filter(LlmUpstream.id == upstream_id).first()
        return to_dict(obj)

    def create_upstream(self, upstream: Dict[str, Any]) -> None:
        obj = LlmUpstream(**upstream)
        self.db.add(obj)
        self.db.commit()

    def update_upstream(self, upstream_id: str, updates: Dict[str, Any], expected_version: Optional[int] = None) -> Dict[str, Any]:
        obj = self.db.query(LlmUpstream).filter(LlmUpstream.id == upstream_id).first()
        if not obj:
            raise KeyError(f"Upstream {upstream_id} not found")
        
        if expected_version is not None and obj.version != expected_version:
             raise ValueError(f"Version mismatch: expected {expected_version}, got {obj.version}")

        for k, v in updates.items():
            if hasattr(obj, k):
                setattr(obj, k, v)
        
        obj.version += 1
        self.db.commit()
        return to_dict(obj)

    def delete_upstream(self, upstream_id: str) -> None:
        obj = self.db.query(LlmUpstream).filter(LlmUpstream.id == upstream_id).first()
        if obj:
            self.db.delete(obj)
            self.db.commit()


class PostgresModelGroupStore(ModelGroupStore):
    def __init__(self, db: Session):
        self.db = db

    def list_model_groups(self) -> List[Dict[str, Any]]:
        objs = self.db.query(ModelGroup).options(joinedload(ModelGroup.deployment_rows)).all()
        results = []
        for o in objs:
            d = to_dict(o)
            # Hydrate deployments from rows
            d['deployments'] = [
                {'upstream_id': r.upstream_id, 'model_name': r.model_name, 'weight': r.weight}
                for r in o.deployment_rows
            ]
            results.append(d)
        return results

    def get_model_group(self, group_id: str) -> Optional[Dict[str, Any]]:
        obj = self.db.query(ModelGroup).options(joinedload(ModelGroup.deployment_rows)).filter(ModelGroup.id == group_id).first()
        if not obj:
            return None
        d = to_dict(obj)
        d['deployments'] = [
            {'upstream_id': r.upstream_id, 'model_name': r.model_name, 'weight': r.weight}
            for r in obj.deployment_rows
        ]
        return d

    def create_model_group(self, group: Dict[str, Any]) -> None:
        # Separate deployments
        deployments_data = group.pop('deployments', [])
        obj = ModelGroup(**group)
        self.db.add(obj)
        self.db.flush() # Get ID if needed, though usually passed
        # Create deployment rows
        for dep in deployments_data:
            d_row = Deployment(
                id=str(uuid.uuid4()), # UUID for deployment row
                model_group_id=obj.id, 
                upstream_id=dep['upstream_id'], 
                model_name=dep['model_name'], 
                weight=dep.get('weight', 100)
            )
            self.db.add(d_row)
        self.db.commit()

    def update_model_group(self, group_id: str, updates: Dict[str, Any], expected_version: Optional[int] = None) -> Dict[str, Any]:
        obj = self.db.query(ModelGroup).filter(ModelGroup.id == group_id).first()
        if not obj:
            raise KeyError(f"ModelGroup {group_id} not found")

        if expected_version is not None and obj.version != expected_version:
             raise ValueError("Version mismatch")

        # Handle deployments update (full replace strategy)
        if 'deployments' in updates:
            deployments_data = updates.pop('deployments')
            # clear existing
            self.db.query(Deployment).filter(Deployment.model_group_id == group_id).delete()
            # add new
            for dep in deployments_data:
                d_row = Deployment(
                     id=str(uuid.uuid4()),
                     model_group_id=group_id,
                     upstream_id=dep['upstream_id'],
                     model_name=dep['model_name'],
                     weight=dep.get('weight', 100)
                )
                self.db.add(d_row)

        for k, v in updates.items():
            if hasattr(obj, k):
                setattr(obj, k, v)
        
        obj.version += 1
        self.db.commit()
        return self.get_model_group(group_id)

    def delete_model_group(self, group_id: str) -> None:
        obj = self.db.query(ModelGroup).filter(ModelGroup.id == group_id).first()
        if obj:
            self.db.delete(obj) # Cascade handles deployment_rows
            self.db.commit()


class PostgresRoutingPolicyStore(RoutingPolicyStore):
    def __init__(self, db: Session):
        self.db = db

    def list_policies(self) -> List[Dict[str, Any]]:
        # This will return ALL versions if we just query everything.
        # Typically list implies latest versions? Or all?
        # Dashboard wants "routing policies", usually unique by ID.
        # We should distinct by policy_id order by version desc.
        # MVP: Return just all rows and let frontend filter, or query distinct.
        # Postgres DISTINCT ON (policy_id) ORDER BY policy_id, version DESC
        objs = self.db.query(RoutingPolicy).distinct(RoutingPolicy.policy_id).order_by(RoutingPolicy.policy_id, desc(RoutingPolicy.version)).all()
        return [to_dict(o) for o in objs]
    
    def get_policy(self, policy_id: str, version: Optional[int] = None) -> Optional[Dict[str, Any]]:
        q = self.db.query(RoutingPolicy).filter(RoutingPolicy.policy_id == policy_id)
        if version is not None:
             q = q.filter(RoutingPolicy.version == version)
        else:
             q = q.order_by(desc(RoutingPolicy.version))
        
        obj = q.first()
        if not obj:
             return None
             
        # Map policy_id back to id if needed for compat?
        d = to_dict(obj)
        if d: d['id'] = d['policy_id']
        return d
    
    def create_policy(self, policy: Dict[str, Any]) -> None:
        # Immutable insert
        # Map id -> policy_id if needed
        if 'id' in policy and 'policy_id' not in policy:
             policy['policy_id'] = policy.pop('id')
             
        obj = RoutingPolicy(**policy)
        self.db.add(obj)
        self.db.commit()

    def delete_policy(self, policy_id: str) -> None:
        self.db.query(RoutingPolicy).filter(RoutingPolicy.policy_id == policy_id).delete(synchronize_session=False)
        self.db.commit()





class PostgresMcpStore(McpStore):
    def __init__(self, db: Session):
        self.db = db

    def list_servers(self) -> List[Dict[str, Any]]:
        objs = self.db.query(McpServer).all()
        return [to_dict(o) for o in objs]

    def get_server(self, server_id: str) -> Optional[Dict[str, Any]]:
        obj = self.db.query(McpServer).filter(McpServer.id == server_id).first()
        return to_dict(obj)

    def create_server(self, server: Dict[str, Any]) -> None:
        obj = McpServer(**server)
        self.db.add(obj)
        self.db.commit()

    def update_server(self, server_id: str, updates: Dict[str, Any], expected_version: Optional[int] = None) -> Dict[str, Any]:
        obj = self.db.query(McpServer).filter(McpServer.id == server_id).first()
        if not obj:
            raise KeyError("Server not found")
        
        if expected_version is not None and obj.version != expected_version:
             raise ValueError("Version mismatch")

        for k, v in updates.items():
            if hasattr(obj, k):
                setattr(obj, k, v)
        
        obj.version += 1
        self.db.commit()
        return to_dict(obj)

    def delete_server(self, server_id: str) -> None:
        obj = self.db.query(McpServer).filter(McpServer.id == server_id).first()
        if obj:
            self.db.delete(obj)
            self.db.commit()

    def list_policies(self, team_id: str) -> List[Dict[str, Any]]:
        objs = self.db.query(McpPolicy).filter(McpPolicy.team_id == team_id).all()
        return [to_dict(o) for o in objs]

    def upsert_policy(self, policy: Dict[str, Any]) -> None:
        obj = self.db.query(McpPolicy).filter(McpPolicy.id == policy['id']).first()
        if obj:
            for k, v in policy.items():
                if hasattr(obj, k):
                    setattr(obj, k, v)
            obj.version += 1
        else:
            obj = McpPolicy(**policy)
            self.db.add(obj)
        self.db.commit()

    def delete_policy(self, policy_id: str) -> None:
        obj = self.db.query(McpPolicy).filter(McpPolicy.id == policy_id).first()
        if obj:
            self.db.delete(obj)
            self.db.commit()

from app.domain.interfaces import UsageStore

class PostgresUsageStore(UsageStore):
    def __init__(self, db: Session):
        self.db = db

    def record_usage(self, event: Dict[str, Any]) -> None:
        usage = UsageEvent(**event)
        self.db.add(usage)
        self.db.commit()

    def get_stats(self, window_hours: int = 24) -> Dict[str, Any]:
        from sqlalchemy import func
        since = datetime.now(timezone.utc) - timedelta(hours=window_hours)
        
        res = self.db.query(
            func.count(UsageEvent.id).label("count"),
            func.sum(UsageEvent.input_tokens + UsageEvent.output_tokens).label("tokens"),
            func.sum(UsageEvent.cost_usd).label("cost"),
            func.avg(UsageEvent.latency_ms).label("latency")
        ).filter(UsageEvent.timestamp >= since).one()
        
        return {
            "requests_total": res.count or 0,
            "tokens_total": int(res.tokens or 0),
            "cost_usd": float(res.cost or 0),
            "latency_avg_ms": float(res.latency or 0),
            "window_hours": window_hours
        }


from app.domain.a2a.canonical import canonical_json_bytes
import hashlib

class PostgresAuditStore(AuditStore):
    def __init__(self, db: Session):
        self.db = db

    def append_event(self, event: Dict[str, Any]) -> None:
        # Prepare event for hashing (exclude volatile fields if needed, 
        # but Phase 5 says 'stable after schema upgrades', 
        # so we hash the canonical core fields)
        core_event = {
            "principal_id": event.get("principal_id"),
            "action": event.get("action"),
            "resource_type": event.get("resource_type"),
            "resource_id": event.get("resource_id"),
            "status": event.get("status"),
            "schema_id": event.get("schema_id"),
            "schema_version": event.get("schema_version"),
            "details": event.get("details", {})
        }
        
        # Add timestamp to hash for temporal integrity
        if "timestamp" in event:
            ts = event["timestamp"]
            if isinstance(ts, datetime):
                core_event["timestamp"] = ts.isoformat()
            else:
                 core_event["timestamp"] = str(ts)

        from app.domain.a2a.canonical import canonical_json_bytes
        canonical_bytes = canonical_json_bytes(core_event)
        event_h = hashlib.sha256(canonical_bytes).hexdigest()
        
        event["event_hash"] = event_h
        
        obj = AuditEvent(**event)
        self.db.add(obj)
        self.db.commit()

    def list_events(self, filters: Dict[str, Any], limit: int = 100) -> List[Dict[str, Any]]:
        q = self.db.query(AuditEvent)
        if 'resource_type' in filters:
            q = q.filter(AuditEvent.resource_type == filters['resource_type'])
        if 'resource_id' in filters:
            q = q.filter(AuditEvent.resource_id == filters['resource_id'])
        
        objs = q.order_by(desc(AuditEvent.timestamp)).limit(limit).all()
        return [to_dict(o) for o in objs]


class PostgresPrincipalStore(PrincipalStore):
    def __init__(self, db: Session):
        self.db = db

    def get_principal(self, principal_id: str) -> Optional[Dict[str, Any]]:
        obj = self.db.query(Principal).filter(Principal.id == principal_id).first()
        return to_dict(obj)
