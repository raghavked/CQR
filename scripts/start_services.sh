#!/bin/bash
# Start all 7 CQR backend services natively with uvicorn
# Each package uses a src/ layout, so PYTHONPATH must point to the package root.
set -a
source /home/ubuntu/CQR/.env
set +a

REPO=/home/ubuntu/CQR
LOGS=/tmp/cqr-logs
mkdir -p $LOGS

start_service() {
  local name=$1
  local pkg_dir=$2   # e.g. packages/kg-engine
  local module=$3    # e.g. src.api:app
  local port=$4

  echo "Starting $name on port $port..."
  local pkg_path="$REPO/$pkg_dir"
  PYTHONPATH="$pkg_path" \
    nohup uvicorn "$module" \
      --host 0.0.0.0 \
      --port "$port" \
      --log-level info \
      --app-dir "$pkg_path" \
      > "$LOGS/${name}.log" 2>&1 &
  echo $! > "$LOGS/${name}.pid"
  echo "  PID: $!"
}

# Kill any existing instances on our ports
for port in 8000 8001 8002 8003 8004 8005 8006; do
  fuser -k ${port}/tcp 2>/dev/null || true
done
sleep 1

start_service "kg-engine"        "packages/kg-engine"        "src.api:app"      8001
start_service "lsm-layer"        "packages/lsm-layer"        "src.api:app"      8002
start_service "execution-env"    "packages/execution-env"    "src.api:app"      8003
start_service "vault"            "packages/vault"            "src.api:app"      8004
start_service "agent-bridge"     "packages/agent-bridge"     "src.api:app"      8005
start_service "security-scanner" "packages/security-scanner" "src.api:app"      8006
start_service "orchestration"    "packages/orchestration"    "src.main:app"     8000

echo ""
echo "All services started. Waiting 6s for startup..."
sleep 6
echo "Done."
