# Database Connection Troubleshooting

## Common Connection Errors

### Error: "Connection refused"

This means PostgreSQL is either:
- Not running
- Running on a different host/port
- Not accepting TCP/IP connections

---

## Step 1: Check if PostgreSQL is Running

### macOS
```bash
# Check if PostgreSQL is installed and running
brew services list | grep postgresql

# Start PostgreSQL if not running
brew services start postgresql

# Or check manually
ps aux | grep postgres
```

### Linux
```bash
# Check status
sudo systemctl status postgresql

# Start if not running
sudo systemctl start postgresql

# Check if port is listening
sudo netstat -tlnp | grep 5432
```

### Windows
- Open Services panel (services.msc)
- Look for "PostgreSQL" service
- Start it if it's stopped

---

## Step 2: Find Your PostgreSQL Connection Details

### Local PostgreSQL

**Default connection:**
- Host: `localhost` or `127.0.0.1`
- Port: `5432` (default)
- Database: `postgres` (default) or your database name
- User: Your system username or `postgres`
- Password: (if set)

**Find your PostgreSQL port:**
```bash
# macOS/Linux
psql -l  # Lists databases, shows connection info

# Or check config
cat /usr/local/var/postgres/postgresql.conf | grep port
```

### Cloud PostgreSQL (AWS RDS, Google Cloud SQL, etc.)

**Connection details:**
- Host: Your cloud database endpoint (e.g., `mydb.xxxxx.us-east-1.rds.amazonaws.com`)
- Port: Usually `5432` (check your cloud console)
- Database: Your database name
- User: Your database username
- Password: Your database password

**Example for AWS RDS:**
```
postgresql://username:password@mydb.xxxxx.us-east-1.rds.amazonaws.com:5432/analytics
```

### Docker PostgreSQL

If PostgreSQL is in Docker:
```bash
# Find container
docker ps | grep postgres

# Get connection details
docker inspect <container_id> | grep IPAddress
```

Connection string:
```
postgresql://user:password@localhost:5432/analytics
```
(Note: Port might be mapped differently, check `docker ps` output)

---

## Step 3: Test Connection

Use the test script:
```bash
# Test with connection URL
python3 database/test_connection.py --db-url "postgresql://user:pass@host:port/db"

# Test with individual parameters
python3 database/test_connection.py \
  --host localhost \
  --port 5432 \
  --database analytics \
  --user postgres \
  --password yourpassword
```

---

## Step 4: Common Connection String Formats

### Format 1: Full URL
```
postgresql://username:password@host:port/database
```

**Examples:**
```
postgresql://postgres:mypassword@localhost:5432/analytics
postgresql://user:pass@db.example.com:5432/analytics
postgresql://admin:secret@192.168.1.100:5432/analytics
```

### Format 2: With Special Characters in Password

If password contains special characters, URL encode them:
- `@` → `%40`
- `#` → `%23`
- `%` → `%25`
- `&` → `%26`
- `:` → `%3A`

**Example:**
```
# Password: "p@ss#word"
postgresql://user:p%40ss%23word@localhost:5432/analytics
```

### Format 3: Using Environment Variables

```bash
export PGHOST=localhost
export PGPORT=5432
export PGDATABASE=analytics
export PGUSER=postgres
export PGPASSWORD=yourpassword

# Then use psql
psql
```

---

## Step 5: Verify PostgreSQL is Accepting Connections

### Check PostgreSQL Config

**postgresql.conf:**
```conf
listen_addresses = '*'  # Should be '*' or 'localhost'
port = 5432
```

**pg_hba.conf:**
```
# Allow local connections
local   all             all                                     trust
host    all             all             127.0.0.1/32            md5
host    all             all             ::1/128                 md5
```

After changing config, restart PostgreSQL:
```bash
# macOS
brew services restart postgresql

# Linux
sudo systemctl restart postgresql
```

---

## Step 6: Alternative - Use psql Command Line

Test connection manually:
```bash
psql -h localhost -p 5432 -U postgres -d analytics
```

If this works, use the same parameters in the Python script.

---

## Step 7: If Using Cloud Database

### AWS RDS
1. Check Security Groups - ensure port 5432 is open
2. Check if your IP is whitelisted
3. Use the RDS endpoint as host

### Google Cloud SQL
1. Enable Cloud SQL Admin API
2. Use Cloud SQL Proxy or whitelist IP
3. Connection string format: `postgresql://user:pass@/database?host=/cloudsql/project:region:instance`

### Heroku Postgres
```bash
# Get connection string
heroku config:get DATABASE_URL

# Use directly
python3 database/test_load_menu_postgresql.py --db-url "$(heroku config:get DATABASE_URL)"
```

---

## Quick Diagnostic Commands

```bash
# Check if PostgreSQL is running
pg_isready -h localhost -p 5432

# List all databases
psql -h localhost -U postgres -l

# Test connection
psql -h localhost -p 5432 -U postgres -d analytics -c "SELECT version();"
```

---

## Still Having Issues?

1. **Check firewall** - Ensure port 5432 is not blocked
2. **Check PostgreSQL logs** - Look for connection errors
3. **Try different host** - Use `127.0.0.1` instead of `localhost`
4. **Check SSL requirements** - Some cloud databases require SSL
5. **Verify credentials** - Double-check username and password

---

*Last Updated: January 9, 2026*

