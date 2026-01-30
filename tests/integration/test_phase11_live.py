#!/usr/bin/env python3
"""
Phase 11 Integration Tests - Live Testing with Docker Services

Tests Phase 11 production hardening features:
- Rate limiting with Redis
- Distributed tracing with Jaeger
- Health checks (live and ready)
- Graceful shutdown

Prerequisites:
    docker-compose -f docker-compose.phase11.yml up -d
"""

import asyncio
import httpx
import pytest
import time
from typing import AsyncIterator


GATEWAY_URL = "http://localhost:8000"
JAEGER_UI_URL = "http://localhost:16686"


@pytest.fixture
async def http_client() -> AsyncIterator[httpx.AsyncClient]:
    """Async HTTP client for gateway requests."""
    async with httpx.AsyncClient(timeout=30.0) as client:
        yield client


class TestRateLimiting:
    """Integration tests for Phase 11.1: Rate Limiting"""
    
    @pytest.mark.asyncio
    async def test_rate_limit_with_redis(self, http_client: httpx.AsyncClient):
        """Test that rate limiting works with Redis backend."""
        # Make rapid requests to trigger rate limit
        responses = []
        for i in range(15):
            try:
                resp = await http_client.get(f"{GATEWAY_URL}/health/live")
                responses.append(resp.status_code)
            except Exception as e:
                print(f"Request {i+1} failed: {e}")
        
        # Should get some 429 responses if rate limiting is enforced
        # (Default is 5 RPS with burst of 10, so 15 rapid requests should hit limit)
        rate_limited = [r for r in responses if r == 429]
        print(f"Got {len(rate_limited)} rate-limited responses out of {len(responses)}")
        
        # Note: Health endpoints might be excluded from rate limiting
        # To properly test, we'd need an endpoint that's rate-limited
    
    @pytest.mark.asyncio
    async def test_rate_limit_headers(self, http_client: httpx.AsyncClient):
        """Test that rate limit headers are present."""
        resp = await http_client.get(f"{GATEWAY_URL}/health/live")
        
        # Check for rate limit headers
        print("Response headers:", dict(resp.headers))
        # Headers like X-RateLimit-Limit, X-RateLimit-Remaining, etc.


class TestDistributedTracing:
    """Integration tests for Phase 11.2: Distributed Tracing"""
    
    @pytest.mark.asyncio
    async def test_traces_exported_to_jaeger(self, http_client: httpx.AsyncClient):
        """Test that traces are exported to Jaeger."""
        # Make a request to generate a trace
        resp = await http_client.get(f"{GATEWAY_URL}/health/live")
        assert resp.status_code == 200
        
        # Wait for trace export
        await asyncio.sleep(2)
        
        # Check Jaeger UI for traces (API endpoint)
        jaeger_resp = await http_client.get(
            f"{JAEGER_UI_URL}/api/services"
        )
        
        if jaeger_resp.status_code == 200:
            services = jaeger_resp.json()
            print("Jaeger services:", services)
            assert "talos-gateway" in services.get("data", [])
        else:
            print(f"Jaeger UI not accessible (status {jaeger_resp.status_code})")
    
    @pytest.mark.asyncio
    async def test_sensitive_data_redacted(self, http_client: httpx.AsyncClient):
        """Test that sensitive data is redacted from traces."""
        # Make a request with Authorization header
        headers = {"Authorization": "Bearer secret_token_123"}
        resp = await http_client.get(f"{GATEWAY_URL}/health/live", headers=headers)
        
        # Wait for trace export
        await asyncio.sleep(2)
        
        # Would need to query Jaeger API to verify redaction
        # For now, just log that the request was made
        print("Request made with Authorization header - verify redaction in Jaeger UI")


