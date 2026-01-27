# Makefile for Analytics (SQLite)

.PHONY: help start verify clean sync reload-all

# Default target
help:
	@echo "Analytics Commands:"
	@echo "  make start      - Start BOTH Backend and Frontend"
	@echo "  make backend    - Start Backend only"
	@echo "  make frontend   - Start Frontend only"
	@echo "  make verify     - Verify SQLite connection and schema"
	@echo "  make clean      - Remove the database file (RESET DB)"
	@echo "  make sync       - Sync new orders (incremental)"
	@echo "  make reload-all - Reload all orders from source"

# Start everything (Backend + Frontend)
start:
	./scripts/start_app.sh

# Start backend only
backend:
	./scripts/start_backend.sh

# Start frontend only
frontend:
	cd ui_electron && npm run dev

# Verify DB
verify:
	python3 scripts/verify_sqlite.py

# Clean everything (removes DB - WARNING: deletes data)
clean:
	@echo "Deleting analytics.db..."
	rm -f analytics.db
	@echo "Database removed. Run 'make verify' or 'make start' to recreate."

# Sync new orders (run locally)
sync:
	python3 services/load_orders.py --incremental

# Reload all orders (run locally)
reload-all:
	python3 services/load_orders.py

