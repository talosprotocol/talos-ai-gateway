#!/usr/bin/env python3
"""Integration verification script for Talos AI Gateway Admin API."""
import requests
import sys
import uuid
import time

BASE_URL = "http://localhost:8000"
ADMIN_URL = f"{BASE_URL}/admin/v1"
HEADERS = {"Content-Type": "application/json", "Authorization": "Bearer admin-1"}

def log(msg, success=True):
    icon = "✅" if success else "❌"
    print(f"{icon} {msg}")

def test_catalog():
    print("\n--- Testing Provider Catalog ---")
    try:
        r = requests.get(f"{ADMIN_URL}/catalog/provider-templates", headers=HEADERS)
        r.raise_for_status()
        data = r.json()
        version = data.get("version")
        templates = data.get("templates", [])
        log(f"Fetched Catalog v{version} with {len(templates)} templates")
        
        # Verify specific provider exists
        openai = next((t for t in templates if t["provider_key"] == "openai"), None)
        if openai:
            log(f"Found OpenAI template: {openai['default_base_url']}")
        else:
            log("OpenAI template missing!", False)
            
    except Exception as e:
        log(f"Catalog test failed: {e}", False)

def test_secrets():
    print("\n--- Testing Secrets API ---")
    secret_name = f"test-secret-{uuid.uuid4().hex[:8]}"
    try:
        # Create
        r = requests.post(f"{ADMIN_URL}/secrets", headers=HEADERS, json={
            "name": secret_name,
            "value": "sk-test-123456789"
        })
        r.raise_for_status()
        log(f"Created secret: {secret_name}")
        
        # List
        r = requests.get(f"{ADMIN_URL}/secrets", headers=HEADERS)
        r.raise_for_status()
        secrets = r.json().get("secrets", [])
        if any(s["name"] == secret_name for s in secrets):
            log(f"Secret {secret_name} found in list")
        else:
            log(f"Secret {secret_name} NOT found in list", False)
            
        # Delete
        r = requests.delete(f"{ADMIN_URL}/secrets/{secret_name}", headers=HEADERS)
        r.raise_for_status()
        log(f"Deleted secret: {secret_name}")
        
    except Exception as e:
        log(f"Secrets test failed: {e}", False)

def test_secrets_leaks():
    print("\n--- Testing Secrets Safety (Leaks) ---")
    try:
        r = requests.get(f"{ADMIN_URL}/secrets", headers=HEADERS)
        r.raise_for_status()
        secrets = r.json().get("secrets", [])
        leaks = False
        for s in secrets:
            if "value" in s:
                log(f"CRITICAL: Secret value exposed for {s['name']}", False)
                leaks = True
            elif "value_masked" in s and s["value_masked"] != "******":
                 log(f"Secret {s['name']} not strictly masked", False)
                 leaks = True
            else:
                 log(f"Secret {s['name']} is safe")
        if not leaks:
            log("No leaks found")
    except Exception as e:
        log(f"Secrets leak check failed: {e}", False)

def test_chat():
    print("\n--- Testing Chat Functionality ---")
    try:
        # Get a model group
        r = requests.get(f"{ADMIN_URL}/llm/model-groups", headers=HEADERS)
        groups = r.json().get("model_groups", [])
        if not groups:
            log("No model groups to test chat", False)
            return

        target = groups[0]["id"]
        print(f"Testing chat with {target}...")
        
        # Chat request
        # Use test key defined in auth_public
        chat_headers = {"Authorization": "Bearer sk-test-key-1"}
        payload = {
            "model": target,
            "messages": [{"role": "user", "content": "Hello"}]
        }
        r = requests.post(f"{BASE_URL}/v1/chat/completions", json=payload, headers=chat_headers)
        
        if r.status_code == 200:
            log("Chat success (200 OK)")
        elif r.status_code == 429:
            log("Chat reached upstream (429 Rate Limit) - Connectivity Verified")
        elif r.status_code >= 500:
            log(f"Chat failed with Server Error: {r.status_code} {r.text}", False)
        else:
            log(f"Chat returned unexpected status: {r.status_code} {r.text}")
            
    except Exception as e:
        log(f"Chat test failed: {e}", False)

