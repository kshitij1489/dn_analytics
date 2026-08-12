#!/bin/bash
# Start both Backend and Frontend

# Function to kill all child processes on exit
cleanup() {
    trap - SIGINT SIGTERM EXIT
    echo "Stopping all services..."
    kill $(jobs -p) 2>/dev/null
    exit
}

# Ctrl+C, termination and ordinary exit all tear the backend down. Without the
# EXIT trap, closing the Electron window left uvicorn holding port 8000, and the
# next launch silently attached to that stale process.
trap cleanup SIGINT SIGTERM EXIT

# Ensure we are in project root
cd "$(dirname "$0")/.."

# 1. Start Backend
echo "Starting Backend..."
./scripts/start_backend.sh &
BACKEND_PID=$!

# 2. Start Frontend. Exactly one process may own port 8000, so tell Electron the
# backend is already being launched here; it waits for this one instead of
# spawning a second uvicorn that dies on bind.
echo "Starting Frontend..."
export ANALYTICS_SKIP_BACKEND_SPAWN=1
(cd ui_electron && npm run dev) &
FRONTEND_PID=$!

# Stop as soon as either side exits, so a crashed backend does not leave the UI
# running against nothing. macOS ships Bash 3.2, which has no `wait -n`, so poll
# the two exact child PIDs and let the EXIT trap terminate the survivor.
while kill -0 "$BACKEND_PID" 2>/dev/null && kill -0 "$FRONTEND_PID" 2>/dev/null; do
    sleep 1
done
