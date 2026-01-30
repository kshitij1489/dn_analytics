"""
AI Mode: OpenAI client and model configuration from DB.
"""

import openai


def get_ai_client(conn):
    """Fetch API Key from DB or specific ENV fallback."""
    try:
        cursor = conn.execute("SELECT value FROM system_config WHERE key = 'openai_api_key'")
        row = cursor.fetchone()
        if row and row[0]:
            return openai.OpenAI(api_key=row[0])
    except Exception as e:
        print(f"Error fetching API key from DB: {e}")
    return None


def get_ai_model(conn):
    """Fetch Model Name from DB or default to gpt-4o."""
    try:
        cursor = conn.execute("SELECT value FROM system_config WHERE key = 'openai_model'")
        row = cursor.fetchone()
        if row and row[0]:
            return row[0]
    except Exception as e:
        print(f"Error fetching AI model from DB: {e}")
    return "gpt-4o"