def test_chat_ollama():
    print("\n--- Testing Chat Functionality (Ollama) ---")
    try:
        target = "llama3"
        print(f"Testing chat with {target}...")
        
        chat_headers = {"Authorization": "Bearer sk-test-key-1"}
        payload = {
            "model": target,
            "messages": [{"role": "user", "content": "Hello"}]
        }
        r = requests.post(f"{BASE_URL}/v1/chat/completions", json=payload, headers=chat_headers)
        
        if r.status_code == 200:
            data = r.json()
            if "choices" in data:
                content = data['choices'][0]['message']['content']
                log(f"Ollama Success: {content[:50]}...")
            else:
                log(f"Ollama Success but unexpected body: {data}")
        elif r.status_code == 502:
            detail = r.json().get('detail', {}).get('error', {}).get('message', '')
            if "ConnectCallFailed" in detail or "Connection refused" in detail or "Max retries exceeded" in detail:
                 log("Ollama unreachable (Connection Refused) - Is Ollama running?", False)
            else:
                 log(f"Ollama failed with 502: {detail}", False)
        else:
            log(f"Ollama returned unexpected status: {r.status_code} {r.text}", False)
            
    except Exception as e:
        log(f"Ollama test failed: {e}", False)

import subprocess
import os

def test_protocol():
    print("\n--- Testing Talos Protocol (WebSocket) ---")
    # Subprocess test_protocol_handshake.py
    try:
        # Check if file exists
        if not os.path.exists("test_protocol_handshake.py"):
             log("test_protocol_handshake.py not found", False)
             return

        result = subprocess.run([sys.executable, "test_protocol_handshake.py"], 
                                capture_output=True, text=True, env={**os.environ, "PYTHONPATH": "."})
        if result.returncode == 0:
            log("Protocol Handshake verified (via subprocess)")
        else:
            log("Protocol Handshake failed", False)
            print(result.stdout)
            print(result.stderr)
            
    except Exception as e:
        log(f"Protocol test failed: {e}", False)

def test_cli_secrets():
    print("\n--- Testing CLI Secrets ---")
    try:
        # Check if we can run module
        cmd = [sys.executable, "-m", "app.cli", "secret", "list"]
        result = subprocess.run(cmd, capture_output=True, text=True, env={**os.environ, "PYTHONPATH": "."})
        if result.returncode == 0:
            log("CLI secret list command runs successfully")
        else:
            log("CLI secret list command failed", False)
            print(result.stderr)
    except Exception as e:
        log(f"CLI test failed: {e}", False)

def test_mcp():
    print("\n--- Testing MCP API ---")
    server_id = f"mcp-test-{uuid.uuid4().hex[:8]}"
    try:
        # Create
        data = {
            "id": server_id,
            "transport": "stdio",
            "command": "npx",
            "args": ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"],
            "env": {},
            "enabled": True
        }
        r = requests.post(f"{ADMIN_URL}/mcp/servers", headers=HEADERS, json=data)
        r.raise_for_status()
        log(f"Created MCP server: {server_id}")
        
        # List
        r = requests.get(f"{ADMIN_URL}/mcp/servers", headers=HEADERS)
        r.raise_for_status()
        servers = r.json().get("servers", [])
        created = next((s for s in servers if s["id"] == server_id), None)
        if created:
            log(f"Found MCP server: {created['command']} {created['args']}")
        else:
            log(f"MCP server {server_id} NOT found", False)
            
        # Disable
        r = requests.post(f"{ADMIN_URL}/mcp/servers/{server_id}:disable", headers=HEADERS)
        r.raise_for_status()
        log(f"Disabled MCP server: {server_id}")
        
        # Delete
        r = requests.delete(f"{ADMIN_URL}/mcp/servers/{server_id}", headers=HEADERS)
        r.raise_for_status()
        log(f"Deleted MCP server: {server_id}")
        
    except Exception as e:
        log(f"MCP test failed: {e}", False)

def test_dashboard():
    print("\n--- Testing Dashboard UI ---")
    try:
        r = requests.get(f"{BASE_URL}/")
        r.raise_for_status()
        html = r.text
        if "Talos AI Gateway" in html:
            log("Dashboard loads successfully")
        else:
            log("Dashboard title missing", False)
            
        if "MCP Servers" in html:
            log("MCP Servers section found in HTML")
        else:
            log("MCP Servers section MISSING in HTML", False)
            
    except Exception as e:
        log(f"Dashboard test failed: {e}", False)

if __name__ == "__main__":
    print(f"Verifying Talos Gateway at {BASE_URL}...")
    try:
        # Wait for server if needed
        for i in range(5):
            try:
                requests.get(f"{BASE_URL}/docs", timeout=1)
                break
            except:
                print(f"Waiting for server... {i+1}/5")
                time.sleep(1)
                
        test_catalog()
        test_catalog()
        test_secrets()
        
        # New Tests
        test_secrets_leaks()
        test_chat()
        test_chat_ollama()
        test_protocol()
        test_cli_secrets()
        
        test_mcp()
        test_dashboard()
        print("\nVerification Complete.")
    except Exception as e:
        print(f"\nFATAL: Verification script failed: {e}")
        sys.exit(1)
