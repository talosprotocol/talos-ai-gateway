import sys
import os
import pytest
from fastapi.testclient import TestClient
from starlette.middleware.base import BaseHTTPMiddleware

# Ensure we can import app
sys.path.append(os.getcwd())

from app.main import app

# Mock Auth Middleware to inject identity
class MockAuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        user_id = request.headers.get("X-Mock-User", "anonymous")
        request.state.user_id = user_id
        response = await call_next(request)
        return response

# Insert Mock Auth before RBAC (which is usually last added, so first executed? No, middlewares are LIFO or FIFO?)
# Starlette adds middleware in reverse order of execution (onion skin).
# So the last added middleware runs FIRST.
# In main.py:
# app.add_middleware(RegionHeaderMiddleware) (runs first)
# app.add_middleware(RBACMiddleware) (runs 2nd)
# app.add_middleware(ShutdownGateMiddleware) (runs 3rd)
# ...
# So to run BEFORE RBAC (i.e. wrap it), we should add it LAST.
app.add_middleware(MockAuthMiddleware)

app.add_middleware(MockAuthMiddleware)

def run_tests():
    with TestClient(app) as client:
        print("\n--- Testing Public Route (/health/live) ---")
        resp = client.get("/health/live")
        print(f"Status: {resp.status_code}")
        assert resp.status_code == 200

        print("\n--- Testing Unmapped Route (/v1/foobar) ---")
        resp = client.get("/v1/foobar")
        print(f"Status: {resp.status_code} Body: {resp.json()}")
        assert resp.status_code == 403
        assert resp.json()["code"] == "RBAC_SURFACE_UNMAPPED_DENIED"

        print("\n--- Testing Authorized Access (/v1/secrets as dev-user) ---")
        # Route: POST /v1/secrets (secrets:write)
        resp = client.post("/v1/secrets", headers={"X-Mock-User": "dev-user"})
        print(f"Status: {resp.status_code}")
        # Expecting anything NOT 403/401. 404 is fine (no router).
        assert resp.status_code != 403, f"Should be allowed, but got {resp.json()}"
        assert resp.status_code != 401
    
        print("\n--- Testing Unauthorized Access (/v1/secrets as anonymous) ---")
        # anonymous has role-public (system:health, system:metrics)
        # Target: secrets:write
        resp = client.post("/v1/secrets", headers={"X-Mock-User": "anonymous"})
        print(f"Status: {resp.status_code} Body: {resp.json()}")
        assert resp.status_code == 401 # Anonymous access to protected route returns 401 in middleware
        # Wait, my middleware returns 401 if anonymous?
        # Yes: if principal_id == "anonymous": return 401
        assert resp.json()["code"] == "UNAUTHORIZED"
        
        print("\n--- Testing Scope Logic (/v1/secrets/{id}) ---")
        # /v1/secrets/{secret_id} requires secrets:read scope:secret
        # dev-user has Global scope
        resp = client.get("/v1/secrets/test-secret", headers={"X-Mock-User": "dev-user"})
        print(f"Status: {resp.status_code}")
        assert resp.status_code != 403

if __name__ == "__main__":
    try:
        run_tests()

    except AssertionError as e:
        print(f"\n❌ Verification Failed: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ Error: {e}")
        sys.exit(1)
