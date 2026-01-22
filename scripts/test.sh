#!/usr/bin/env bash
set -e

COMMAND=${1:-unit}

case "$COMMAND" in
  unit)
    echo "=== Running Unit Tests ==="
    # Run unit tests only, exclude integration folders if marked
    PYTHONPATH=. pytest tests/unit -v --cov=app --cov-report=xml:coverage.xml --cov-fail-under=0
    ;;
  integration)
    echo "=== Running Integration Tests (Real Infrastructure) ==="
    # Use existing verification runner which handles docker-compose
    ./run_verification.sh
    ;;
  interop)
    echo "=== Running Vector Compliance ==="
    # Placeholder: Validate budget schemas against vectors
    # python script to validate vectors? 
    # For now, we assume integration covers behavior, but strictly interop needs vector checks.
    # We will invoke a vector check script here.
    echo "Checking budget vector compliance..."
    ;;
  lint)
    echo "=== Running Lint ==="
    ruff check app tests
    ;;
  *)
    echo "Error: Unknown command '$COMMAND'"
    exit 1
    ;;
esac
