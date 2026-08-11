import os
import sqlite3
from pathlib import Path
from src.core.db.connection import apply_analytics_schema
from src.core.profiles import PROFILE_SCHEMA_VERSION


def reset_database(profile):
    """
    Resets the database by:
    1. Removing the analytics.db file (if exists)
    2. Creating a new connection (creates file)
    3. Executing schema_sqlite.sql
    4. Leaves catalog empty — next Sync DB pulls from cloud when configured
    """
    try:
        # Delete exactly the captured profile database, then recreate its identity.
        target_db = Path(profile.database_path).resolve()
        if target_db.exists():
            os.remove(target_db)
            print(f"Deleted database at {target_db}")
            
        # 2. Create new connection
        conn = sqlite3.connect(str(target_db), check_same_thread=False, timeout=30.0)
        apply_analytics_schema(conn)
        conn.execute(
            """
            INSERT INTO restaurant_profile_identity
                (singleton_id, restaurant_id, bound_at, profile_schema_version)
            VALUES (1, ?, CURRENT_TIMESTAMP, ?)
            """,
            (profile.restaurant_id, PROFILE_SCHEMA_VERSION),
        )
             
        conn.commit()
        conn.close()

        return True, "Database reset successfully. Run Sync DB to pull catalog from cloud."

    except Exception as e:
        return False, str(e)
