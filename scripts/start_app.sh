#!/bin/bash
# Start both Backend and Frontend

# Function to kill all child processes on exit
cleanup() {
    echo "Stopping all services..."
    kill $(jobs -p) 2>/dev/null
    exit
}

# Trap SIGINT (Ctrl+C) and call cleanup
trap cleanup SIGINT

# Ensure we are in project root
cd "$(dirname "$0")/.."

# 1. Start Backend
echo "Starting Backend..."
./scripts/start_backend.sh &
BACKEND_PID=$!

# Wait for backend to be ready (rudimentary check)
sleep 2

# 2. Start Frontend
echo "Starting Frontend..."
cd ui_electron && npm run dev &
FRONTEND_PID=$!

# Wait for both
wait $BACKEND_PID $FRONTEND_PID
