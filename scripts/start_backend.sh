#!/bin/bash
# Start the backend with SQLite configuration
export DB_URL="analytics.db"

# Ensure we are in project root
cd "$(dirname "$0")/.."

# Activate virtual environment if it exists
if [ -d ".venv" ]; then
    source .venv/bin/activate
fi

echo "Starting Analytics Backend (SQLite)..."
uvicorn src.api.main:app --reload --port 8000
