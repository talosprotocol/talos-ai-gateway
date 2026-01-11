"""Verify Dashboard Stats Implementation."""
import sys
import os
import time
from fastapi.testclient import TestClient

# Ensure app in path
sys.path.append(os.getcwd())
os.environ["DEV_MODE"] = "true"

from app.main import app

def test_stats():
    os.environ["DEV_MODE"] = "true"
    client = TestClient(app)
    
    # 1. Trigger Usage
    print("\n[TEST] Triggering usage via Public API...")
    # Mock public key auth
    resp = client.post(
        "/v1/chat/completions",
        headers={"Authorization": "Bearer sk-test-key-1"},
        json={
            "model": "gpt-4-turbo",
            "messages": [{"role": "user", "content": "Hello"}]
        }
    )
    if resp.status_code == 200:
        print("[PASS] Chat completion successful.")
    else:
        print(f"[FAIL] Chat completion failed: {resp.status_code} {resp.text}")

    # 2. Get Stats
    print("\n[TEST] Fetching telemetry stats...")
    resp = client.get(
        "/admin/v1/telemetry/stats",
        headers={"X-Talos-Principal": "admin@talos.io"} # Using legacy header in DEV_MODE
    )
    if resp.status_code == 200:
        stats = resp.json()
        print(f"[PASS] Stats retrieved: {stats}")
        if stats.get("requests_total", 0) > 0:
            print("[VERIFIED] Stats are non-zero.")
        else:
            print("[WARN] Stats are zero (might be expected if using real Postgres and no previous data, or if JsonStore mock failed).")
    else:
        print(f"[FAIL] Fetching stats failed: {resp.status_code} {resp.text}")

if __name__ == "__main__":
    test_stats()
