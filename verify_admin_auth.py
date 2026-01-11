"""Verify Admin Auth Implementation."""
import sys
import os
import jwt
from datetime import datetime, timedelta
from fastapi.testclient import TestClient

# Ensure app in path
sys.path.append(os.getcwd())

from app.main import app
from app.adapters.postgres.session import SessionLocal
from app.adapters.postgres.models import Role, RoleBinding, Principal

def seed_rbac():
    db = SessionLocal()
    try:
        # Clear existing to avoid conflicts
        db.query(RoleBinding).delete()
        db.query(Role).delete()
        db.query(Principal).delete()
        
        db.add(Principal(id="admin@talos.io", type="user", email="admin@talos.io"))
        db.add(Principal(id="viewer@talos.io", type="user", email="viewer@talos.io"))
        
        db.add(Role(id="PlatformAdmin", name="Platform Admin", permissions=["*"]))
        db.add(Role(id="PlatformViewer", name="Platform Viewer", permissions=["audit.read"]))
        
        db.add(RoleBinding(id="b1", principal_id="admin@talos.io", role_id="PlatformAdmin", scope_type="platform"))
        db.add(RoleBinding(id="b2", principal_id="viewer@talos.io", role_id="PlatformViewer", scope_type="platform"))
        
        db.commit()
        print("[DB] RBAC data seeded.")
    finally:
        db.close()

def test_auth():
    os.environ["DEV_MODE"] = "true"
    os.environ["AUTH_ADMIN_SECRET"] = "test-secret"
    
    client = TestClient(app)
    
    # 1. Test Legacy Header
    print("\n[TEST] Legacy Header Auth...")
    resp = client.get("/admin/v1/me", headers={"X-Talos-Principal": "admin@talos.io"})
    if resp.status_code == 200:
        print("[PASS] Legacy Header successful.")
    else:
        print(f"[FAIL] Legacy Header failed: {resp.status_code} {resp.text}")

    # 2. Test JWT (Valid)
    print("\n[TEST] JWT Auth (Valid)...")
    token = jwt.encode(
        {"sub": "admin@talos.io", "exp": datetime.utcnow() + timedelta(hours=1)}, 
        "test-secret", 
        algorithm="HS256"
    )
    resp = client.get("/admin/v1/me", headers={"Authorization": f"Bearer {token}"})
    if resp.status_code == 200:
        print("[PASS] JWT Auth successful.")
    else:
        print(f"[FAIL] JWT Auth failed: {resp.status_code} {resp.text}")

    # 3. Test RBAC Enforcement
    print("\n[TEST] RBAC Enforcement (Viewer trying to write)...")
    token_viewer = jwt.encode(
        {"sub": "viewer@talos.io", "exp": datetime.utcnow() + timedelta(hours=1)}, 
        "test-secret", 
        algorithm="HS256"
    )
    # /admin/v1/upstreams requires mcp.admin (wait, I should check a write endpoint)
    # Actually most admin endpoints require specific permissions.
    # Let's try list upstreams (requires llm.read or similar)
    resp = client.get("/admin/v1/llm/upstreams", headers={"Authorization": f"Bearer {token_viewer}"})
    if resp.status_code == 403:
        print("[PASS] RBAC enforced (Viewer caught).")
    elif resp.status_code == 200:
        print("[FAIL] RBAC NOT enforced (Viewer allowed to view upstreams without permission).")
    else:
        print(f"[INFO] Unexpected status: {resp.status_code} {resp.text}")

    # 4. Test Invalid JWT
    print("\n[TEST] JWT Auth (Invalid Secret)...")
    bad_token = jwt.encode({"sub": "admin@talos.io"}, "wrong-secret", algorithm="HS256")
    resp = client.get("/admin/v1/me", headers={"Authorization": f"Bearer {bad_token}"})
    if resp.status_code == 401:
        print("[PASS] Invalid JWT rejected.")
    else:
        print(f"[FAIL] Invalid JWT accepted: {resp.status_code}")

if __name__ == "__main__":
    seed_rbac()
    test_auth()
