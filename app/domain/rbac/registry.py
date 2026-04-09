import json
import logging
import os
from typing import Any, Dict, List, Optional

from fastapi import FastAPI
from fastapi.routing import APIRoute

from .models import SurfaceRoute, Scope, ScopeType

logger = logging.getLogger(__name__)

class RBACSurfaceRegistry:
    def __init__(self, inventory_path: str):
        self._routes: List[SurfaceRoute] = []
        self._exhaustive = False
        self._load_inventory(inventory_path)

    def _load_inventory(self, path: str):
        if not os.path.exists(path):
            logger.warning(f"Surface Registry manifest not found at {path}. RBAC will deny all non-public routes.")
            return

        try:
            with open(path, 'r') as f:
                data = json.load(f)

            if "items" in data:
                self._load_contract_inventory(data.get("items", []))
            else:
                self._load_route_inventory(data.get("routes", []))
            
            logger.info(f"Loaded {len(self._routes)} RBAC surface routes from {path}")
            
        except Exception as e:
            logger.error(f"Failed to load Surface Registry from {path}: {e}")
            raise RuntimeError(f"Surface Registry Load Failed: {e}")

    def _load_contract_inventory(self, items: List[Dict[str, Any]]) -> None:
        self._exhaustive = False
        for item in items:
            if item.get("type") != "http":
                continue

            match = item.get("http_match", {})
            method = match.get("method")
            path_template = match.get("path_template")
            if not isinstance(method, str) or not isinstance(path_template, str):
                raise RuntimeError(f"Contract surface item missing http_match method/path_template: {item.get('id', '<unknown>')}")

            scopes = [scope for scope in item.get("required_scopes", []) if isinstance(scope, str)]
            permission = scopes[0] if scopes else str(item.get("id", f"{method}:{path_template}"))

            self._routes.append(
                SurfaceRoute(
                    method=method,
                    path_template=path_template,
                    permission=permission,
                    scope_template=Scope(scope_type=ScopeType.GLOBAL, attributes={}),
                    public=bool(item.get("public", False)),
                )
            )

    def _load_route_inventory(self, routes: List[Dict[str, Any]]) -> None:
        self._exhaustive = True
        for item in routes:
            st_data = item.get('scope_template', {})
            scope_template = Scope(
                scope_type=st_data.get('scope_type', ScopeType.GLOBAL),
                attributes=st_data.get('attributes', {})
            )

            route = SurfaceRoute(
                method=item['method'],
                path_template=item['path_template'],
                permission=item['permission'],
                scope_template=scope_template,
                public=item.get('public', False)
            )
            self._routes.append(route)

    def get_routes(self) -> List[SurfaceRoute]:
        return self._routes

    def verify_app_routes(self, app: FastAPI) -> None:
        """Validate the registry against the mounted FastAPI routes."""
        known_routes = {
            f"{route.method}:{route.path_template}"
            for route in self._routes
        }
        app_routes = set()

        for route in app.routes:
            if not isinstance(route, APIRoute):
                continue
            if route.path in ["/docs", "/redoc", "/openapi.json"]:
                continue

            for method in route.methods:
                if method == "OPTIONS":
                    continue
                app_routes.add(f"{method}:{route.path}")

        if self._exhaustive:
            missing = sorted(app_routes - known_routes)
            if not missing:
                logger.info("RBAC surface completeness check passed.")
                return
            msg = (
                "Security Surface Gap: "
                f"{len(missing)} routes are defined in FastAPI but missing from the RBAC surface registry.\n"
                f"Missing: {missing}"
            )
            logger.critical(msg)
            raise RuntimeError(msg)

        missing_registry_routes = sorted(known_routes - app_routes)
        if missing_registry_routes:
            msg = (
                "Security Surface Drift: "
                f"{len(missing_registry_routes)} registry routes do not exist in FastAPI.\n"
                f"Missing in app: {missing_registry_routes}"
            )
            logger.critical(msg)
            raise RuntimeError(msg)

        logger.info("RBAC surface registry routes all exist in the current app.")
