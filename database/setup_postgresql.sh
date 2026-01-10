#!/bin/bash

# PostgreSQL Setup Script for macOS
# This script installs and sets up PostgreSQL locally

set -e  # Exit on error

echo "================================================================================"
echo "PostgreSQL Local Setup for macOS"
echo "================================================================================"

# Check if Homebrew is installed
if ! command -v brew &> /dev/null; then
    echo "❌ Homebrew is not installed."
    echo "   Install it from: https://brew.sh"
    echo "   Then run: /bin/bash -c \"\$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)\""
    exit 1
fi

echo "✓ Homebrew is installed"

# Check if PostgreSQL is already installed
if brew list postgresql@14 &> /dev/null || brew list postgresql@15 &> /dev/null || brew list postgresql@16 &> /dev/null; then
    echo "✓ PostgreSQL is already installed"
    POSTGRES_INSTALLED=true
else
    echo "📦 Installing PostgreSQL..."
    brew install postgresql@16
    POSTGRES_INSTALLED=false
fi

# Find which version is installed
if brew list postgresql@16 &> /dev/null; then
    POSTGRES_VERSION=16
    POSTGRES_DIR="/opt/homebrew/var/postgresql@16"
elif brew list postgresql@15 &> /dev/null; then
    POSTGRES_VERSION=15
    POSTGRES_DIR="/opt/homebrew/var/postgresql@15"
elif brew list postgresql@14 &> /dev/null; then
    POSTGRES_VERSION=14
    POSTGRES_DIR="/opt/homebrew/var/postgresql@14"
else
    echo "❌ Could not determine PostgreSQL version"
    exit 1
fi

echo "✓ Using PostgreSQL $POSTGRES_VERSION"

# Initialize database if not already initialized
if [ ! -d "$POSTGRES_DIR" ]; then
    echo "📦 Initializing PostgreSQL database..."
    initdb "$POSTGRES_DIR"
    echo "✓ Database initialized"
else
    echo "✓ Database already initialized"
fi

# Start PostgreSQL service
echo "🚀 Starting PostgreSQL service..."
brew services start postgresql@$POSTGRES_VERSION

# Wait a moment for service to start
sleep 2

# Check if service is running
if brew services list | grep -q "postgresql@$POSTGRES_VERSION.*started"; then
    echo "✓ PostgreSQL service is running"
else
    echo "⚠️  PostgreSQL service may not be running. Trying to start..."
    brew services restart postgresql@$POSTGRES_VERSION
    sleep 2
fi

# Create analytics database
echo "📦 Creating 'analytics' database..."
createdb analytics 2>/dev/null || echo "   Database 'analytics' already exists (this is OK)"

# Test connection
echo "🧪 Testing connection..."
if psql -d analytics -c "SELECT version();" &> /dev/null; then
    echo "✓ Connection test successful!"
else
    echo "⚠️  Connection test failed. You may need to set up a user."
fi

echo ""
echo "================================================================================"
echo "✅ PostgreSQL Setup Complete!"
echo "================================================================================"
echo ""
echo "Connection Details:"
echo "  Host: localhost"
echo "  Port: 5432"
echo "  Database: analytics"
echo "  User: $(whoami) (your macOS username)"
echo "  Password: (none by default)"
echo ""
echo "Connection URL:"
echo "  postgresql://$(whoami)@localhost:5432/analytics"
echo ""
echo "Useful Commands:"
echo "  Start PostgreSQL:  brew services start postgresql@$POSTGRES_VERSION"
echo "  Stop PostgreSQL:   brew services stop postgresql@$POSTGRES_VERSION"
echo "  Check status:      brew services list | grep postgresql"
echo "  Connect to DB:     psql -d analytics"
echo ""
echo "Next Steps:"
echo "  1. Test connection:"
echo "     python3 database/test_connection.py --db-url 'postgresql://$(whoami)@localhost:5432/analytics'"
echo ""
echo "  2. Load menu data:"
echo "     python3 database/test_load_menu_postgresql.py --db-url 'postgresql://$(whoami)@localhost:5432/analytics'"
echo ""

