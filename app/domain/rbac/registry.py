import json
import logging
import os
from typing import List, Optional
from .models import SurfaceRoute, Scope, ScopeType

logger = logging.getLogger(__name__)

class RBACSurfaceRegistry:
    def __init__(self, inventory_path: str):
        self._routes: List[SurfaceRoute] = []
        self._load_inventory(inventory_path)

    def _load_inventory(self, path: str):
        if not os.path.exists(path):
            logger.warning(f"Surface Registry manifest not found at {path}. RBAC will deny all non-public routes.")
            return

        try:
            with open(path, 'r') as f:
                data = json.load(f)
            
            for item in data.get('routes', []):
                # Parse Scope Template
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
            
            logger.info(f"Loaded {len(self._routes)} RBAC surface routes from {path}")
            
        except Exception as e:
            logger.error(f"Failed to load Surface Registry from {path}: {e}")
            raise RuntimeError(f"Surface Registry Load Failed: {e}")

    def get_routes(self) -> List[SurfaceRoute]:
        return self._routes
