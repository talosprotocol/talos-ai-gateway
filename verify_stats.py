"""Verify Dashboard Stats Implementation."""
import os
import requests

BASE_URL = os.getenv("TALOS_GATEWAY_URL", "http://localhost:8000").rstrip("/")
ADMIN_URL = f"{BASE_URL}/admin/v1"
AUTH_ADMIN_SECRET = os.getenv("AUTH_ADMIN_SECRET", "dev-admin-secret")
AUTH_ADMIN_PRINCIPAL = os.getenv("AUTH_ADMIN_PRINCIPAL", "dev-admin")
DATA_PLANE_TOKEN = os.getenv("TALOS_API_TOKEN", "test-key-hard")


def session_headers(permissions, *, data_plane=False):
    payload = {
        "principal": AUTH_ADMIN_PRINCIPAL,
        "permissions": permissions,
        "ttl_seconds": 3600,
    }
    if data_plane:
        payload["data_plane_token"] = DATA_PLANE_TOKEN

    resp = requests.post(
        f"{ADMIN_URL}/auth/token",
        headers={
            "Content-Type": "application/json",
            "X-Talos-Admin-Secret": AUTH_ADMIN_SECRET,
        },
        json=payload,
        timeout=10,
    )
    resp.raise_for_status()
    return {"Authorization": f"Bearer {resp.json()['token']}"}

def test_stats():
    # 1. Trigger Usage
    print("\n[TEST] Triggering usage via Public API...")
    resp = requests.post(
        f"{BASE_URL}/v1/chat/completions",
        headers=session_headers(["llm.invoke"], data_plane=True),
        json={
            "model": "gpt-4-turbo",
            "messages": [{"role": "user", "content": "Hello"}]
        },
        timeout=30,
    )
    if resp.status_code == 200:
        print("[PASS] Chat completion successful.")
    else:
        print(f"[FAIL] Chat completion failed: {resp.status_code} {resp.text}")

    # 2. Get Stats
    print("\n[TEST] Fetching telemetry stats...")
    resp = requests.get(
        f"{ADMIN_URL}/telemetry/stats",
        headers=session_headers(["audit.read"]),
        timeout=10,
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
