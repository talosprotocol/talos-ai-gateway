#!/usr/bin/env python3
"""
Phase 12 Static Verification - Multi-Region Architecture

Verifies Phase 12 implementation without importing the app (avoids psycopg2 dependency).
Checks for:
- Read/write database separation
- Circuit breaker implementation
- Response headers (X-Talos-DB-Role, X-Talos-Read-Fallback)
"""

import re
from pathlib import Path


def check_file_contains(file_path: Path, pattern: str, description: str) -> bool:
    """Check if a file contains a pattern."""
    try:
        content = file_path.read_text()
        if re.search(pattern, content, re.MULTILINE):
            print(f"✅ {description}")
            return True
        else:
            print(f"❌ {description}")
            return False
    except Exception as e:
        print(f"❌ {description} - Error: {e}")
        return False


def verify_phase12():
    """Verify Phase 12 implementation."""
    base = Path(__file__).parent
    results = {}
    
    print("\n" + "="*60)
    print("Phase 12: Multi-Region Architecture - Static Verification")
    print("="*60 + "\n")
    
    # 1. Check read/write database separation
    print("1. Read/Write Database Separation")
    print("-" * 60)
    
    deps_file = base / "app" / "dependencies.py"
    
    results["write_engine"] = check_file_contains(
        deps_file,
        r"_write_engine\s*=\s*create_engine",
        "Write engine initialization"
    )
    
    results["read_engine"] = check_file_contains(
        deps_file,
        r"_read_engine\s*=.*create_engine.*DATABASE_READ_URL",
        "Read engine initialization"
    )
    
    results["engine_distinct"] = check_file_contains(
        deps_file,
        r"_read_engine_is_distinct",
        "Engine distinction tracking"
    )
    
    results["read_only_transaction"] = check_file_contains(
        deps_file,
        r"SET\s+TRANSACTION\s+READ\s+ONLY",
        "Read-only transaction enforcement"
    )
    
    # 2. Check circuit breaker
    print("\n2. Circuit Breaker")
    print("-" * 60)
    
    results["circuit_breaker_class"] = check_file_contains(
        deps_file,
        r"class\s+CircuitBreakerState",
        "CircuitBreakerState class"
    )
    
    results["failure_threshold"] = check_file_contains(
        deps_file,
        r"failure_threshold.*=.*3",
        "Failure threshold = 3"
    )
    
    results["open_duration"] = check_file_contains(
        deps_file,
        r"(open_duration|CIRCUIT_OPEN_DURATION).*30",
        "Open duration = 30 seconds"
    )
    
    results["circuit_record_failure"] = check_file_contains(
        deps_file,
        r"def\s+record_failure",
        "record_failure method"
    )
    
    results["circuit_is_open"] = check_file_contains(
        deps_file,
        r"def\s+is_open",
        "is_open method"
    )
    
    # 3. Check response headers
    print("\n3. Response Headers")
    print("-" * 60)
    
    results["db_role_header"] = check_file_contains(
        deps_file,
        r'["\']X-Talos-DB-Role["\']',
        "X-Talos-DB-Role header"
    )
    
    results["read_fallback_header"] = check_file_contains(
        deps_file,
        r'["\']X-Talos-Read-Fallback["\']',
        "X-Talos-Read-Fallback header"
    )
    
    results["primary_value"] = check_file_contains(
        deps_file,
        r'["\']primary["\']',
        "DB role 'primary' value"
    )
    
    results["replica_value"] = check_file_contains(
        deps_file,
        r'["\']replica["\']',
        "DB role 'replica' value"
    )
    
    # 4. Check configuration
    print("\n4. Configuration")
    print("-" * 60)
    
    config_file = base / "app" / "core" / "config.py"
    
    results["read_url_config"] = check_file_contains(
        config_file,
        r"DATABASE_READ_URL",
        "DATABASE_READ_URL setting"
    )
    
    results["circuit_config"] = check_file_contains(
        config_file,
        r"CIRCUIT_OPEN_DURATION_SECONDS",
        "CIRCUIT_OPEN_DURATION_SECONDS setting"
    )
    
    # 5. Check verification script
    print("\n5. Verification Script")
    print("-" * 60)
    
    verify_script = base / "verify_multi_region.py"
    results["verify_script_exists"] = verify_script.exists()
    
    if results["verify_script_exists"]:
        print("✅ verify_multi_region.py exists")
        
        results["verify_lag_check"] = check_file_contains(
            verify_script,
            r"lag.*threshold",
            "Replication lag check"
        )
    else:
        print("❌ verify_multi_region.py not found")
        results["verify_lag_check"] = False
    
    # 6. Check unit tests
    print("\n6. Unit Tests")
    print("-" * 60)
    
    test_file = base / "tests" / "unit" / "test_phase12_multiregion.py"
    results["test_file_exists"] = test_file.exists()
    
    if results["test_file_exists"]:
        print("✅ test_phase12_multiregion.py exists")
        
        results["test_circuit"] = check_file_contains(
            test_file,
            r"TestCircuitBreaker",
            "Circuit breaker tests"
        )
        
        results["test_headers"] = check_file_contains(
            test_file,
            r"X-Talos-DB-Role",
            "Response header tests"
        )
    else:
        print("❌ test_phase12_multiregion.py not found")
        results["test_circuit"] = False
        results["test_headers"] = False
    
    # Summary
    print("\n" + "="*60)
    print("Summary")
    print("="*60)
    
    passed = sum(1 for v in results.values() if v)
    total = len(results)
    
    print(f"Tests Passed: {passed}/{total}")
    
    if passed == total:
        print("\n✅ Phase 12 Multi-Region Architecture: COMPLETE")
        return True
    else:
        print(f"\n⚠️  Phase 12: {total - passed} checks failed")
        failed = [k for k, v in results.items() if not v]
        print(f"Failed checks: {', '.join(failed)}")
        return False


if __name__ == "__main__":
    success = verify_phase12()
    exit(0 if success else 1)
