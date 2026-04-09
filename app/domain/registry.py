import json
import logging
from typing import Any, Dict, List, Optional

from fastapi import FastAPI
from fastapi.routing import APIRoute

logger = logging.getLogger(__name__)

from pydantic import BaseModel, Field

class SurfaceItem(BaseModel):
    """Security Surface Item (Pydantic)."""
    id: str  # Opcode
    type: str = "http"
    required_scopes: List[str] = Field(default_factory=list)
    attestation_required: bool = False
    audit_action: str = "gateway.request"
    data_classification: str = "public"
    audit_meta_allowlist: List[str] = Field(default_factory=list)
    path_template: Optional[str] = None
    public: bool = False

class SurfaceRegistry:
    def __init__(self, inventory_path: str):
        self._items: Dict[str, SurfaceItem] = {}  # Map "METHOD:path_template[:rpc_method]" -> Item
        self._load_inventory(inventory_path)

    def _load_inventory(self, path: str) -> None:
        with open(path, 'r') as f:
            data = json.load(f)

        if "items" in data:
            self._load_contract_inventory(data.get("items", []))
        elif "routes" in data:
            self._load_route_inventory(data.get("routes", []))
        else:
            raise RuntimeError(f"Unsupported surface inventory format: {path}")

        logger.info(f"Loaded {len(self._items)} surface items from inventory.")

    def _load_contract_inventory(self, items: List[Dict[str, Any]]) -> None:
        for item in items:
            if item.get("type") != "http":
                continue

            match = item.get("http_match", {})
            method = match.get("method")
            tmpl = match.get("path_template")
            if not isinstance(method, str) or not isinstance(tmpl, str):
                raise RuntimeError(f"Invalid http_match for surface {item.get('id', '<unknown>')}")

            base_item = SurfaceItem(
                id=str(item["id"]),
                type="http",
                required_scopes=self._string_list(item.get("required_scopes")),
                attestation_required=bool(item.get("attestation_required", False)),
                audit_action=str(item.get("audit_action", "gateway.request")),
                data_classification=str(item.get("data_classification", "public")),
                audit_meta_allowlist=self._validated_allowlist(item, item.get("audit_meta_allowlist")),
                path_template=tmpl,
                public=bool(item.get("public", False)),
            )
            self._register_item(method, tmpl, base_item)
            self._register_rpc_methods(method, tmpl, base_item, item.get("rpc_methods"))

    def _load_route_inventory(self, routes: List[Dict[str, Any]]) -> None:
        for route in routes:
            method = route.get("method")
            tmpl = route.get("path_template")
            if not isinstance(method, str) or not isinstance(tmpl, str):
                raise RuntimeError("Route inventory entry missing method/path_template")

            permission = route.get("permission")
            required_scopes = self._string_list(route.get("required_scopes"))
            if not required_scopes and isinstance(permission, str):
                required_scopes = [permission]

            base_item = SurfaceItem(
                id=str(route.get("id") or f"{method}:{tmpl}"),
                type=str(route.get("type", "http")),
                required_scopes=required_scopes,
                attestation_required=bool(route.get("attestation_required", False)),
                audit_action=str(route.get("audit_action", permission or "gateway.request")),
                data_classification=str(route.get("data_classification", "public")),
                audit_meta_allowlist=self._validated_allowlist(route, route.get("audit_meta_allowlist", [])),
                path_template=tmpl,
                public=bool(route.get("public", False)),
            )
            self._register_item(method, tmpl, base_item)
            self._register_rpc_methods(method, tmpl, base_item, route.get("rpc_methods"))

    def _register_rpc_methods(
        self,
        method: str,
        path_template: str,
        base_item: SurfaceItem,
        rpc_methods: Any,
    ) -> None:
        if not isinstance(rpc_methods, dict):
            return

        for rpc_method, config in rpc_methods.items():
            if not isinstance(rpc_method, str) or not isinstance(config, dict):
                continue

            aliases = [rpc_method, *self._string_list(config.get("aliases"))]
            rpc_item = base_item.model_copy(
                update={
                    "required_scopes": self._string_list(config.get("required_scopes")) or list(base_item.required_scopes),
                    "audit_action": str(config.get("audit_action", base_item.audit_action)),
                }
            )
            for alias in aliases:
                self._register_item(method, path_template, rpc_item, rpc_method=alias)

    def _register_item(
        self,
        method: str,
        path_template: str,
        item: SurfaceItem,
        rpc_method: Optional[str] = None,
    ) -> None:
        key = self._make_key(method, path_template, rpc_method)
        self._items[key] = item

    def _make_key(
        self,
        method: str,
        path_template: str,
        rpc_method: Optional[str] = None,
    ) -> str:
        key = f"{method}:{path_template}"
        if rpc_method is not None:
            key = f"{key}:{rpc_method}"
        return key

    def _validated_allowlist(self, item: Dict[str, Any], allowlist_value: Any) -> List[str]:
        allowlist = self._string_list(allowlist_value)
        for entry in allowlist:
            if '*' in entry or '?' in entry or '[' in entry or '{' in entry:
                raise RuntimeError(f"Surface {item.get('id', '<unknown>')} contains forbidden wildcard in allowlist: {entry}")
        return allowlist

    def _string_list(self, values: Any) -> List[str]:
        if not isinstance(values, list):
            return []
        return [value for value in values if isinstance(value, str)]

    def match_request(
        self,
        method: str,
        path_template: str,
        rpc_method: Optional[str] = None,
    ) -> Optional[SurfaceItem]:
        """Match request method and route template to inventory item."""
        if rpc_method is not None:
            item = self._items.get(self._make_key(method, path_template, rpc_method))
            if item is not None:
                return item
        return self._items.get(self._make_key(method, path_template))

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
