import pytest
import json
from unittest.mock import Mock, MagicMock
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
                "data_classification": "public"
            }
        ]
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
