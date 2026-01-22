#!/bin/bash
set -e

# Phase 15: Adaptive Budgets - Automated Verification Script
# This script automates the full behavioral verification suite.

echo "--- Starting Phase 15 Verification ---"

# 1. Rebuild and restart containers to ensure clean state and env propagation
echo "Step 1: Restarting Multi-Region Cluster..."
docker-compose -f docker-compose.multi-region.yml up -d --build

# 2. Wait for health checks
echo "Step 2: Waiting for Gateway to be Healthy (region-a)..."
until docker inspect -f '{{.State.Health.Status}}' ai-gateway-gateway-region-a-1 | grep -q "healthy"; do
  sleep 2
done
echo "Gateway A Healthy."

# 3. Sync test scripts
echo "Step 3: Syncing Test Scripts..."
docker cp setup_test_budget.py ai-gateway-gateway-region-a-1:/app/setup_test_budget.py
docker cp verify_budgets.py ai-gateway-gateway-region-a-1:/app/verify_budgets.py

# 4. Run Setup
echo "Step 4: Running Budget Setup..."
docker-compose -f docker-compose.multi-region.yml exec gateway-region-a python setup_test_budget.py

# 5. Run Verification
echo "Step 5: Running Behavioral Verification Suite..."
docker-compose -f docker-compose.multi-region.yml exec gateway-region-a python verify_budgets.py

# 6. Run Concurrency Suite
echo "Step 6: Running Concurrency & Cleanup Verification..."
docker cp verify_concurrency.py ai-gateway-gateway-region-a-1:/app/verify_concurrency.py
docker-compose -f docker-compose.multi-region.yml exec gateway-region-a python verify_concurrency.py

echo "--- Verification Complete ---"
