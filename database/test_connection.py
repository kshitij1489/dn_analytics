"""
Test Database Connection

This script helps diagnose database connection issues.

Usage:
    python3 database/test_connection.py --db-url "postgresql://user:pass@host:port/db"
"""

import sys
import argparse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

try:
    import psycopg2
    PSYCOPG2_AVAILABLE = True
except ImportError:
    PSYCOPG2_AVAILABLE = False
    print("❌ psycopg2 not installed. Install with: pip install psycopg2-binary")
    sys.exit(1)


def test_connection(db_url=None, host=None, port=None, database=None, user=None, password=None):
    """Test database connection"""
    print("=" * 80)
    print("Testing Database Connection")
    print("=" * 80)
    
    try:
        if db_url:
            print(f"\nAttempting connection with URL...")
            print(f"  (URL format: postgresql://user:password@host:port/database)")
            conn = psycopg2.connect(db_url)
            print(f"  ✓ Connection successful!")
        else:
            print(f"\nAttempting connection with parameters...")
            print(f"  Host: {host}")
            print(f"  Port: {port}")
            print(f"  Database: {database}")
            print(f"  User: {user}")
            conn = psycopg2.connect(
                host=host,
                port=port,
                database=database,
                user=user,
                password=password
            )
            print(f"  ✓ Connection successful!")
        
        # Test query
        cursor = conn.cursor()
        cursor.execute("SELECT version();")
        version = cursor.fetchone()[0]
        print(f"\n  PostgreSQL Version: {version}")
        
        # Check if tables exist
        cursor.execute("""
            SELECT table_name 
            FROM information_schema.tables 
            WHERE table_schema = 'public'
            ORDER BY table_name
        """)
        tables = cursor.fetchall()
        
        if tables:
            print(f"\n  Existing tables in database:")
            for table in tables:
                print(f"    - {table[0]}")
        else:
            print(f"\n  No tables found in database (schema needs to be created)")
        
        conn.close()
        print(f"\n✅ Connection test passed!")
        return True
        
    except psycopg2.OperationalError as e:
        print(f"\n❌ Connection Error: {e}")
        print(f"\nTroubleshooting:")
        print(f"  1. Is PostgreSQL running?")
        print(f"     - macOS: brew services list | grep postgresql")
        print(f"     - Linux: sudo systemctl status postgresql")
        print(f"     - Windows: Check Services panel")
        print(f"  2. Is the port correct? (default: 5432)")
        print(f"  3. Is the database name correct?")
        print(f"  4. Are the credentials correct?")
        print(f"  5. Is PostgreSQL accepting TCP/IP connections?")
        print(f"     - Check postgresql.conf: listen_addresses = '*'")
        print(f"     - Check pg_hba.conf: host all all 0.0.0.0/0 md5")
        return False
        
    except psycopg2.Error as e:
        print(f"\n❌ Database Error: {e}")
        return False


def main():
    parser = argparse.ArgumentParser(description='Test database connection')
    parser.add_argument('--db-url', help='PostgreSQL connection URL')
    parser.add_argument('--host', default='localhost', help='Database host')
    parser.add_argument('--port', type=int, default=5432, help='Database port')
    parser.add_argument('--database', help='Database name')
    parser.add_argument('--user', help='Database user')
    parser.add_argument('--password', help='Database password')
    args = parser.parse_args()
    
    if not PSYCOPG2_AVAILABLE:
        return
    
    if args.db_url:
        success = test_connection(db_url=args.db_url)
    elif args.database and args.user:
        success = test_connection(
            host=args.host,
            port=args.port,
            database=args.database,
            user=args.user,
            password=args.password
        )
    else:
        print("❌ ERROR: Provide either --db-url or --database + --user")
        print("\nUsage examples:")
        print("  python3 database/test_connection.py --db-url 'postgresql://user:pass@host:port/db'")
        print("  python3 database/test_connection.py --host localhost --database analytics --user postgres --password pass")
        return
    
    if success:
        print("\n" + "=" * 80)
        print("Next Steps:")
        print("=" * 80)
        print("1. If connection works, run the menu loading script:")
        if args.db_url:
            print(f"   python3 database/test_load_menu_postgresql.py --db-url '{args.db_url}'")
        else:
            print(f"   python3 database/test_load_menu_postgresql.py --host {args.host} --port {args.port} --database {args.database} --user {args.user} --password {args.password}")
    else:
        print("\n" + "=" * 80)
        print("Connection Failed - Please fix the connection issue first")
        print("=" * 80)


if __name__ == "__main__":
    main()

