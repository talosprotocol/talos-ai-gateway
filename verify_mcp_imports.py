import sys
import os
sys.path.append(os.getcwd())

try:
    from app.adapters.mcp.client import McpClient
    print("[SUCCESS] McpClient imported successfully.")
except ImportError as e:
    print(f"[FAILURE] Failed to import McpClient: {e}")
    sys.exit(1)
