"""
Database Configuration Example

Copy this file to db_config.py and fill in your database credentials.
Then import it in your scripts.

Usage:
    from database.db_config import get_db_connection
    
    conn = get_db_connection()
"""

# PostgreSQL Configuration
DB_CONFIG = {
    'host': 'localhost',
    'port': 5432,
    'database': 'analytics',
    'user': 'postgres',
    'password': 'your_password_here'
}

# Or use connection URL
# DB_URL = "postgresql://username:password@localhost:5432/analytics"

def get_db_connection():
    """
    Get database connection using configuration.
    
    Returns:
        psycopg2 connection object
    """
    import psycopg2
    
    # Option 1: Use individual parameters
    return psycopg2.connect(
        host=DB_CONFIG['host'],
        port=DB_CONFIG['port'],
        database=DB_CONFIG['database'],
        user=DB_CONFIG['user'],
        password=DB_CONFIG['password']
    )
    
    # Option 2: Use connection URL (uncomment if using DB_URL)
    # return psycopg2.connect(DB_URL)

