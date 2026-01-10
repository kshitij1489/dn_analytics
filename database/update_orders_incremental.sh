#!/bin/bash
# Incremental Order Update Script
# 
# This script fetches and loads only new orders (orders with stream_id > last processed)
# 
# Usage:
#   ./database/update_orders_incremental.sh
#   ./database/update_orders_incremental.sh --db-url "postgresql://user:pass@localhost:5432/analytics"

# Default database URL (can be overridden)
DB_URL="${DB_URL:-postgresql://kshitijsharma@localhost:5432/analytics}"

# Parse arguments
if [ "$1" == "--db-url" ] && [ -n "$2" ]; then
    DB_URL="$2"
fi

echo "=================================================================================="
echo "Incremental Order Update"
echo "=================================================================================="
echo "Database: $DB_URL"
echo "Timestamp: $(date)"
echo ""

# Run the incremental update
python3 database/load_orders.py --db-url "$DB_URL" --incremental

# Check exit code
if [ $? -eq 0 ]; then
    echo ""
    echo "✅ Incremental update completed successfully"
else
    echo ""
    echo "❌ Incremental update failed"
    exit 1
fi

