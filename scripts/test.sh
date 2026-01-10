#!/usr/bin/env bash
set -e

echo "=== Running Talos AI Gateway Tests ==="

# Install deps if needed
pip install -e ".[dev]" -q 2>/dev/null || true

# Run tests
PYTHONPATH=. pytest tests/ -v --tb=short

echo "=== Tests Complete ==="