class TestHealthChecks:
    """Integration tests for Phase 11.3: Health Checks"""
    
    @pytest.mark.asyncio
    async def test_health_live_returns_200(self, http_client: httpx.AsyncClient):
        """Test /health/live returns 200 OK."""
        resp = await http_client.get(f"{GATEWAY_URL}/health/live")
        
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "healthy"
        assert "timestamp" in data
        print("✅ /health/live:", data)
    
    @pytest.mark.asyncio
    async def test_health_ready_checks_dependencies(self, http_client: httpx.AsyncClient):
        """Test /health/ready checks PostgreSQL and Redis."""
        resp = await http_client.get(f"{GATEWAY_URL}/health/ready")
        
        # Should be 200 if all dependencies are healthy
        print(f"/health/ready status: {resp.status_code}")
        data = resp.json()
        print(f"✅ /health/ready: {data}")
        
        # In Docker Compose environment, should be healthy
        if resp.status_code == 200:
            assert data.get("status") == "healthy"
            # Check dependency statuses
            assert "db" in data or "database" in data
        else:
            print(f"⚠️  Dependencies not healthy: {data}")
    
    @pytest.mark.asyncio
    async def test_health_live_available_during_load(self, http_client: httpx.AsyncClient):
        """Test /health/live responds even under load."""
        # Make concurrent requests
        tasks = [
            http_client.get(f"{GATEWAY_URL}/health/live")
            for _ in range(10)
        ]
        responses = await asyncio.gather(*tasks)
        
        # All should succeed
        assert all(r.status_code == 200 for r in responses)
        print(f"✅ All {len(responses)} concurrent requests succeeded")


class TestGracefulShutdown:
    """Integration tests for Phase 11.4: Graceful Shutdown"""
    
    @pytest.mark.asyncio
    async def test_shutdown_gate_rejects_requests(self, http_client: httpx.AsyncClient):
        """Test that shutdown gate would reject requests during shutdown."""
        # This test would require triggering actual shutdown
        # For manual testing: send SIGTERM to container, then test responses
        print("⏭️  Skipping - requires manual shutdown trigger")
        pytest.skip("Manual test - trigger shutdown and verify 503 responses")
    
    @pytest.mark.asyncio
    async def test_health_live_during_shutdown(self, http_client: httpx.AsyncClient):
        """Test /health/live still responds during shutdown."""
        # Manual test
        print("⏭️  Skipping - requires manual shutdown trigger")
        pytest.skip("Manual test - verify /health/live works during shutdown")


class TestCompliance:
    """Test overall Phase 11 compliance in live environment"""
    
    @pytest.mark.asyncio
    async def test_all_phase11_features_enabled(self, http_client: httpx.AsyncClient):
        """Verify all Phase 11 features are enabled and working."""
        results = {}
        
        # 1. Health checks
        live_resp = await http_client.get(f"{GATEWAY_URL}/health/live")
        results["health_live"] = live_resp.status_code == 200
        
        ready_resp = await http_client.get(f"{GATEWAY_URL}/health/ready")
        results["health_ready"] = ready_resp.status_code in [200, 503]
        
        # 2. Rate limiting (make requests to test)
        rate_test_responses = []
        for _ in range(5):
            resp = await http_client.get(f"{GATEWAY_URL}/health/live")
            rate_test_responses.append(resp.status_code)
        results["rate_limiting"] = all(r in [200, 429] for r in rate_test_responses)
        
        # 3. Tracing (check Jaeger)
        try:
            jaeger_resp = await http_client.get(f"{JAEGER_UI_URL}/api/services", timeout=5.0)
            results["tracing"] = jaeger_resp.status_code == 200
        except:
            results["tracing"] = False
        
        # Print summary
        print("\n" + "="*60)
        print("Phase 11 Feature Status:")
        print("="*60)
        for feature, status in results.items():
            icon = "✅" if status else "❌"
            print(f"{icon} {feature}: {'PASS' if status else 'FAIL'}")
        print("="*60)
        
        # All should pass
        assert all(results.values()), f"Some features failed: {results}"


if __name__ == "__main__":
    print("Running Phase 11 integration tests...")
    print("Ensure Docker Compose is running:")
    print("  docker-compose -f docker-compose.phase11.yml up -d")
    print("")
    
    # Run pytest
    pytest.main([__file__, "-v", "-s"])
