#!/usr/bin/env python3
"""
Phase 13 Static Verification - Secrets Rotation Automation

Verifies Phase 13 implementation without running the app.
Checks for:
- Atomic rotation (transaction-based)
- Concurrency control (Postgres advisory locks)
- Multi-KEK support
- Background worker
- Fail-closed behavior
"""

import re
from pathlib import Path


def check_file_contains(file_path: Path, pattern: str, description: str) -> bool:
    """Check if a file contains a pattern."""
    try:
        content = file_path.read_text()
        if re.search(pattern, content, re.MULTILINE | re.DOTALL):
            print(f"✅ {description}")
            return True
        else:
            print(f"❌ {description}")
            return False
    except Exception as e:
        print(f"❌ {description} - Error: {e}")
        return False


def verify_phase13():
    """Verify Phase 13 implementation."""
    base = Path(__file__).parent
    results = {}
    
    print("\n" + "="*60)
    print("Phase 13: Secrets Rotation Automation - Static Verification")
    print("="*60 + "\n")
    
    # 1. Check atomic rotation (transaction-based)
    print("1. Atomic Rotation (Transactions)")
    print("-" * 60)
    
    rotation_worker = base / "app" / "jobs" / "rotation_worker.py"
    
    results["worker_exists"] = rotation_worker.exists()
    if not results["worker_exists"]:
        print("❌ rotation_worker.py not found")
    else:
        print("✅ rotation_worker.py exists")
        
        results["db_commit"] = check_file_contains(
            rotation_worker,
            r"db\.commit\(\)",
            "Database transaction commits"
        )
        
        results["batch_rotation"] = check_file_contains(
            rotation_worker,
            r"rotate_batch",
            "Batch rotation method"
        )
    
    # 2. Check concurrency control (Postgres advisory locks)
    print("\n2. Concurrency Control (Advisory Locks)")
    print("-" * 60)
    
    results["advisory_lock"] = check_file_contains(
        rotation_worker,
        r"pg_try_advisory_lock",
        "pg_try_advisory_lock usage"
    )
    
    results["lock_unlock"] = check_file_contains(
        rotation_worker,
        r"pg_advisory_unlock",
        "pg_advisory_unlock (lock release)"
    )
    
    results["lock_id"] = check_file_contains(
        rotation_worker,
        r"ROTATION_LOCK_ID",
        "Deterministic lock ID"
    )
    
    # 3. Check Multi-KEK support
    print("\n3. Multi-KEK Support")
    print("-" * 60)
    
    rotation_service = base / "app" / "domain" / "secrets" / "rotation.py"
    results["rotation_service_exists"] = rotation_service.exists()
    
    if not results["rotation_service_exists"]:
        print("❌ rotation.py not found")
    else:
        print("✅ rotation.py exists")
        
        results["rotation_service_class"] = check_file_contains(
            rotation_service,
            r"class\s+RotationService",
            "RotationService class"
        )
    
    # Check MultiKekProvider
    kek_file = base / "app" / "domain" / "secrets" / "kek_provider.py"
    results["multi_kek_exists"] = kek_file.exists()
    
    if results["multi_kek_exists"]:
        print("✅ kek_provider.py exists")
        
        results["multi_kek_class"] = check_file_contains(
            kek_file,
            r"class\s+MultiKekProvider",
            "MultiKekProvider class"
        )
        
        results["decrypt_with_fallback"] = check_file_contains(
            kek_file,
            r"(decrypt.*fallback|try.*except.*kek)",
            "KEK fallback logic"
        )
    else:
        print("❌ kek_provider.py not found")
        results["multi_kek_class"] = False
        results["decrypt_with_fallback"] = False
    
    # 4. Check background worker integration
    print("\n4. Background Worker Integration")
    print("-" * 60)
    
    main_file = base / "app" / "main.py"
    
    results["worker_import"] = check_file_contains(
        main_file,
        r"from\s+app\.jobs\.rotation_worker\s+import\s+rotation_worker",
        "rotation_worker import in main.py"
    )
    
    results["worker_task"] = check_file_contains(
        main_file,
        r"rotation_task\s*=\s*asyncio\.create_task",
        "rotation_task created on startup"
    )
    
    # 5. Check fail-closed behavior
    print("\n5. Fail-Closed Behavior")
    print("-" * 60)
    
    if rotation_worker.exists():
        results["error_handling"] = check_file_contains(
            rotation_worker,
            r"try:.*except.*Exception",
            "Exception handling in worker"
        )
        
        results["failed_status"] = check_file_contains(
            rotation_worker,
            r'status\s*=\s*["\']failed["\']',
            "Failed status on error"
        )
        
        results["audit_failed"] = check_file_contains(
            rotation_worker,
            r"rotation_failed",
            "Audit event for failure"
        )
    else:
        results["error_handling"] = False
        results["failed_status"] = False
        results["audit_failed"] = False
    
    # 6. Check API endpoints
    print("\n6. Admin API Endpoints")
    print("-" * 60)
    
    admin_router = base / "app" / "api" / "admin" / "router.py"
    
    results["rotate_endpoint"] = check_file_contains(
        admin_router,
        r'["\']secrets/rotate-all["\']',
        "/admin/v1/secrets/rotate-all endpoint"
    )
    
    results["rotation_status_endpoint"] = check_file_contains(
        admin_router,
        r'["\']secrets/rotation-status',
        "/admin/v1/secrets/rotation-status endpoint"
    )
    
    results["rotation_already_running_check"] = check_file_contains(
        admin_router,
        r"ROTATION_ALREADY_RUNNING",
        "Check for concurrent rotation attempts"
    )
    
    # 7. Check database schema
    print("\n7. Database Schema")
    print("-" * 60)
    
    models_file = base / "app" / "adapters" / "postgres" / "models.py"
    
    results["rotation_operation_model"] = check_file_contains(
        models_file,
        r"class\s+RotationOperation",
        "RotationOperation model"
    )
    
    results["rotation_table"] = check_file_contains(
        models_file,
        r'__tablename__\s*=\s*["\']rotation_operations["\']',
        "rotation_operations table"
    )
    
    # 8. Check verification script
    print("\n8. Verification Script")
    print("-" * 60)
    
    verify_script = base / "verify_rotation.py"
    results["verify_script_exists"] = verify_script.exists()
    
    if results["verify_script_exists"]:
        print("✅ verify_rotation.py exists")
        
        results["test_rotation_flow"] = check_file_contains(
            verify_script,
            r"def\s+test_rotation_flow",
            "test_rotation_flow function"
        )
        
        results["test_multi_kek"] = check_file_contains(
            verify_script,
            r"test_multi_kek",
            "Multi-KEK status test"
        )
    else:
        print("❌ verify_rotation.py not found")
        results["test_rotation_flow"] = False
        results["test_multi_kek"] = False
    
    # Summary
    print("\n" + "="*60)
    print("Summary")
    print("="*60)
    
    passed = sum(1 for v in results.values() if v)
    total = len(results)
    
    print(f"Tests Passed: {passed}/{total}")
    
    if passed == total:
        print("\n✅ Phase 13 Secrets Rotation Automation: COMPLETE")
        return True
    else:
        print(f"\n⚠️  Phase 13: {total - passed} checks failed")
        failed = [k for k, v in results.items() if not v]
        print(f"Failed checks: {', '.join(failed)}")
        return False


if __name__ == "__main__":
    success = verify_phase13()
    exit(0 if success else 1)
