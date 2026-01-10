# PostgreSQL Local Setup Guide (macOS)

This guide helps you install and set up PostgreSQL locally on macOS.

---

## Quick Setup (Automated)

Run the setup script:

```bash
./database/setup_postgresql.sh
```

This will:
- Install PostgreSQL via Homebrew
- Initialize the database
- Start the PostgreSQL service
- Create the `analytics` database
- Test the connection

---

## Manual Setup

### Step 1: Install Homebrew (if not installed)

```bash
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
```

### Step 2: Install PostgreSQL

```bash
brew install postgresql@16
```

Or install the latest version:
```bash
brew install postgresql
```

### Step 3: Initialize Database (if needed)

PostgreSQL usually initializes automatically, but if needed:

```bash
# For Intel Macs
initdb /usr/local/var/postgres

# For Apple Silicon Macs
initdb /opt/homebrew/var/postgresql@16
```

### Step 4: Start PostgreSQL Service

```bash
# Start PostgreSQL
brew services start postgresql@16

# Or if you installed just "postgresql"
brew services start postgresql
```

### Step 5: Verify PostgreSQL is Running

```bash
# Check service status
brew services list | grep postgresql

# Test connection
psql -d postgres -c "SELECT version();"
```

### Step 6: Create Analytics Database

```bash
createdb analytics
```

### Step 7: Test Connection

```bash
# Connect to database
psql -d analytics

# You should see: analytics=#
# Type \q to exit
```

---

## Connection Details

After setup, use these connection details:

**Connection URL:**
```
postgresql://your_username@localhost:5432/analytics
```

**Individual Parameters:**
- Host: `localhost`
- Port: `5432`
- Database: `analytics`
- User: Your macOS username (usually no password needed)

---

## Common Commands

```bash
# Start PostgreSQL
brew services start postgresql@16

# Stop PostgreSQL
brew services stop postgresql@16

# Restart PostgreSQL
brew services restart postgresql@16

# Check status
brew services list | grep postgresql

# Connect to database
psql -d analytics

# List all databases
psql -l

# Drop database (if needed)
dropdb analytics
```

---

## Troubleshooting

### PostgreSQL won't start

**Check if port 5432 is in use:**
```bash
lsof -i :5432
```

**Kill process if needed:**
```bash
kill -9 <PID>
```

**Check logs:**
```bash
# For Intel Macs
tail -f /usr/local/var/log/postgres.log

# For Apple Silicon Macs
tail -f /opt/homebrew/var/log/postgres.log
```

### "command not found: psql"

Add PostgreSQL to your PATH:

```bash
# For Intel Macs
echo 'export PATH="/usr/local/opt/postgresql@16/bin:$PATH"' >> ~/.zshrc
source ~/.zshrc

# For Apple Silicon Macs
echo 'export PATH="/opt/homebrew/opt/postgresql@16/bin:$PATH"' >> ~/.zshrc
source ~/.zshrc
```

### Permission Denied

If you get permission errors, you may need to create a PostgreSQL user:

```bash
psql postgres
```

Then in psql:
```sql
CREATE USER your_username WITH SUPERUSER;
ALTER USER your_username WITH PASSWORD 'your_password';
\q
```

---

## Next Steps

Once PostgreSQL is running:

1. **Test Connection:**
   ```bash
   python3 database/test_connection.py --db-url "postgresql://$(whoami)@localhost:5432/analytics"
   ```

2. **Load Menu Data:**
   ```bash
   python3 database/test_load_menu_postgresql.py --db-url "postgresql://$(whoami)@localhost:5432/analytics"
   ```

---

*Last Updated: January 9, 2026*

