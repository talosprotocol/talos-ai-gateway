#!/usr/bin/env bash
set -euo pipefail

# talos-gateway start script
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
SERVICE_NAME="talos-gateway"
PID_FILE="/tmp/${SERVICE_NAME}.pid"
LOG_FILE="/tmp/${SERVICE_NAME}.log"
PORT="${TALOS_GATEWAY_PORT:-8080}"

cd "$REPO_DIR"

# Check if already running
if [ -f "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
    echo "$SERVICE_NAME is already running (PID: $(cat "$PID_FILE"))"
    exit 0
fi

# Start service
echo "Starting $SERVICE_NAME on port $PORT..."
TALOS_ENV="${TALOS_ENV:-production}" \
TALOS_RUN_ID="${TALOS_RUN_ID:-default}" \
uvicorn main:app --port "$PORT" --host 0.0.0.0 > "$LOG_FILE" 2>&1 &


PID=$!
echo "$PID" > "$PID_FILE"

# Wait for startup
sleep 2

# Verify running
if kill -0 "$PID" 2>/dev/null; then
    echo "✓ $SERVICE_NAME started (PID: $PID, Port: $PORT)"
else
    echo "✗ $SERVICE_NAME failed to start. Check $LOG_FILE"
    exit 1
fi
