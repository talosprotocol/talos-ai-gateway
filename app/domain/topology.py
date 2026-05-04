import os
import logging
from typing import Dict, List, Optional
from pydantic import BaseModel

logger = logging.getLogger(__name__)

class RegionInfo(BaseModel):
    id: str
    endpoint: str
    status: str = "active"

class GatewayTopology:
    """
    Manages regional gateway endpoints and discovery.
    """
    
    def __init__(self, current_region: str):
        self.current_region = current_region
        self._regions: Dict[str, RegionInfo] = {}
        self._load_from_env()

    def _load_from_env(self):
        """Load regional endpoints from environment variables."""
        regions = ["US", "EU", "ASIA"]
        for r in regions:
            env_var = f"MCP_SERVER_{r}"
            endpoint = os.getenv(env_var)
            if endpoint:
                self._regions[r.lower()] = RegionInfo(id=r.lower(), endpoint=endpoint)
        
        logger.info(f"Loaded topology with {len(self._regions)} regions")

    def get_region(self, region_id: str) -> Optional[RegionInfo]:
        return self._regions.get(region_id.lower())

    def list_regions(self) -> List[RegionInfo]:
        return list(self._regions.values())

    def get_closest_region(self, client_hint: Optional[str] = None) -> RegionInfo:
        """
        Returns the closest region based on client hint or current region.
        In a real implementation, this would use latency or GeoIP.
        """
        if client_hint and client_hint.lower() in self._regions:
            return self._regions[client_hint.lower()]
        
        return self._regions.get(self.current_region.lower()) or self.list_regions()[0]

# Singleton instance
_topology_instance: Optional[GatewayTopology] = None

def get_topology() -> GatewayTopology:
    global _topology_instance
    if _topology_instance is None:
        from app.settings import settings
        _topology_instance = GatewayTopology(settings.talos_region)
    return _topology_instance
