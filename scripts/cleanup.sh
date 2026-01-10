#!/usr/bin/env bash
set -euo pipefail

# talos-gateway cleanup script
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
SERVICE_NAME="talos-gateway"
PID_FILE="/tmp/${SERVICE_NAME}.pid"
LOG_FILE="/tmp/${SERVICE_NAME}.log"

cd "$REPO_DIR"

# Stop service if running
if [ -f "$PID_FILE" ]; then
    kill "$(cat "$PID_FILE")" 2>/dev/null || true
    rm -f "$PID_FILE"
fi
rm -f "$LOG_FILE"

# Clean dependencies and generated files
rm -rf *.egg-info build dist
rm -rf .venv venv
rm -rf .pytest_cache .ruff_cache
rm -rf __pycache__
# Coverage & reports
rm -f .coverage .coverage.* coverage.xml conformance.xml junit.xml 2>/dev/null || true
rm -rf htmlcov coverage 2>/dev/null || true
# Cache files
rm -rf .mypy_cache .pytype 2>/dev/null || true
find . -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null || true
find . -name "*.pyc" -delete 2>/dev/null || true

echo "✓ $SERVICE_NAME cleaned"
