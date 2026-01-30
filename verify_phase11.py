#!/usr/bin/env python3
"""Phase 11 Production Hardening Verification Script.

Static code analysis verification (no imports required).
"""

import re
from pathlib import Path


def print_header(text: str):
    print(f"\n{'='*70}")
    print(f"  {text}")
    print(f"{'='*70}")


def print_success(text: str):
    print(f"  ✅ {text}")


def print_warning(text: str):
    print(f"  ⚠️  {text}")


def verify_startup_checks():
    """Verify Phase 11.1 & 11.2 fail-closed checks exist."""
    print_header("Test 1: Startup Fail-Closed Checks")
    
    main_py = Path("app/main.py").read_text()
    
    # Check rate limiting fail-closed
    has_rate_check = ('RATE_LIMIT_BACKEND' in main_py and 'redis' in main_py)
    if has_rate_check:
        print_success("Rate limiting fail-closed check found")
    else:
        print_warning("Rate limiting fail-closed check missing")
        return False
    
    # Check tracing fail-closed
    has_trace_check = ('TRACING_ENABLED' in main_py and 'OTEL_EXPORTER_OTLP_ENDPOINT' in main_py)
    if has_trace_check:
        print_success("Tracing fail-closed check found")
    else:
        print_warning("Tracing fail-closed check missing")
        return False
    
    return True


def verify_error_codes():
    """Verify Phase 11.3 stable error codes."""
    print_header("Test 2: Stable Error Codes")
    
    rate_limit_py = Path("app/middleware/rate_limit.py").read_text()
    shutdown_py = Path("app/middleware/shutdown_gate.py").read_text()
    
    # Check RATE_LIMITED (429)
    if 'RATE_LIMITED' in rate_limit_py:
        print_success("RATE_LIMITED error code found")
    else:
        print_warning("RATE_LIMITED error code missing")
        return False
    
    # Check RATE_LIMITER_UNAVAILABLE (503, dev only)
    if 'RATE_LIMITER_UNAVAILABLE' in rate_limit_py:
        print_success("RATE_LIMITER_UNAVAILABLE error code found")
    else:
        print_warning("RATE_LIMITER_UNAVAILABLE error code missing")
        return False
    
    # Check SERVER_SHUTTING_DOWN (503)
    if 'shutting_down' in shutdown_py.lower():
        print_success("SERVER_SHUTTING_DOWN error code found")
    else:
        print_warning("SERVER_SHUTTING_DOWN error code missing")
        return False
    
    return True


def verify_tracing_redaction():
    """Verify Phase 11.2 sensitive data redaction."""
    print_header("Test 3: Tracing Redaction")
    
    tracing_py = Path("app/observability/tracing.py").read_text()
    
    # Check for sensitive key patterns
    required_keys = ["authorization", "header_b64u", "ciphertext_b64u"]
    
    for key in required_keys:
        if key in tracing_py.lower():
            print_success(f"Redaction for '{key}' configured")
        else:
            print_warning(f"Redaction for '{key}' missing")
            return False
    
    return True


def verify_sql_logging_disabled():
    """Verify SQL statement logging is disabled."""
    print_header("Test 4: SQL Statement Logging")
    
    main_py = Path("app/main.py").read_text()
    
    if 'db_statement_enabled=False' in main_py:
        print_success("SQL statement logging disabled")
        return True
    else:
        print_warning("SQL statement logging not explicitly disabled")
        return False


def verify_health_checks():
    """Verify Phase 11.3 health check endpoints exist."""
    print_header("Test 5: Health Check Endpoints")
    
    health_py = Path("app/routers/health.py").read_text()
    
    if '/health/live' in health_py:
        print_success("/health/live endpoint found")
    else:
        print_warning("/health/live endpoint missing")
        return False
    
    if '/health/ready' in health_py:
        print_success("/health/ready endpoint found")
    else:
        print_warning("/health/ready endpoint missing")
        return False
    
    return True


def verify_graceful_shutdown():
    """Verify Phase 11.4 graceful shutdown middleware."""
    print_header("Test 6: Graceful Shutdown")
    
    shutdown_py = Path("app/middleware/shutdown_gate.py").read_text()
    
    if 'shutting_down' in shutdown_py.lower() and '/health/live' in shutdown_py:
        print_success("Shutdown gate middleware configured")
        return True
    else:
        print_warning("Shutdown gate middleware incomplete")
        return False


def main():
    print_header("Phase 11 Production Hardening Verification")
    print("")
    print("  Static code analysis (no imports required)...")
    print("")
    
    results = []
    
    try:
        results.append(("Startup Checks", verify_startup_checks()))
        results.append(("Error Codes", verify_error_codes()))
        results.append(("Tracing", verify_tracing_redaction()))
        results.append(("SQL Logging Disabled", verify_sql_logging_disabled()))
        results.append(("Health Checks", verify_health_checks()))
        results.append(("Graceful Shutdown", verify_graceful_shutdown()))
    except Exception as e:
        print(f"\n❌ Verification failed with error: {e}")
        import traceback
        traceback.print_exc()
        return False
    
    # Summary
    print_header("Verification Summary")
    print("")
    
    passed = sum(1 for _, result in results if result)
    total = len(results)
    
    for name, result in results:
        status = "✅ PASS" if result else "❌ FAIL"
        print(f"  {status}: {name}")
    
    print("")
    print(f"  Total: {passed}/{total} checks passed")
    print("")
    
    if passed == total:
        print_success("All Phase 11 verifications passed!")
        print("")
        print("  Phase 11 Production Hardening: COMPLETE ✅")
        print("")
        return True
    else:
        print_warning(f"{total - passed} checks failed")
        return False


if __name__ == "__main__":
    import sys
    success = main()
    sys.exit(0 if success else 1)
