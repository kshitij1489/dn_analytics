# Docker Quick Start

Get the Analytics app running in Docker in 3 steps!

## Prerequisites

- Docker installed
- Docker Compose installed (usually included)

## Quick Start

```bash
# 1. Create .env file (optional)
cp .env.example .env
# Edit .env to change passwords if needed

# 2. Start everything
docker-compose up -d

# 3. Access the app
# Open http://localhost:8501
```

That's it! The app and PostgreSQL will start automatically.

## Using Makefile (Easier)

```bash
# Start services
make up

# View logs
make logs

# Stop services
make down

# Access PostgreSQL
make psql

# Load menu data
make load-menu

# Sync new orders
make sync
```

## Initial Setup

After starting containers:

```bash
# 1. Load menu data
make load-menu

# 2. Load some test orders
docker-compose exec app python3 database/load_orders.py \
  --db-url "postgresql://postgres:postgres@postgres:5432/analytics" \
  --limit 10

# 3. Access app at http://localhost:8501
```

## Common Commands

```bash
# View logs
docker-compose logs -f app

# Restart services
docker-compose restart

# Stop everything
docker-compose down

# Clean everything (removes data!)
docker-compose down -v
```

## Troubleshooting

**Blank page when accessing app:**
- ✅ Use `http://localhost:8501` (NOT `http://0.0.0.0:8501`)
- The `0.0.0.0` address shown in logs is internal Docker address
- From your browser, always use `localhost` or `127.0.0.1`

**Port already in use:**
- Change `APP_PORT` in `.env` file
- Or edit `docker-compose.yml`

**Can't connect to database:**
- Wait a few seconds for PostgreSQL to start
- Check logs: `docker-compose logs postgres`

**App not loading:**
- Check logs: `docker-compose logs app`
- Verify container is running: `docker-compose ps`
- See `TROUBLESHOOTING_APP.md` for detailed troubleshooting

For detailed documentation, see `DOCKER_README.md`

