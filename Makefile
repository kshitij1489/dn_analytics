# Makefile for Analytics (SQLite)

.PHONY: help start verify clean sync reload-all

# Default target
help:
	@echo "Analytics Commands:"
	@echo "  make start      - Start BOTH Backend and Frontend"
	@echo "  make backend    - Start Backend only"
	@echo "  make frontend   - Start Frontend only"
	@echo "  make verify RESTAURANT_ID=<id> - Verify one profile's SQLite schema"
	@echo "  make clean      - Remove the database file (RESET DB)"
	@echo "  make sync RESTAURANT_ID=<id> - Sync one profile's new orders"
	@echo "  make reload-all RESTAURANT_ID=<id> - Reload one profile from source"

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
	@test -n "$(RESTAURANT_ID)" || (echo "RESTAURANT_ID is required"; exit 2)
	python3 scripts/verify_sqlite.py --restaurant-id "$(RESTAURANT_ID)"

# Clean everything (removes DB - WARNING: deletes data)
clean:
	@echo "Deleting analytics.db..."
	rm -f analytics.db
	@echo "Database removed. Run the app and explicitly re-bind the restaurant profile."

# Sync new orders (run locally)
sync:
	@test -n "$(RESTAURANT_ID)" || (echo "RESTAURANT_ID is required"; exit 2)
	python3 services/load_orders.py --incremental --restaurant-id "$(RESTAURANT_ID)"

# Reload all orders (run locally)
reload-all:
	@test -n "$(RESTAURANT_ID)" || (echo "RESTAURANT_ID is required"; exit 2)
	python3 services/load_orders.py --restaurant-id "$(RESTAURANT_ID)"
