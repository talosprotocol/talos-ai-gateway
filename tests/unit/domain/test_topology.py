import os
import pytest
from app.domain.topology import GatewayTopology, RegionInfo

def test_topology_load_from_env(monkeypatch):
    monkeypatch.setenv("MCP_SERVER_US", "http://us-gateway")
    monkeypatch.setenv("MCP_SERVER_EU", "http://eu-gateway")
    
    topology = GatewayTopology(current_region="us")
    
    assert len(topology.list_regions()) == 2
    assert topology.get_region("us").endpoint == "http://us-gateway"
    assert topology.get_region("eu").endpoint == "http://eu-gateway"

def test_get_closest_region(monkeypatch):
    monkeypatch.setenv("MCP_SERVER_US", "http://us-gateway")
    monkeypatch.setenv("MCP_SERVER_EU", "http://eu-gateway")
    
    topology = GatewayTopology(current_region="eu")
    
    # Defaults to current region
    assert topology.get_closest_region().id == "eu"
    
    # Respects hint
    assert topology.get_closest_region(client_hint="us").id == "us"
    
    # Fallback for unknown hint
    assert topology.get_closest_region(client_hint="asia").id == "eu"
