from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Dict, Any, List
import sqlite3
import os

router = APIRouter()

# Use DB_URL env var (set by Electron main.js) or fallback to cwd
DB_PATH = os.environ.get('DB_URL') or os.path.join(os.getcwd(), 'analytics.db')

class ConfigUpdate(BaseModel):
    settings: Dict[str, str]

def get_db_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

@router.get("/")
def get_config():
    """Get all configuration settings"""
    conn = get_db_connection()
    try:
        # First ensure table exists (idempotent for fresh dbs)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS system_config (
                key TEXT PRIMARY KEY,
                value TEXT,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
        """)
        
        cursor = conn.execute("SELECT key, value FROM system_config")
        rows = cursor.fetchall()
        
        config = {row['key']: row['value'] for row in rows}
        return config
    except Exception as e:
        print(f"Error fetching config: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        conn.close()

class ConfigVerification(BaseModel):
    type: str  # 'openai', 'orders'
    settings: Dict[str, str]

@router.post("/verify")
def verify_config(data: ConfigVerification):
    """Verify configuration settings by attempting a connection"""
    
    if data.type == "openai":
        api_key = data.settings.get("openai_api_key")
        if not api_key:
            raise HTTPException(status_code=400, detail="Missing API Key")
        
        try:
            import openai
            client = openai.OpenAI(api_key=api_key)
            # minimal call to check auth
            client.models.list(limit=1) 
            return {"status": "success", "message": "✅ OpenAI Connection Successful!"}
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Connection Failed: {str(e)}")

    elif data.type == "orders":
        url = data.settings.get("integration_orders_url")
        key = data.settings.get("integration_orders_key")
        
        if not url or not key:
            raise HTTPException(status_code=400, detail="Missing URL or Key")
            
        try:
            import requests
            # Clean URL
            base_url = url.rstrip('/')
            # Try a lightweight request (limit=1) to check auth
            resp = requests.get(
                f"{base_url}/orders/", 
                headers={
                    "X-API-Key": key
                }, 
                params={"limit": 1},
                timeout=10
            )
            resp.raise_for_status()
            return {"status": "success", "message": "✅ Orders Integration Connected!"}
        except Exception as e:
             raise HTTPException(status_code=400, detail=f"Connection Failed: {str(e)}")
             
    else:
        raise HTTPException(status_code=400, detail="Unknown verification type")

@router.post("/")
def update_config(data: ConfigUpdate):
    """Update configuration settings (Upsert)"""
    conn = get_db_connection()
    try:
        # Ensure table exists
        conn.execute("""
            CREATE TABLE IF NOT EXISTS system_config (
                key TEXT PRIMARY KEY,
                value TEXT,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
        """)
        
        for key, value in data.settings.items():
            conn.execute("""
                INSERT INTO system_config (key, value, updated_at) 
                VALUES (?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(key) DO UPDATE SET 
                    value=excluded.value,
                    updated_at=CURRENT_TIMESTAMP
            """, (key, value))
        
        conn.commit()
        return {"status": "success", "message": "Configuration updated"}
    except Exception as e:
        print(f"Error updating config: {e}")
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        conn.close()
