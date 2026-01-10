# Docker Setup Guide

This guide explains how to run the Analytics Database Client app using Docker.

## Prerequisites

- Docker installed ([Install Docker](https://docs.docker.com/get-docker/))
- Docker Compose installed (usually included with Docker Desktop)

## Quick Start

### Option 1: Docker Compose (Recommended)

This will start both PostgreSQL and the Streamlit app:

```bash
# 1. Create .env file (optional - uses defaults if not present)
cp .env.example .env
# Edit .env if you want to change passwords/ports

# 2. Start services
docker-compose up -d

# 3. View logs
docker-compose logs -f app

# 4. Access the app
# ⚠️ IMPORTANT: Use http://localhost:8501 (NOT http://0.0.0.0:8501)
# Open http://localhost:8501 in your browser
```

**Note:** The logs may show `http://0.0.0.0:8501` - this is the internal Docker address. Always use `http://localhost:8501` from your browser.

### Option 2: Docker Only (App Only)

If you already have PostgreSQL running:

```bash
# 1. Build the image
docker build -t analytics-app .

# 2. Run the container
docker run -d \
  --name analytics-app \
  -p 8501:8501 \
  -e DB_URL="postgresql://user:password@host.docker.internal:5432/analytics" \
  analytics-app

# 3. View logs
docker logs -f analytics-app
```

## Docker Compose Services

### PostgreSQL Service

- **Image:** `postgres:16-alpine`
- **Port:** `5432` (configurable via `POSTGRES_PORT`)
- **Database:** `analytics`
- **User:** `postgres`
- **Password:** Set via `POSTGRES_PASSWORD` in `.env`
- **Data Persistence:** Stored in Docker volume `postgres_data`
- **Schema:** Automatically loads SQL files from `database/schema/` on first run

### Streamlit App Service

- **Port:** `8501` (configurable via `APP_PORT`)
- **Database Connection:** Automatically connects to `postgres` service
- **Hot Reload:** Code changes are reflected immediately (in development mode)

## Configuration

### Environment Variables

Create a `.env` file in the project root:

```bash
# PostgreSQL
POSTGRES_PASSWORD=your_secure_password
POSTGRES_PORT=5432

# Application
APP_PORT=8501
```

### Database Connection

The app automatically connects to the PostgreSQL service using:
```
postgresql://postgres:${POSTGRES_PASSWORD}@postgres:5432/analytics
```

To override, set `DB_URL` in `.env`:
```bash
DB_URL=postgresql://user:password@host:port/database
```

## Common Commands

### Start Services
```bash
docker-compose up -d
```

### Stop Services
```bash
docker-compose down
```

### View Logs
```bash
# All services
docker-compose logs -f

# Specific service
docker-compose logs -f app
docker-compose logs -f postgres
```

### Restart Services
```bash
docker-compose restart
```

### Rebuild After Code Changes
```bash
docker-compose up -d --build
```

### Access PostgreSQL
```bash
# Using docker-compose
docker-compose exec postgres psql -U postgres -d analytics

# Or using docker directly
docker exec -it analytics-postgres psql -U postgres -d analytics
```

### Execute Commands in App Container
```bash
docker-compose exec app bash
```

## Initial Setup

After starting the containers for the first time:

### 1. Load Menu Data

```bash
# Option A: Using docker-compose exec
docker-compose exec app python3 database/test_load_menu_postgresql.py \
  --db-url "postgresql://postgres:postgres@postgres:5432/analytics"

# Option B: Access container and run
docker-compose exec app bash
python3 database/test_load_menu_postgresql.py --db-url "postgresql://postgres:postgres@postgres:5432/analytics"
```

### 2. Load Historical Orders

```bash
docker-compose exec app python3 database/load_orders.py \
  --db-url "postgresql://postgres:postgres@postgres:5432/analytics" \
  --limit 10  # Test with 10 orders first
```

### 3. Access the App

Open http://localhost:8501 in your browser

## Production Deployment

### Security Considerations

1. **Change Default Passwords:**
   ```bash
   POSTGRES_PASSWORD=your_very_secure_password
   ```

2. **Remove Volume Mounts:**
   In `docker-compose.yml`, remove the volume mounts for production:
   ```yaml
   # Remove these lines in production:
   volumes:
     - ./app.py:/app/app.py
     # ... other mounts
   ```

3. **Use Secrets Management:**
   - Use Docker secrets or environment variables
   - Don't commit `.env` file to git

4. **Add Authentication:**
   - Configure Streamlit authentication
   - Use reverse proxy (nginx) with SSL

5. **Resource Limits:**
   Add to `docker-compose.yml`:
   ```yaml
   services:
     app:
       deploy:
         resources:
           limits:
             cpus: '2'
             memory: 2G
   ```

### Production docker-compose.yml Example

```yaml
version: '3.8'

services:
  postgres:
    image: postgres:16-alpine
    environment:
      POSTGRES_DB: analytics
      POSTGRES_USER: postgres
      POSTGRES_PASSWORD_FILE: /run/secrets/postgres_password
    secrets:
      - postgres_password
    volumes:
      - postgres_data:/var/lib/postgresql/data
    restart: always

  app:
    build: .
    environment:
      DB_URL: postgresql://postgres@postgres:5432/analytics
    secrets:
      - postgres_password
    depends_on:
      - postgres
    restart: always

secrets:
  postgres_password:
    external: true

volumes:
  postgres_data:
```

## Troubleshooting

### Container Won't Start

**Check logs:**
```bash
docker-compose logs app
docker-compose logs postgres
```

**Common issues:**
- Port already in use: Change `APP_PORT` or `POSTGRES_PORT` in `.env`
- Database connection failed: Check `DB_URL` environment variable
- Permission errors: Check file permissions

### Database Connection Issues

**Test connection from app container:**
```bash
docker-compose exec app python3 database/test_connection.py \
  --db-url "postgresql://postgres:postgres@postgres:5432/analytics"
```

**Check if PostgreSQL is ready:**
```bash
docker-compose exec postgres pg_isready -U postgres
```

### App Not Loading

1. Check if container is running:
   ```bash
   docker-compose ps
   ```

2. Check app logs:
   ```bash
   docker-compose logs -f app
   ```

3. Verify port mapping:
   ```bash
   docker-compose port app 8501
   ```

### Data Persistence

Data is stored in Docker volumes. To backup:

```bash
# Backup PostgreSQL data
docker-compose exec postgres pg_dump -U postgres analytics > backup.sql

# Restore
docker-compose exec -T postgres psql -U postgres analytics < backup.sql
```

### Clean Start (Remove All Data)

⚠️ **Warning:** This will delete all data!

```bash
docker-compose down -v
docker-compose up -d
```

## Development Mode

For development with hot reload, the `docker-compose.yml` includes volume mounts. Code changes will be reflected immediately.

To disable hot reload in production, remove the volume mounts from `docker-compose.yml`.

## Health Checks

Both services include health checks:

- **PostgreSQL:** Checks if database is ready to accept connections
- **App:** Checks if Streamlit server is responding

View health status:
```bash
docker-compose ps
```

## Networking

Services communicate via Docker network `analytics-network`. The app connects to PostgreSQL using the service name `postgres` as the hostname.

## File Structure

```
.
├── Dockerfile              # App container definition
├── docker-compose.yml      # Multi-container setup
├── .dockerignore          # Files to exclude from build
├── .env.example           # Environment variables template
└── .env                   # Your environment variables (not in git)
```

## Additional Resources

- [Docker Documentation](https://docs.docker.com/)
- [Docker Compose Documentation](https://docs.docker.com/compose/)
- [Streamlit Deployment](https://docs.streamlit.io/deploy)

---

*Last Updated: January 9, 2026*

