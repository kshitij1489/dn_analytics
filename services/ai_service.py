import os
from functools import lru_cache
import openai
from typing import Dict, Any, List, Optional
from datetime import datetime
import pandas as pd
import json
import uuid
from dotenv import load_dotenv

from src.api.models import AIResponse
from src.api.utils import df_to_json

# Load environment variables (legacy support or other vars)
# load_dotenv()

def get_ai_client(conn):
    """Fetch API Key from DB or specific ENV fallback"""
    try:
        # Check DB first
        cursor = conn.execute("SELECT value FROM system_config WHERE key = 'openai_api_key'")
        row = cursor.fetchone()
        if row and row[0]:
            return openai.OpenAI(api_key=row[0])
    except Exception as e:
        print(f"Error fetching API key from DB: {e}")
    
    return None

def get_ai_model(conn):
    """Fetch Model Name from DB or default to gpt-4o"""
    try:
        cursor = conn.execute("SELECT value FROM system_config WHERE key = 'openai_model'")
        row = cursor.fetchone()
        if row and row[0]:
            return row[0]
    except Exception as e:
        print(f"Error fetchingAI model from DB: {e}")
    return "gpt-4o"

def log_interaction(conn, query: str, intent: str, response: AIResponse, sql: str = None, error: str = None):
    """Log the AI interaction to the database"""
    try:
        log_id = str(uuid.uuid4())
        
        # Prepare payload
        payload = None
        if isinstance(response.content, (dict, list)):
            payload = json.dumps(response.content)
        elif isinstance(response.content, str):
            payload = json.dumps({"text": response.content})
            
        # SQLite uses :name binding
        query_sql = """
            INSERT INTO ai_logs 
            (log_id, user_query, intent, sql_generated, response_type, response_payload, error_message, created_at)
            VALUES (:log_id, :query, :intent, :sql, :type, :payload, :error, datetime('now'))
        """
        conn.execute(query_sql, {
            "log_id": log_id,
            "query": query,
            "intent": intent,
            "sql": sql,
            "type": response.type,
            "payload": payload,
            "error": error
        })
        conn.commit()
        
        return log_id
    except Exception as e:
        print(f"❌ Error logging interaction: {str(e)}")
        return None

@lru_cache(maxsize=1)
def get_schema_context():
    """Read the base schema from the SQL file"""
    try:
        # Assuming schema_sqlite.sql is in the standard location relative to this file
        schema_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../database/schema_sqlite.sql"))
        with open(schema_path, "r") as f:
            return f.read()
    except Exception as e:
        return f"Error reading schema: {str(e)}"

from .prompts import SYSTEM_ROUTER_PROMPT, SQL_GENERATION_PROMPT, CHART_GENERATION_PROMPT

def classify_intent(conn, prompt: str, history: List[Dict] = None) -> Dict[str, Any]:
    """Classify the user's intent using LLM"""
    client = get_ai_client(conn)
    model = get_ai_model(conn)
    if not client:
        return {"intent": "GENERAL_CHAT", "reason": "No API key config"}

    try:
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": SYSTEM_ROUTER_PROMPT},
                {"role": "user", "content": prompt}
            ],
            temperature=0,
            response_format={"type": "json_object"}
        )
        return json.loads(response.choices[0].message.content)
    except Exception as e:
        print(f"❌ Error in classify_intent: {str(e)}")
        # Fallback to general chat if classification fails
        return {"intent": "GENERAL_CHAT", "reason": f"Error: {str(e)}"}

def generate_sql(conn, prompt: str) -> str:
    """Generate SQL from natural language"""
    client = get_ai_client(conn)
    model = get_ai_model(conn)
    if not client:
        return "ERROR: API Key Missing"

    schema = get_schema_context()
    today = datetime.now().strftime('%Y-%m-%d')
    
    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": SQL_GENERATION_PROMPT.format(schema=schema, today=today)},
            {"role": "user", "content": prompt}
        ],
        temperature=0
    )
    
    sql = response.choices[0].message.content.strip()
    # Clean markdown
    if sql.startswith("```sql"):
        sql = sql.replace("```sql", "", 1).replace("```", "", 1)
    elif sql.startswith("```"):
        sql = sql.replace("```", "", 1).replace("```", "", 1)
        
    return sql.strip()

