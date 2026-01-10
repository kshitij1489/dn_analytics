# Troubleshooting Guide

## Common Issues

### 1. Blank Page Issue or App Not Loading
**Problem:** You see a blank page when accessing the app at `http://localhost:8501`.

**Solution:**
- **Correct URL:** Use `http://localhost:8501`.
- **Incorrect URL:** Do NOT use `http://0.0.0.0:8501`. The logs might show `0.0.0.0`, but that is the internal Docker address.

**Quick Fix:**
1. Open your browser to `http://localhost:8501`.
2. If that fails, check container status: `docker-compose ps`.
3. Check logs: `docker-compose logs app --tail 50`.

### 2. Port Already in Use
**Error:** `Port 8501 is already in use`.

**Solution:**
Change the port in `docker-compose.yml` or `.env`:
```bash
APP_PORT=8502
docker-compose down && docker-compose up -d
```

### 3. Database Connection Errors
**Error:** `Database connection failed`.

**Steps:**
1. Ensure Postgres is running: `docker-compose ps postgres`.
2. Check logs: `docker-compose logs postgres`.
3. Test connection: `make test` (if Makefile is configured) or:
   ```bash
   docker-compose exec app python3 database/test_connection.py \
     --db-url "postgresql://postgres:postgres@postgres:5432/analytics"
   ```

### 4. Import Errors
**Error:** `ModuleNotFoundError`.

**Solution:**
Rebuild the container to install missing dependencies:
```bash
docker-compose up -d --build
```

## Debugging Workflow

1.  **Check Status:** `docker-compose ps` (All containers should be "Up" and "healthy").
2.  **View Logs:** `docker-compose logs -f`.
3.  **Shell Access:**
    - App: `docker-compose exec app bash`
    - DB: `docker-compose exec postgres psql -U postgres -d analytics`
4.  **Restart:** `docker-compose restart` or `make restart`.

## Still Stuck?
Check the `docs/archive/` folder for older specific reports or logs if relevant.
