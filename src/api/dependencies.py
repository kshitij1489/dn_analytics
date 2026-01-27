"""
Shared Dependencies for FastAPI Routers

This module contains common dependencies used across multiple routers to avoid duplication.
"""

from fastapi import HTTPException
from src.core.db.connection import get_db_connection


def get_db():
    """
    Database connection dependency for FastAPI routes.
    
    Yields a database connection and ensures it's properly closed after use.
    Raises HTTPException 500 if connection fails.
    
    Usage:
        @router.get("/endpoint")
        def my_endpoint(conn = Depends(get_db)):
            # use conn here
    """
    conn, err = get_db_connection()
    if conn is None:
        raise HTTPException(status_code=500, detail=f"Database connection failed: {err}")
    try:
        yield conn
    finally:
        conn.close()
