#!/bin/bash
# Start the backend with SQLite configuration

# Ensure we are in project root
cd "$(dirname "$0")/.."

# Address the same databases the desktop app uses. Electron passes these four
# variables to the backend it spawns (ui_electron/main.js); a backend started
# from the shell must resolve the identical Electron userData directory, or the
# UI ends up reading a different control database, profile registry and profile
# files than the app it is paired with. Every value stays overridable so a
# throwaway root can still be pointed at explicitly.
if [ -z "$ANALYTICS_APP_DATA_ROOT" ]; then
    case "$(uname -s)" in
        Darwin) ANALYTICS_APP_DATA_ROOT="$HOME/Library/Application Support/dn-analytics" ;;
        *)      ANALYTICS_APP_DATA_ROOT="${XDG_CONFIG_HOME:-$HOME/.config}/dn-analytics" ;;
    esac
fi
export ANALYTICS_APP_DATA_ROOT
export ANALYTICS_DB_PATH="${ANALYTICS_DB_PATH:-$ANALYTICS_APP_DATA_ROOT/analytics.db}"
export ANALYTICS_CONTROL_DB_PATH="${ANALYTICS_CONTROL_DB_PATH:-$ANALYTICS_APP_DATA_ROOT/analytics-control.db}"
export DB_URL="$ANALYTICS_DB_PATH"

mkdir -p "$ANALYTICS_APP_DATA_ROOT"

# Activate virtual environment if it exists
if [ -d ".venv" ]; then
    source .venv/bin/activate
fi

echo "Starting Analytics Backend (SQLite)..."
echo "App data root: $ANALYTICS_APP_DATA_ROOT"
uvicorn src.api.main:app --reload --port 8000
