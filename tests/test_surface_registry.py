import pytest
import json
from unittest.mock import Mock, MagicMock
from pathlib import Path

from fastapi.routing import APIRoute

from app.domain.rbac.registry import RBACSurfaceRegistry
from app.domain.registry import SurfaceRegistry

@pytest.fixture
def mock_inventory(tmp_path):
    path = tmp_path / "inventory.json"
    data = {
        "version": "1.0",
        "items": [
            {
                "id": "test.op",
                "type": "http",
                "http_match": { "method": "GET", "path_template": "/test" },
                "required_scopes": ["test.scope"],
                "attestation_required": True,
                "audit_action": "test",
                "data_classification": "public",
                "audit_meta_allowlist": ["request_id", "actor_id"]
            }
        ]
    }
    with open(path, "w") as f:
        json.dump(data, f)
    return str(path)

@pytest.fixture
def rpc_inventory(tmp_path):
    path = tmp_path / "rpc_inventory.json"
    data = {
        "version": "1.0",
        "items": [
            {
                "id": "a2a.v1.rpc",
                "type": "http",
                "http_match": {"method": "POST", "path_template": "/rpc"},
                "attestation_required": True,
                "audit_action": "a2a.rpc.invoke",
                "data_classification": "sensitive",
                "audit_meta_allowlist": ["method", "id"],
                "rpc_methods": {
                    "SendMessage": {
                        "required_scopes": ["a2a.send"],
                        "audit_action": "a2a.send",
                        "aliases": ["message/send"],
                    },
                    "GetTask": {
                        "required_scopes": ["a2a.get"],
                        "audit_action": "a2a.get",
                        "aliases": ["tasks/get"],
                    },
                },
            }
        ],
    }
    with open(path, "w") as f:
        json.dump(data, f)
    return str(path)

def test_registry_loading(mock_inventory):
    reg = SurfaceRegistry(mock_inventory)
    item = reg.match_request("GET", "/test")
    assert item is not None
    assert item.id == "test.op"
    assert item.required_scopes == ["test.scope"]
    
    assert reg.match_request("POST", "/test") is None
    assert reg.match_request("GET", "/other") is None

def test_startup_gate_pass(mock_inventory):
    reg = SurfaceRegistry(mock_inventory)
    app = Mock()
    route = MagicMock()
    route.methods = {"GET"}
    route.path = "/test"
    # Identify as APIRoute (duck typing check in verify_app_routes uses isinstance)
    # We need to mock isinstance(route, APIRoute) to True?
    # verify_app_routes imports APIRoute.
    # Let's just create real APIRoute objects if possible or mock the class check?
    
    # Better: Use FastAPI app
    from fastapi import FastAPI
    app = FastAPI()
    @app.get("/test")
    def handler(): pass
    
    # Passes because /test is in inventory
    reg.verify_app_routes(app)

def test_startup_gate_fail(mock_inventory):
    reg = SurfaceRegistry(mock_inventory)
    from fastapi import FastAPI
    app = FastAPI()
    @app.get("/test")
    def handler(): pass
    
    @app.post("/unmapped")
    def unmapped_handler(): pass
    
    with pytest.raises(RuntimeError) as exc:
        reg.verify_app_routes(app)
    
    assert "Security Surface Gap" in str(exc.value)
    assert "POST:/unmapped" in str(exc.value)

def test_startup_gate_ignore_docs(mock_inventory):
    reg = SurfaceRegistry(mock_inventory)
    from fastapi import FastAPI
    app = FastAPI()
    @app.get("/test")
    def handler(): pass
    
    # Standard docs routes are ignored
    # We can't easily add them to app.routes manually without mounting docs?
    # Actually FastAPI does it by default.
    # verify_app_routes checks route.path string.
    # Let's mock a route object for docs.
    from fastapi.routing import APIRoute
    route_docs = APIRoute("/docs", lambda: None, methods=["GET"])
    
    app.routes.append(route_docs)

    reg.verify_app_routes(app) # Should pass


def test_registry_matches_rpc_methods_and_aliases(rpc_inventory):
    reg = SurfaceRegistry(rpc_inventory)

    send_item = reg.match_request("POST", "/rpc", rpc_method="SendMessage")
    assert send_item is not None
    assert send_item.required_scopes == ["a2a.send"]
    assert send_item.attestation_required is True

    alias_item = reg.match_request("POST", "/rpc", rpc_method="message/send")
    assert alias_item is not None
    assert alias_item.required_scopes == ["a2a.send"]
    assert alias_item.audit_action == "a2a.send"


def test_registry_falls_back_to_base_rpc_surface_for_unknown_method(rpc_inventory):
    reg = SurfaceRegistry(rpc_inventory)

    item = reg.match_request("POST", "/rpc", rpc_method="UnknownMethod")
    assert item is not None
    assert item.id == "a2a.v1.rpc"
    assert item.required_scopes == []
    assert item.path_template == "/rpc"


def test_contract_surface_inventory_contains_a2a_v1_auth_surfaces():
    inventory_path = Path(__file__).resolve().parents[3] / "contracts" / "inventory" / "gateway_surface.json"
    with open(inventory_path) as f:
        data = json.load(f)

    route_index = {
        (item["http_match"]["method"], item["http_match"]["path_template"]): item
        for item in data["items"]
        if item.get("type") == "http"
    }

    assert ("POST", "/rpc") in route_index
    assert "rpc_methods" in route_index[("POST", "/rpc")]
    assert route_index[("POST", "/rpc")]["rpc_methods"]["SendMessage"]["required_scopes"] == ["a2a.send"]
    assert ("POST", "/a2a/v1/") in route_index
    assert route_index[("POST", "/a2a/v1/")]["required_scopes"] == ["a2a.invoke"]
    assert ("POST", "/v1/chat/completions") in route_index
    assert ("GET", "/v1/models") in route_index
    assert ("POST", "/v1/mcp/servers/{server_id}/tools/{tool_name}:call") in route_index


def test_rbac_registry_accepts_contract_inventory_subset():
    from app.main import app

    inventory_path = Path(__file__).resolve().parents[3] / "contracts" / "inventory" / "gateway_surface.json"
    registry = RBACSurfaceRegistry(str(inventory_path))

    registry.verify_app_routes(app)

    registry_routes = {
        f"{route.method}:{route.path_template}"
        for route in registry.get_routes()
    }
    app_routes = set()
    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue
        if route.path in {"/docs", "/redoc", "/openapi.json"}:
            continue
        for method in route.methods:
            if method == "OPTIONS":
                continue
            app_routes.add(f"{method}:{route.path}")

    assert registry_routes <= app_routes
    assert "POST:/rpc" in registry_routes
    assert "POST:/a2a/v1/" in registry_routes
