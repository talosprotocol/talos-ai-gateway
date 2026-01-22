"""Verification script for Phase 15 Budgets."""
import requests
import asyncio
import aiohttp
import time
from decimal import Decimal

BASE_URL = "http://localhost:8000/v1"

# Keys from setup_test_budget.py
KEY_HARD = "test-key-hard"        # $0.03 limit
KEY_WARN = "test-key-warn"        # $0.01 limit
KEY_PRECEDENCE = "test-key-precedence"    # $100 limit (Key), but $0.05 limit (Team)
KEY_CONCURRENCY = "test-key-concurrency"  # $0.03 limit
KEY_STREAMING = "test-key-streaming"      # $0.00 limit

async def make_request(session, key, model="gpt-4", max_tokens=1000, stream=False):
    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": "hello"}],
        "max_tokens": max_tokens,
        "stream": stream
    }
    async with session.post(f"{BASE_URL}/chat/completions", headers=headers, json=payload) as resp:
        body = await resp.json()
        return resp.status, resp.headers, body

async def test_hard_enforcement():
    print("\n--- Testing HARD Enforcement ---")
    async with aiohttp.ClientSession() as session:
        # Request 1: Should succeed
        # Estimate $0.03 (1000 tokens gpt-4)
        status, headers, body = await make_request(session, KEY_HARD, max_tokens=1000)
        print(f"Req 1: Status={status}, Body={body}, Remaining={headers.get('X-Talos-Budget-Remaining-USD')}")
        assert status == 200, f"Expected 200, got {status}: {body}"
        
        # Request 2: Should fail (Remaining $0.02, Estimate $0.03)
        status, headers, body = await make_request(session, KEY_HARD, max_tokens=1000)
        print(f"Req 2: Status={status}, Error={body.get('detail', {}).get('error', {}).get('code')}")
        assert status == 402
        assert body['detail']['error']['code'] == "BUDGET_EXCEEDED"

async def test_concurrency():
    print("\n--- Testing Concurrency (20 parallel) ---")
    # Reset is hard to do via API without admin, so we just assume a fresh key or large enough limit.
    # Actually, we can use a new key name if we wanted, but let's just use a fresh state.
    # We'll use a new key with $0.05 limit via setup script if needed, or just observe partial success.
    
    # We'll use a specific key for this: 'key-concurrency'
    # I'll update setup_test_budget.py to include it or just use KEY_HARD if I can reset it.
    
    async with aiohttp.ClientSession() as session:
        tasks = [make_request(session, KEY_CONCURRENCY, max_tokens=1000) for _ in range(20)]
        results = await asyncio.gather(*tasks)
        
        success = [r for r in results if r[0] == 200]
        blocked = [r for r in results if r[0] == 402]
        
        print(f"Concurrency Result: Success={len(success)}, Blocked={len(blocked)}")
        # If budget is $0.03 and estimate is $0.03, only 1 should succeed.
        assert len(success) == 1

async def test_precedence():
    print("\n--- Testing Precedence (Team Blocks Key) ---")
    async with aiohttp.ClientSession() as session:
        # KEY_PRECEDENCE has $100 limit, but Team Precedence has $0.05 limit.
        # It was manually set to 0.04 used_usd in setup.
        # Estimate $0.03 -> Total 0.07 > 0.05 -> BLOCK.
        status, headers, body = await make_request(session, KEY_PRECEDENCE, max_tokens=1000)
        print(f"Precedence Req: Status={status}, Body={body}")
        assert status == 402
        assert body['detail']['error']['code'] == "BUDGET_EXCEEDED"

async def test_streaming_deferral():
    print("\n--- Testing Streaming Deferral (Bypass HARD) ---")
    async with aiohttp.ClientSession() as session:
        # KEY_STREAMING has $0.00 limit. Non-streaming fails.
        status, headers, body = await make_request(session, KEY_STREAMING, max_tokens=1000, stream=False)
        print(f"Regular Req (Should Block): Status={status}, Body={body}")
        assert status == 402
        
        # Streaming should bypass reservation (WARN behavior).
        status, headers, body = await make_request(session, KEY_STREAMING, max_tokens=1000, stream=True)
        print(f"Streaming Req (Should Bypass 402): Status={status}, Body={body}")
        # Bypass 402 -> reaches route logic -> 400 not implemented.
        assert status == 400

import sys
import traceback

ADMIN_URL = "http://localhost:8000/admin/v1"

async def test_forced_crash():
    print("\n--- Testing Forced Crash (Cleanup) ---")
    scope_id = "key-hard-5c"
    
    async with aiohttp.ClientSession() as session:
        headers = {"X-Talos-Principal": "admin"}
        
        # 1. Get initial state
        async with session.get(f"{ADMIN_URL}/test/budget/scope/virtual_key/{scope_id}", headers=headers) as resp:
            data = await resp.json()
            if resp.status != 200:
                print(f"Error getting scope: Status={resp.status}, Body={data}")
                assert resp.status == 200
            initial_reserved = Decimal(data["reserved_usd"])
        print(f"Initial reserved_usd: {initial_reserved}")
        
        # 2. Simulate a leak of $0.02
        leak_amount = "0.02"
        async with session.post(
            f"{ADMIN_URL}/test/budget/simulate-leak",
            headers=headers,
            json={"scope_id": scope_id, "amount": leak_amount, "scope_type": "virtual_key"}
        ) as resp:
            data = await resp.json()
            if resp.status != 200:
                print(f"Error simulating leak: Status={resp.status}, Body={data}")
                assert resp.status == 200
        print(f"Simulation: Leaked ${leak_amount} reservation created.")
        
        # 3. Verify it's reserved in the scope
        async with session.get(f"{ADMIN_URL}/test/budget/scope/virtual_key/{scope_id}", headers=headers) as resp:
            data = await resp.json()
            if resp.status != 200:
                print(f"Error verifying leak: Status={resp.status}, Body={data}")
                assert resp.status == 200
            after_leak_reserved = Decimal(data["reserved_usd"])
        print(f"Reserved after leak: {after_leak_reserved}")
        assert after_leak_reserved == initial_reserved + Decimal(leak_amount)
        
        # 4. Trigger cleanup
        async with session.post(f"{ADMIN_URL}/test/budget/trigger-cleanup", headers=headers) as resp:
            data = await resp.json()
            if resp.status != 200:
                print(f"Error triggering cleanup: Status={resp.status}, Body={data}")
                assert resp.status == 200
            print(f"Cleanup Triggered: {data}")
        
        # 5. Verify released
        async with session.get(f"{ADMIN_URL}/test/budget/scope/virtual_key/{scope_id}", headers=headers) as resp:
            data = await resp.json()
            if resp.status != 200:
                print(f"Error verifying cleanup: Status={resp.status}, Body={data}")
                assert resp.status == 200
            final_reserved = Decimal(data["reserved_usd"])
        print(f"Final reserved_usd: {final_reserved}")
        assert final_reserved == initial_reserved
        print("Success: Leaked reservation reclaimed.")

async def main():
    try:
        await test_hard_enforcement()
        await test_concurrency() 
        await test_precedence()
        await test_streaming_deferral()
        await test_forced_crash()
        print("\nALL TESTS PASSED")
    except AssertionError as e:
        print(f"\nTEST ASSERTION FAILED: {e}")
        traceback.print_exc()
        sys.exit(1)
    except Exception as e:
        print(f"\nTEST ERROR: {e}")
        traceback.print_exc()
        sys.exit(1)

if __name__ == "__main__":
    asyncio.run(main())
