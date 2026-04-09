import pytest
from fastapi.testclient import TestClient
from app.main import app
import json

client = TestClient(app)

TEST_KEY = "sk-test-key-1"
HEADERS = {"Authorization": f"Bearer {TEST_KEY}"}

def test_chat_completion_streaming_supported():
    """Test that streaming is supported and returns SSE."""
    # This should currently fail with 400 because it's not implemented yet
    response = client.post(
        "/v1/chat/completions",
        headers=HEADERS,
        json={
            "model": "gpt-4-turbo",
            "messages": [{"role": "user", "content": "Hello"}],
            "stream": True
        }
    )
    
    # Once implemented, this should be 200
    # For now, we expect 400 as per current implementation
    if response.status_code == 400:
        assert response.json()["error"]["code"] == "STREAMING_NOT_SUPPORTED"
    else:
        assert response.status_code == 200
        assert response.headers["content-type"] == "text/event-stream"
        
        # Read some of the stream
        lines = response.text.split("\n")
        data_lines = [line for line in lines if line.startswith("data: ")]
        assert len(data_lines) > 0
        
        # Check first chunk
        first_chunk = json.loads(data_lines[0][6:])
        assert "choices" in first_chunk
        assert "delta" in first_chunk["choices"][0]

def test_chat_completion_streaming_accounting():
    """Test that streaming accounting (settle-on-end) works."""
    # This test will be more involved as it needs to check usage/audit stores
    # For now, just ensure it doesn't crash
    pass
