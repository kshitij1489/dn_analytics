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
	@echo "  make psql-shell - Access PostgreSQL shell"
	@echo "  make sync       - Sync new orders"
	@echo "  make reload-all - Reload all orders from API (for migrations)"

# Build images
build:
	docker-compose build

# Start services
up:
	docker-compose up -d
	@echo "PostgreSQL started on port 5432. Run API with: cd src/api && uvicorn main:app --reload"

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

# Access postgres container shell
psql-shell:
	docker-compose exec postgres bash


# Sync new orders (run locally)
sync:
	python3 services/load_orders.py \
		--db-url "postgresql://postgres:postgres@localhost:5432/analytics" \
		--incremental

# Reload all orders (run locally, for migrations)
reload-all:
	python3 services/load_orders.py \
		--db-url "postgresql://postgres:postgres@localhost:5432/analytics"

# Rebuild and restart
rebuild:
	docker-compose up -d --build

# View running containers
ps:
	docker-compose ps

