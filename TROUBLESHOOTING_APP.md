# Troubleshooting - Analytics App

## Blank Page Issue

### Problem: Blank page when accessing the app

**Solution:** Use `http://localhost:8501` instead of `http://0.0.0.0:8501`

The URL `http://0.0.0.0:8501` shown in the logs is the internal Docker address. From your browser, you need to use:
- ✅ **Correct:** `http://localhost:8501`
- ❌ **Wrong:** `http://0.0.0.0:8501`

### Check if App is Running

```bash
# Check container status
docker-compose ps

# Check app logs for errors
docker-compose logs app --tail 100

# Check if port is accessible
curl http://localhost:8501
```

### Common Issues

#### 1. Port Already in Use

**Error:** `Port 8501 is already in use`

**Solution:**
```bash
# Change port in docker-compose.yml or .env
APP_PORT=8502

# Restart
docker-compose down
docker-compose up -d
```

#### 2. Import Errors

**Error:** `ModuleNotFoundError` or `ImportError`

**Solution:**
```bash
# Rebuild the container
docker-compose up -d --build

# Check if all dependencies are installed
docker-compose exec app pip list
```

#### 3. Database Connection Errors

**Error:** `Database connection failed`

**Solution:**
```bash
# Check if PostgreSQL is running
docker-compose ps postgres

# Test connection
docker-compose exec app python3 database/test_connection.py \
  --db-url "postgresql://postgres:postgres@postgres:5432/analytics"

# Check PostgreSQL logs
docker-compose logs postgres
```

#### 4. App Crashes on Load

**Check logs:**
```bash
docker-compose logs app --tail 200
```

**Common causes:**
- Missing dependencies
- Database not ready
- Import errors
- Syntax errors in app.py

#### 5. Browser Shows "Connection Refused"

**Solution:**
1. Verify container is running: `docker-compose ps`
2. Check port mapping: `docker-compose port app 8501`
3. Try accessing from different browser
4. Check firewall settings

## Debugging Steps

### Step 1: Verify Container is Running

```bash
docker-compose ps
```

Should show both `analytics-app` and `analytics-postgres` as "Up"

### Step 2: Check App Logs

```bash
docker-compose logs app --tail 50 -f
```

Look for:
- Error messages
- Import errors
- Database connection errors
- Python tracebacks

### Step 3: Test App Directly

```bash
# Access container shell
docker-compose exec app bash

# Inside container, test imports
python3 -c "import streamlit; print('OK')"
python3 -c "import app; print('App imports OK')"
```

### Step 4: Test Database Connection

```bash
docker-compose exec app python3 database/test_connection.py \
  --db-url "postgresql://postgres:postgres@postgres:5432/analytics"
```

### Step 5: Restart Services

```bash
# Restart app only
docker-compose restart app

# Restart everything
docker-compose restart

# Full restart (rebuild)
docker-compose down
docker-compose up -d --build
```

## Quick Fixes

### Fix 1: Rebuild Container

```bash
docker-compose down
docker-compose build --no-cache
docker-compose up -d
```

### Fix 2: Check Environment Variables

```bash
# Verify DB_URL is set correctly
docker-compose exec app env | grep DB_URL

# Should show:
# DB_URL=postgresql://postgres:postgres@postgres:5432/analytics
```

### Fix 3: Clear Streamlit Cache

```bash
# Restart app to clear cache
docker-compose restart app
```

### Fix 4: Check File Permissions

```bash
# Verify app.py is readable
docker-compose exec app ls -la /app/app.py

# Should show file exists and is readable
```

## Getting More Information

### Enable Verbose Logging

Add to `docker-compose.yml`:
```yaml
services:
  app:
    environment:
      STREAMLIT_LOGGER_LEVEL=debug
```

### Check System Resources

```bash
# Check container resource usage
docker stats analytics-app

# Check disk space
docker system df
```

## Still Not Working?

1. **Check all logs:**
   ```bash
   docker-compose logs > all_logs.txt
   ```

2. **Verify Docker setup:**
   ```bash
   docker --version
   docker-compose --version
   ```

3. **Try accessing from container:**
   ```bash
   docker-compose exec app curl http://localhost:8501
   ```

4. **Check network:**
   ```bash
   docker network inspect analytics_analytics-network
   ```

## Contact & Support

If issues persist:
1. Save all logs: `docker-compose logs > logs.txt`
2. Check `APP_README.md` for configuration
3. Review `DOCKER_README.md` for Docker-specific issues

