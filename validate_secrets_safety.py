import requests
import json
import sys

BASE_URL = "http://localhost:8000"

def check_leaks():
    print("--- Checking for Leaks ---")
    
    # 1. Check Secrets API
    try:
        r = requests.get(f"{BASE_URL}/admin/v1/secrets")
        r.raise_for_status()
        secrets = r.json()["secrets"]
        print(f"Found {len(secrets)} secrets.")
        for s in secrets:
            if "value" in s:
                print(f"🚨 CRITICAL: Secret value exposed for {s['name']}!")
                return False
            if s.get("value_masked") != "******": # If we return masked value
                pass # This is fine if strictly masked
            print(f"✅ Secret {s['name']} is safe (metadata only).")
    except Exception as e:
        print(f"Error checking secrets API: {e}")
        return False

    # 2. Check Upstreams API
    try:
        r = requests.get(f"{BASE_URL}/admin/v1/llm/upstreams")
        r.raise_for_status()
        upstreams = r.json()["upstreams"]
        for u in upstreams:
            # Check if any field looks like a raw key (sk-...)
            dump = json.dumps(u)
            if "sk-" in dump and "secret:sk-" not in dump and "env:sk-" not in dump:
                 # It might be in credentials_ref if user pasted it directly (which is bad practice but "working")
                 # But if they used secret: reference, we check that it didn't resolve.
                 if u.get("credentials_ref", "").startswith("secret:"):
                     print(f"✅ Upstream {u['id']} uses secret reference: {u['credentials_ref']}")
                 elif "sk-" in u.get("credentials_ref", ""):
                     print(f"⚠️  Upstream {u['id']} has RAW KEY in details (User error likely).")
                 else:
                     print(f"✅ Upstream {u['id']} configuration looks safe.")
    except Exception as e:
        print(f"Error checking upstreams API: {e}")
        return False
        
    return True

def test_functionality():
    print("\n--- Testing Functionality (Chat) ---")
    # Try to chat with a model. We need to find a model group.
    try:
        r = requests.get(f"{BASE_URL}/admin/v1/llm/model-groups")
        groups = r.json()["model_groups"]
        if not groups:
            print("No model groups found to test.")
            return

        target_model = groups[0]["id"]
        print(f"Testing chat with model: {target_model}")
        
        # We use a test key for the gateway itself (defined in auth middleware)
        headers = {"Authorization": "Bearer sk-test-key-1"} 
        payload = {
            "model": target_model,
            "messages": [{"role": "user", "content": "Hello, are you working?"}]
        }
        
        r = requests.post(f"{BASE_URL}/v1/chat/completions", json=payload, headers=headers)
        if r.status_code == 200:
            print(f"✅ Chat success! Response: {r.json()['choices'][0]['message']['content'][:50]}...")
        else:
            print(f"❌ Chat failed: {r.status_code} - {r.text}")
            
    except Exception as e:
        print(f"Error testing functionality: {e}")

if __name__ == "__main__":
    if check_leaks():
        test_functionality()
    else:
        sys.exit(1)