def generate_explanation(conn, prompt: str, sql: str, df: pd.DataFrame) -> str:
    """Explain the results in simple terms"""
    client = get_ai_client(conn)
    model = get_ai_model(conn)
    if not client:
        return "Here are the results."
        
    summary_prompt = f"""
    The user asked: "{prompt}"
    We ran this SQL: "{sql}"
    We got {len(df)} rows of data.
    
    Please explain the result briefly in 1-2 bullet points. Highlight the key insight if possible (e.g. "Total revenue is X").
    """
    
    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "user", "content": summary_prompt}
        ],
        temperature=0.5
    )
    
    return response.choices[0].message.content.strip()

def generate_chart_config(conn, prompt: str) -> Dict[str, Any]:
    """Generate chart configuration and fetch data for visualization"""
    client = get_ai_client(conn)
    model = get_ai_model(conn)
    if not client:
        return {"error": "API Key Missing"}

    schema = get_schema_context()
    today = datetime.now().strftime('%Y-%m-%d')
    
    try:
        response = client.chat.completions.create(
        model=model,
            messages=[
                {"role": "system", "content": CHART_GENERATION_PROMPT.format(schema=schema, today=today)},
                {"role": "user", "content": prompt}
            ],
            temperature=0,
            response_format={"type": "json_object"}
        )
        
        config = json.loads(response.choices[0].message.content)
        
        # Execute the SQL to get data
        sql_query = config.get("sql", "")
        df = pd.read_sql_query(sql_query, conn)
        
        return {
            "chart_type": config.get("chart_type", "bar"),
            "data": df_to_json(df),
            "x_key": config.get("x_key", "label"),
            "y_key": config.get("y_key", "value"),
            "title": config.get("title", "Chart"),
            "sql_query": sql_query
        }
    except Exception as e:
        print(f"❌ Error in generate_chart_config: {str(e)}")
        return {"error": str(e)}

async def process_chat(prompt: str, conn, history: List[Dict] = None) -> AIResponse:
    """Main Orchestrator"""
    
    # 1. Classify Intent
    classification = classify_intent(conn, prompt, history)
    intent = classification.get("intent", "GENERAL_CHAT")
    
    if intent == "SQL_QUERY":
        # Generate SQL
        sql_query = generate_sql(conn, prompt)
        
        # Execute SQL
        try:
            df = pd.read_sql_query(sql_query, conn)
            # Explain Results
            explanation = generate_explanation(conn, prompt, sql_query, df)
            
            ai_resp = AIResponse(
                type="table",
                content=df_to_json(df),
                explanation=explanation,
                sql_query=sql_query
            )
            # Log successful query
            ai_resp.log_id = log_interaction(conn, prompt, intent, ai_resp, sql_query)
            return ai_resp
        except Exception as e:
            ai_resp = AIResponse(
                type="text",
                content=f"I tried to query the database but encountered an error: {str(e)}",
                sql_query=sql_query,
                confidence=0.0
            )
            # Log failed query
            ai_resp.log_id = log_interaction(conn, prompt, intent, ai_resp, sql_query, str(e))
            return ai_resp

    elif intent == "CHART_REQUEST":
        chart_config = generate_chart_config(conn, prompt)
        
        if "error" in chart_config:
            ai_resp = AIResponse(
                type="text",
                content=f"I couldn't generate the chart: {chart_config['error']}",
                confidence=0.0
            )
            # Log failed chart
            ai_resp.log_id = log_interaction(conn, prompt, intent, ai_resp, error=chart_config['error'])
            return ai_resp
        
        ai_resp = AIResponse(
            type="chart",
            content=chart_config,
            sql_query=chart_config.get("sql_query"),
            confidence=1.0
        )
        # Log successful chart
        ai_resp.log_id = log_interaction(conn, prompt, intent, ai_resp, chart_config.get("sql_query"))
        return ai_resp
        
    elif intent == "CLARIFICATION_NEEDED":
        ai_resp = AIResponse(
            type="text",
            content=f"I need a bit more info: {classification.get('reason')}",
            confidence=0.5
        )
        # Log clarification
        ai_resp.log_id = log_interaction(conn, prompt, intent, ai_resp)
        return ai_resp
        
    else: # GENERAL_CHAT
        client = get_ai_client(conn)
        model = get_ai_model(conn)
        if client:
            response = client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": prompt}],
            )
            content = response.choices[0].message.content
        else:
            content = "AI not configured. Please add an API Key in Configuration."

        ai_resp = AIResponse(
            type="text",
            content=content,
            confidence=1.0
        )
        # Log general chat
        ai_resp.log_id = log_interaction(conn, prompt, intent, ai_resp)
        return ai_resp

