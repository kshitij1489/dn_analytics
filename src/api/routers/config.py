from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Dict, Optional

import os

router = APIRouter()

# Use DB_URL env var (set by Electron main.js) or fallback to cwd
DB_PATH = os.environ.get('DB_URL') or os.path.join(os.getcwd(), 'analytics.db')

class ConfigUpdate(BaseModel):
    settings: Dict[str, str]

from src.core.db.connection import get_db_connection

@router.get("/")
def get_config():
    """Get all configuration settings"""
    conn, _ = get_db_connection()
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
            # minimal call to check auth (list models; no limit param in SDK)
            client.models.list()
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
    conn, _ = get_db_connection()
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

@router.post("/reset-db")
def reset_db_section(data: Dict[str, str]):
    """Placeholder for resetting a specific database section"""
    section = data.get("section", "")
    if not section:
        raise HTTPException(status_code=400, detail="Missing section parameter")

    conn, _ = get_db_connection()
    try:
        if section == "orders":
            # 1. Reset Orders Section
            # Tables: restaurants, customers, orders, order_taxes, order_discounts, order_items, order_item_addons
            
            # Use PRAGMA foreign_keys = OFF to allow truncation in any order, 
            # or delete in dependency order (leafs first).
            # Dependency order: add-ons -> items -> taxes/discounts -> orders -> customers/restaurants
            
            tables_to_clear = [
                # Orders Tables
                "order_item_addons",
                "order_items",
                "order_taxes",
                "order_discounts",
                "orders",
                "customers",
                "restaurants",
                # Menu Tables
                "menu_item_variants",
                "menu_items",
                "variants",
                # History
                "merge_history"
            ]
            
            for table in tables_to_clear:
                conn.execute(f"DELETE FROM {table}")
                # Optional: Reset sequence
                conn.execute("DELETE FROM sqlite_sequence WHERE name=?", (table,))
                
            conn.commit()
            
            # Re-seed menu from backups if available
            from scripts.seed_from_backups import perform_seeding
            seed_status = "Data cleared."
            try:
                if perform_seeding(conn):
                    seed_status += " Menu re-seeded from backups."
                else:
                    seed_status += " Menu seeding skipped (no backups)."
            except Exception as e:
                seed_status += f" Seeding failed: {str(e)}"
                
            return {"status": "success", "message": f"Successfully reset 'Orders' and 'Menu' database. {seed_status}"}


        elif section == "integrations":
            # 3. Reset Integrations Section
            # Clears all keys starting with 'integration_' from system_config
            
            conn.execute("DELETE FROM system_config WHERE key LIKE 'integration_%'")
            conn.commit()
            return {"status": "success", "message": "Successfully reset all integration settings."}

        elif section == "ai_models":
            # 4. Reset AI Models Section
            # Clears keys for OpenAI, Anthropic, Gemini
            # Keys: openai_api_key, openai_model, anthropic_api_key, anthropic_model, gemini_api_key, gemini_model
            # Pattern: openai_%, anthropic_%, gemini_%
            
            conn.execute("DELETE FROM system_config WHERE key LIKE 'openai_%' OR key LIKE 'anthropic_%' OR key LIKE 'gemini_%'")
            conn.commit()
            return {"status": "success", "message": "Successfully reset all AI Model settings."}

        elif section == "ai mode":
            # 5. Reset AI Mode Section (Database)
            # Tables: ai_logs, ai_feedback, ai_conversations, ai_messages
            # Dependency: ai_feedback -> ai_logs; ai_messages -> ai_conversations + ai_logs
            
            tables_to_clear = [
                "ai_feedback",
                "ai_messages",
                "ai_conversations",
                "ai_logs"
            ]
            
            for table in tables_to_clear:
                conn.execute(f"DELETE FROM {table}")
                conn.execute("DELETE FROM sqlite_sequence WHERE name=?", (table,))
                
            conn.commit()
            return {"status": "success", "message": "Successfully reset 'AI Mode' database (Logs & Conversations)."}

        else:
            # Placeholder for other sections
            print(f"DEBUG: Placeholder reset triggered for section: {section}")
            return {"status": "success", "message": f"Database section '{section}' reset successfully (Placeholder)"}

    except Exception as e:
        print(f"Error resetting DB section {section}: {e}")
        conn.rollback()
        raise HTTPException(status_code=500, detail=f"Failed to reset {section}: {str(e)}")
    finally:
        conn.close()

# --- User Management ---

class User(BaseModel):
    name: str
    employee_id: str
    is_active: bool = True

@router.get("/users")
def get_users():
    """Get list of application users (Singleton). Migration runs at startup in main.py."""
    conn, _ = get_db_connection()
    try:
        cursor = conn.execute("SELECT name, employee_id, is_active, created_at FROM app_users LIMIT 1")
        rows = cursor.fetchall()
        return [dict(row) for row in rows]
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        conn.close()

@router.post("/users")
def save_user(user: User):
    """Update current user profile (Singleton: Wipes and Replaces)"""
    conn, _ = get_db_connection()
    try:
        # Strict Singleton: Reset table and insert new profile
        # Transaction ensures we don't end up with 0 rows if insert fails
        conn.execute("DELETE FROM app_users")
        
        conn.execute("""
            INSERT INTO app_users (name, employee_id, is_active)
            VALUES (?, ?, ?)
        """, (user.name, user.employee_id, user.is_active))
            
        conn.commit()
        return {"status": "success", "message": "Profile updated successfully"}
    except HTTPException:
        raise
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        conn.close()

