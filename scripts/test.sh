#!/usr/bin/env bash
set -eo pipefail

# =============================================================================
# AI Gateway (Python) Standardized Test Entrypoint
# =============================================================================

ARTIFACTS_DIR="artifacts/coverage"
mkdir -p "$ARTIFACTS_DIR"

COMMAND=${1:-"--unit"}

run_unit() {
    echo "=== Running Unit Tests ==="
    PYTHONPATH=. pytest tests/unit -v --cov=. --cov-branch --cov-report=xml:"$ARTIFACTS_DIR/coverage.xml"
}

run_smoke() {
    echo "=== Running Smoke Tests ==="
    PYTHONPATH=. pytest tests/unit -m smoke --maxfail=1 -q || run_unit
}

run_integration() {
    echo "=== Running Integration Tests ==="
    ./run_verification.sh
}

run_coverage() {
    echo "=== Running Coverage (pytest-cov) ==="
    PYTHONPATH=. pytest tests/unit --cov=app --cov-report=xml:"$ARTIFACTS_DIR/coverage.xml"
}

case "$COMMAND" in
    --smoke)
        run_smoke
        ;;
    --unit)
        run_unit
        ;;
    --integration)
        run_integration
        ;;
    --coverage)
        run_coverage
        ;;
    --ci)
        run_smoke
        run_unit
        run_coverage
        ;;
    --full)
        run_smoke
        run_unit
        run_integration
        run_coverage
        ;;
    *)
        echo "Usage: $0 {--smoke|--unit|--integration|--coverage|--ci|--full}"
        exit 1
        ;;
esac

# Generate minimal results.json
mkdir -p artifacts/test
cat <<EOF > artifacts/test/results.json
{
  "repo_id": "services-ai-gateway",
  "command": "$COMMAND",
  "status": "pass",
  "timestamp": "$(date -u +"%Y-%m-%dT%H:%M:%SZ")"
}
EOF
