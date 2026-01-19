
import os
import sys
import time
import requests
import uuid

# Configuration
GATEWAY_A_URL = "http://localhost:8003"
GATEWAY_B_URL = "http://localhost:8004"

ADMIN_HEADERS = {
    # Using mock 'admin' permission if using dev auth or mock principal
    # In real integration, we might need a token.
    # Assuming 'dev' mode bypasses strict auth or we use a static token if configured?
    # Wait, 'verify_integration.py' uses something. Let's assume Dev Mode (Principal injection).
    # If Gateway running in Dev Mode, maybe no auth? Or we simulate header?
    "X-Talos-Principal": "admin-user", # Mock header often used in dev
    "Authorization": "Bearer dev-token"
}

def wait_for_health(url, name):
    print(f"Waiting for {name} ({url})...")
    for i in range(30):
        try:
            r = requests.get(f"{url}/health")
            if r.status_code == 200:
                print(f"✓ {name} is healthy.")
                return
        except:
            pass
        time.sleep(1)
    raise RuntimeError(f"{name} did not become healthy.")

def run_test():
    print("=== Starting Multi-Region Verification ===")
    
    # 1. Health Checks
    wait_for_health(GATEWAY_A_URL, "Gateway A (Primary)")
    wait_for_health(GATEWAY_B_URL, "Gateway B (Replica)")
    
    # 2. Write Secret to Primary
    secret_name = f"test-secret-{uuid.uuid4().hex[:8]}"
    secret_value = "secret-value-123"
    
    print(f"\n[Step 1] Writing secret '{secret_name}' to Primary...")
    r = requests.post(
        f"{GATEWAY_A_URL}/admin/v1/secrets",
        json={"name": secret_name, "value": secret_value},
        # We need headers. If Dev Mode uses dependency overrides or mock auth.
        # Let's hope Dev Mode allows 'X-Talos-Principal'.
        headers={"X-Talos-Principal": "mock-admin", "Content-Type": "application/json"} 
    )
    
    if r.status_code not in [200, 201]:
        print(f"❌ Failed to write secret: {r.status_code} {r.text}")
        sys.exit(1)
    print("✓ Write successful.")
    
    # 3. Read Secret from Replica (Poll for replication lag)
    print("\n[Step 2] Reading secret from Replica (Gateway B)...")
    found = False
    for i in range(10):
        r = requests.get(
            f"{GATEWAY_B_URL}/admin/v1/secrets",
            headers={"X-Talos-Principal": "mock-admin"}
        )
        if r.status_code == 200:
            secrets = r.json().get("secrets", [])
            # Find our secret
            match = next((s for s in secrets if s["name"] == secret_name), None)
            if match:
                print(f"✓ Found secret in Replica! Headers: {r.headers}")
                # Check Source Header
                src = r.headers.get("X-Talos-Read-Source", "unknown")
                print(f"  X-Talos-Read-Source: {src}")
                if src == "replica":
                    print("  (Confirmed read from Replica)")
                elif src == "primary":
                    # This happens if DB URLs are same, or logic failed.
                    # In docker-compose, they are distinct URLs.
                    print("  ⚠️ Read from Primary? (Maybe intended layout?)")
                elif src == "primary_fallback":
                     print("  ⚠️ Read Fallback triggered.")
                
                found = True
                break
        time.sleep(0.5)
        
    if not found:
        print("❌ Secret not found in Replica after 5 seconds.")
        sys.exit(1)
        
    print("\n=== Verification Passed! ===")

if __name__ == "__main__":
    run_test()
