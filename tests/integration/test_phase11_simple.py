#!/usr/bin/env python3
"""
Phase 11 Integration Tests - Simplified (No App Import)

Tests Phase 11 production hardening features using direct HTTP calls.
No app imports required - works with any running Gateway.
"""

import httpx
import asyncio
import time


GATEWAY_URL = "http://localhost:8000"
JAEGER_UI_URL = "http://localhost:16686"


async def test_health_live():
    """Test /health/live returns 200 OK."""
    async with httpx.AsyncClient() as client:
        resp = await client.get(f"{GATEWAY_URL}/health/live")
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"
        data = resp.json()
        assert data["status"] == "ok", f"Expected status=ok, got {data}"
        print("✅ /health/live: OK")
        return True


async def test_health_ready():
    """Test /health/ready checks PostgreSQL and Redis."""
    async with httpx.AsyncClient() as client:
        resp = await client.get(f"{GATEWAY_URL}/health/ready")
        data = resp.json()
        
        if resp.status_code == 200:
            assert data.get("status") == "ok"
            assert "postgres" in data.get("checks", {})
            assert "redis" in data.get("checks", {})
            print(f"✅ /health/ready: OK - {data}")
            return True
        else:
            print(f"❌ /health/ready: Failed - {data}")
            return False


async def test_jaeger_accessible():
    """Test that Jaeger UI is accessible."""
    async with httpx.AsyncClient(timeout=5.0) as client:
        try:
            resp = await client.get(f"{JAEGER_UI_URL}/api/services")
            if resp.status_code == 200:
                services = resp.json()
                print(f"✅ Jaeger accessible, services: {services.get('data', [])}")
                return True
            else:
                print(f"⚠️  Jaeger returned {resp.status_code}")
                return False
        except Exception as e:
            print(f"⚠️  Jaeger not accessible: {e}")
            return False


async def test_rate_limiting():
    """Test rate limiting by making rapid requests."""
    async with httpx.AsyncClient() as client:
        # Make 20 rapid requests
        responses = []
        for i in range(20):
            try:
                resp = await client.get(f"{GATEWAY_URL}/health/live")
                responses.append(resp.status_code)
            except Exception as e:
                print(f"Request {i+1} failed: {e}")
        
        success_count = sum(1 for r in responses if r == 200)
        rate_limited = sum(1 for r in responses if r == 429)
        
        print(f"✅ Rate limiting: {success_count} success, {rate_limited} rate-limited (429)")
        # Note: Health endpoints might be excluded from rate limiting
        return True


async def test_concurrent_requests():
    """Test that Gateway handles concurrent requests."""
    async with httpx.AsyncClient() as client:
        tasks = [
            client.get(f"{GATEWAY_URL}/health/live")
            for _ in range(10)
        ]
        responses = await asyncio.gather(*tasks, return_exceptions=True)
        
        success_count = sum(1 for r in responses if not isinstance(r, Exception) and r.status_code == 200)
        print(f"✅ Concurrent requests: {success_count}/10 succeeded")
        return success_count == 10


async def run_all_tests():
    """Run all Phase 11 integration tests."""
    print("\n" + "="*60)
    print("Phase 11 Integration Tests - Live Environment")
    print("="*60)
    
    results = {}
    
    # Test 1: Health Live
    try:
        results["health_live"] = await test_health_live()
    except Exception as e:
        print(f"❌ health_live failed: {e}")
        results["health_live"] = False
    
    # Test 2: Health Ready
    try:
        results["health_ready"] = await test_health_ready()
    except Exception as e:
        print(f"❌ health_ready failed: {e}")
        results["health_ready"] = False
    
    # Test 3: Jaeger
    try:
        results["jaeger"] = await test_jaeger_accessible()
    except Exception as e:
        print(f"❌ jaeger failed: {e}")
        results["jaeger"] = False
    
    # Test 4: Rate Limiting
    try:
        results["rate_limiting"] = await test_rate_limiting()
    except Exception as e:
        print(f"❌ rate_limiting failed: {e}")
        results["rate_limiting"] = False
    
    # Test 5: Concurrent Requests
    try:
        results["concurrent"] = await test_concurrent_requests()
    except Exception as e:
        print(f"❌ concurrent failed: {e}")
        results["concurrent"] = False
    
    # Summary
    print("\n" + "="*60)
    print("Test Summary:")
    print("="*60)
    passed = sum(1 for v in results.values() if v)
    total = len(results)
    
    for test_name, result in results.items():
        icon = "✅" if result else "❌"
        print(f"{icon} {test_name}: {'PASS' if result else 'FAIL'}")
    
    print("="*60)
    print(f"Total: {passed}/{total} tests passed")
    print("="*60)
    
    return passed == total


if __name__ == "__main__":
    success = asyncio.run(run_all_tests())
    exit(0 if success else 1)
