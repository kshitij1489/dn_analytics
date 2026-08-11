def bind_test_profile(conn, restaurant_id: str = "test-restaurant") -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS restaurant_profile_identity (
            singleton_id INTEGER PRIMARY KEY CHECK (singleton_id = 1),
            restaurant_id TEXT NOT NULL UNIQUE,
            bound_at TEXT NOT NULL,
            profile_schema_version INTEGER NOT NULL
        )
        """
    )
    conn.execute(
        """
        INSERT OR REPLACE INTO restaurant_profile_identity
            (singleton_id, restaurant_id, bound_at, profile_schema_version)
        VALUES (1, ?, CURRENT_TIMESTAMP, 1)
        """,
        (restaurant_id,),
    )
    conn.commit()
