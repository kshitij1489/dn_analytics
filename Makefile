# Makefile for Analytics Docker Setup
# Convenience commands for common Docker operations

.PHONY: help build up down logs restart clean psql shell test

# Default target
help:
	@echo "Analytics Docker Commands:"
	@echo "  make build      - Build Docker images"
	@echo "  make up         - Start all services"
	@echo "  make down       - Stop all services"
	@echo "  make logs       - View logs (follow mode)"
	@echo "  make restart    - Restart all services"
	@echo "  make clean      - Stop and remove all containers/volumes"
	@echo "  make psql       - Access PostgreSQL shell"
	@echo "  make shell      - Access app container shell"
	@echo "  make test       - Test database connection"
	@echo "  make load-menu  - Load menu data into database"
	@echo "  make sync       - Sync new orders"
	@echo "  make reload-all - Reload all orders from API (for migrations)"

# Build images
build:
	docker-compose build

# Start services
up:
	docker-compose up -d
	@echo "Services started. App available at http://localhost:8501"

# Stop services
down:
	docker-compose down

# View logs
logs:
	docker-compose logs -f

# Restart services
restart:
	docker-compose restart

# Clean everything (removes volumes - WARNING: deletes data)
clean:
	docker-compose down -v
	@echo "All containers and volumes removed"

# Access PostgreSQL
psql:
	docker-compose exec postgres psql -U postgres -d analytics

# Access app container shell
shell:
	docker-compose exec app bash

# Test database connection
test:
	docker-compose exec app python3 database/test_connection.py \
		--db-url "postgresql://postgres:$${POSTGRES_PASSWORD:-postgres}@postgres:5432/analytics"

# Load menu data
load-menu:
	docker-compose exec app python3 database/test_load_menu_postgresql.py \
		--db-url "postgresql://postgres:$${POSTGRES_PASSWORD:-postgres}@postgres:5432/analytics"

# Sync new orders
sync:
	docker-compose exec app python3 database/load_orders.py \
		--db-url "postgresql://postgres:$${POSTGRES_PASSWORD:-postgres}@postgres:5432/analytics" \
		--incremental

# Reload all orders (for migrations)
reload-all:
	docker-compose exec app python3 database/load_orders.py \
		--db-url "postgresql://postgres:$${POSTGRES_PASSWORD:-postgres}@postgres:5432/analytics"

# Rebuild and restart
rebuild:
	docker-compose up -d --build

# View running containers
ps:
	docker-compose ps

