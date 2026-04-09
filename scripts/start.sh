#!/usr/bin/env bash
set -euo pipefail

# talos-ai-gateway start script
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../../.." && pwd)"
SERVICE_NAME="talos-ai-gateway"
PID_FILE="/tmp/${SERVICE_NAME}.pid"
LOG_FILE="/tmp/${SERVICE_NAME}.log"
PORT="${TALOS_AI_GATEWAY_PORT:-8001}"
HOST="${TALOS_BIND_HOST:-127.0.0.1}"

source_env_file() {
    local file="$1"
    if [ -f "$file" ]; then
        set -a
        . "$file"
        set +a
    fi
}

source_env_file "$ROOT_DIR/.env"
source_env_file "$ROOT_DIR/.env.local"
source_env_file "$REPO_DIR/.env"
source_env_file "$REPO_DIR/.env.local"

cd "$REPO_DIR"

# Check if already running
if [ -f "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
    echo "$SERVICE_NAME is already running (PID: $(cat "$PID_FILE"))"
    exit 0
fi

# Start service
# Start service
echo "Starting $SERVICE_NAME on port $PORT..."

# Map TALOS_ENV to MODE if not set
if [ -z "${MODE:-}" ]; then
    if [ "$TALOS_ENV" = "production" ]; then
        export MODE="prod"
    else
        export MODE="dev"
    fi
fi

# Phase 11 Hardening Checks (Script-level pre-check)
if [ "$MODE" = "prod" ]; then
    if [ -z "${REDIS_URL:-}" ]; then
        echo "WARNING: MODE=prod but REDIS_URL is not set. Service may fail to start due to Phase 11 checks."
    fi
fi

TALOS_ENV="${TALOS_ENV:-development}" \
TALOS_RUN_ID="${TALOS_RUN_ID:-default}" \
MODE="$MODE" \
DEV_MODE="${DEV_MODE:-true}" \
USE_JSON_STORES="${USE_JSON_STORES:-true}" \
nohup uvicorn app.main:app --port "$PORT" --host "$HOST" > "$LOG_FILE" 2>&1 &


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
