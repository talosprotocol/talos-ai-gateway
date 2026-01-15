import json
import logging
from typing import Dict, Optional, List
from dataclasses import dataclass

from fastapi import FastAPI
from fastapi.routing import APIRoute

logger = logging.getLogger(__name__)

from pydantic import BaseModel, Field

class SurfaceItem(BaseModel):
    """Security Surface Item (Pydantic)."""
    id: str  # Opcode
    type: str
    required_scopes: List[str]
    attestation_required: bool
    audit_action: str
    data_classification: str
    audit_meta_allowlist: List[str]
    path_template: Optional[str] = None

class SurfaceRegistry:
    def __init__(self, inventory_path: str):
        self._items: Dict[str, SurfaceItem] = {} # Map "METHOD:path_template" -> Item
        self._load_inventory(inventory_path)

    def _load_inventory(self, path: str):
        with open(path, 'r') as f:
            data = json.load(f)
        
        for item in data.get('items', []):
            if item['type'] == 'http':
                match = item['http_match']
                method = match['method']
                tmpl = match['path_template']
                key = f"{method}:{tmpl}"
                
                allowlist = item.get('audit_meta_allowlist')
                if allowlist is None:
                    raise RuntimeError(f"Surface {item['id']} missing 'audit_meta_allowlist'")
                
                # Check for wildcards/globs
                for entry in allowlist:
                    if '*' in entry or '?' in entry or '[' in entry or '{' in entry:
                        raise RuntimeError(f"Surface {item['id']} contains forbidden wildcard in allowlist: {entry}")

                self._items[key] = SurfaceItem(
                    id=item['id'],
                    type=item['type'],
                    required_scopes=item['required_scopes'],
                    attestation_required=item['attestation_required'],
                    audit_action=item['audit_action'],
                    data_classification=item['data_classification'],
                    audit_meta_allowlist=allowlist,
                    path_template=tmpl
                )
        logger.info(f"Loaded {len(self._items)} surface items from inventory.")

    def match_request(self, method: str, path_template: str) -> Optional[SurfaceItem]:
        """Match request method and route template to inventory item."""
        key = f"{method}:{path_template}"
        return self._items.get(key)

    def verify_app_routes(self, app: FastAPI):
        """Strict Completeness Gate: Fail if app has routes not in inventory."""
        missing = []
        for route in app.routes:
            if isinstance(route, APIRoute):
                # Ignore doc/openapi routes? Or assume they should be classified?
                # Usually we ignore /docs, /redoc, /openapi.json
                if route.path in ["/docs", "/redoc", "/openapi.json"]:
                    continue
                
                # Check for OPTIONS method or HEAD which might be auto-generated?
                # FastAPI routes have 'methods'. It's a set.
                for method in route.methods:
                    if method == "OPTIONS": continue # Skip CORS preflight checks check?
                    
                    key = f"{method}:{route.path}"
                    if key not in self._items:
                        missing.append(key)
        
        if missing:
            msg = f"Security Surface Gap: {len(missing)} routes are defined in FastAPI but missing from Surface Inventory!\nMissing: {missing}"
            logger.critical(msg)
            raise RuntimeError(msg)
        else:
            logger.info("Surface Completeness Check Passed: All routes are mapped.")

# Singleton instance placeholder? 
# In dependencies we will instantiate it.
