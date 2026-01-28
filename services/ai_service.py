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

SYSTEM_ROUTER_PROMPT = """
You are the "Brain" of a restaurant analytics system. Your job is to classify the user's intent and route it to the correct tool.

AVAILABLE TOOLS:
1. `SQL_QUERY`: User is asking for data, numbers, lists, revenue etc. (e.g., "How many orders?", "Show me the menu").
2. `CHART_REQUEST`: User explicitly asks for a graph, chart, or visual trend (e.g., "Plot daily sales", "Pie chart of categories").
3. `GENERAL_CHAT`: Greetings, philosophical questions, or questions unrelated to the data.
4. `CLARIFICATION_NEEDED`: The user's request is too vague to answer (e.g., "Show me sales" without a timeframe or context).

OUTPUT FORMAT:
Return a JSON object:
{
    "intent": "SQL_QUERY" | "CHART_REQUEST" | "GENERAL_CHAT" | "CLARIFICATION_NEEDED",
    "reason": "Brief explanation",
    "required_params": ["date_range", "category"] // optional, only for clarification
}
"""

SQL_GENERATION_PROMPT = """
You are a SQLite expert for a restaurant analytics system.

## Context
- The database stores restaurant orders from PetPooja (a POS system).
- It tracks Customers, Menu Items, Orders, and Order Items.
- We have a specific focus on "Menu Clustering" (normalizing raw item names to clean menu items).
- All monetary amounts are in INR.
- The restaurant operates in IST (Asia/Kolkata timezone).

## Database Schema
{schema}

## STRATEGY:
- For Menu Items Analysis (Revenue/Sales by Item), prefer using `menu_items_summary_view` as it has pre-calculated `total_revenue`, `total_sold` etc.
- For detailed Order Analysis, join `orders` and `order_items`.

## CRITICAL COLUMN MAPPINGS (use these EXACT names):
| Concept | Use This Column | DO NOT USE |
|---------|-----------------|------------|
| Order Revenue | `orders.total` | 'amount', 'revenue', 'value' |
| Order Date | `orders.created_on` | `occurred_at` (has invalid values!) |
| Order ID | `orders.order_id` | `id` (does not exist) |
| Item Revenue | `order_items.total_price` | |
| Item Name (raw) | `order_items.name_raw` | |
| Menu Item ID | `menu_items.menu_item_id` | `id` (does not exist) |
| Menu Item Name | `menu_items.name` | |
| Category/Type | `menu_items.type` | |
| Order Source | `orders.order_from` | Values: 'Swiggy', 'Zomato', 'POS', 'Home Website' |

## RULES:
1. Return ONLY the SQL query. No markdown, no explanation, no backticks.
2. Use standard SQLite syntax (TEXT for UUIDs/Dates).
3. Dates are stored as TEXT 'YYYY-MM-DD HH:MM:SS'. 
   - Use `date(orders.created_on)` to extract date.
   - Use `strftime('%H', orders.created_on)` for hour.
4. Relative Date Logic:
   - 'today': `date(orders.created_on) = date('now', 'localtime')`
   - 'yesterday': `date(orders.created_on) = date('now', '-1 day', 'localtime')`
   - 'last X days': `orders.created_on >= date('now', '-X days', 'localtime')`
5. `order_items` and `order_item_addons` link to `menu_items` via `menu_item_id`.
6. Limit results to 100 rows unless specified otherwise.
7. NEVER use `occurred_at` - it contains invalid data.
8. NEVER use `created_at` - this is the system insertion time (technical metadata). 
   - ALWAYS use `created_on` - this is the actual Timestamp of Order Placement (Business Date).
9. ALWAYS filter by `orders.order_status = 'Success'` unless specified otherwise.
10. When filtering by Item Name, ALWAYS join 'order_items' with 'menu_items' and filter on 'menu_items.name'. NEVER filter on 'order_items.name_raw'.
11. To calculate "Total Sold" or "Revenue" for an item (which can be sold as a main item OR an add-on):
    - ✅ USE `UNION ALL` to combine results from `order_items` and `order_item_addons`.
    - ❌ DO NOT JOIN `order_items` directly to `order_item_addons`. This causes row explosion.
    - ⚠️ EACH subquery in the UNION must JOIN to `orders` independently if you need to filter by order date/status.
    - Example Pattern:
      ```
      SELECT menu_item_id, SUM(qty) as total_sold, SUM(rev) as total_revenue FROM (
          SELECT oi.menu_item_id, oi.quantity as qty, oi.total_price as rev
          FROM orders o JOIN order_items oi ON o.order_id = oi.order_id
          WHERE o.order_status = 'Success' AND o.created_on >= date('now', '-90 days', 'localtime')
          UNION ALL
          SELECT oia.menu_item_id, oia.quantity as qty, oia.price * oia.quantity as rev
          FROM orders o JOIN order_items oi ON o.order_id = oi.order_id
          JOIN order_item_addons oia ON oi.order_item_id = oia.order_item_id
          WHERE o.order_status = 'Success' AND o.created_on >= date('now', '-90 days', 'localtime')
      ) combined GROUP BY menu_item_id
      ```


## STANDARD BUSINESS DEFINITIONS:
- **Repeat Customer**: A customer who has placed > 1 successful order in their LIFETIME.
  - When matching "Repeat Customers in last 90 days", find customers active in last 90 days, then check their LIFETIME order count (total_orders > 1). Do NOT limit the "repeat" check to just the last 90 days.
- **Item Repeat Rate**: The % of unique customers who bought a specific item more than once in their LIFETIME.
  - Formula: (Count of Customers who bought Item X > 1 time ever) / (Total Unique Customers who bought Item X).

## COMMON MISTAKES TO AVOID:
- ❌ JOINing on `id` (e.g. `orders.id`, `menu_items.id`). THESE COLUMNS DO NOT EXIST.
- ✅ ALWAYS use explicit IDs: `orders.order_id`, `menu_items.menu_item_id`.
- ❌ Using `amount` or `revenue` columns.
- ✅ ALWAYS use `orders.total` or `order_items.total_price`.
- ❌ Using Postgres functions like `ILIKE`, `TIMESTAMPTZ`, `gen_random_uuid`. Use `LIKE` and standard SQLite functions.
- ❌ Filtering on `order_items.name_raw`. ALWAYS join with `menu_items` and use `menu_items.name`.
- ❌ Using `order_item_addons.total_price`. THIS COLUMN DOES NOT EXIST. Use `order_item_addons.price * order_item_addons.quantity` for add-on revenue.
"""

CHART_GENERATION_PROMPT = """
You are a SQLite expert and data visualization specialist for a restaurant analytics system.

## Context
- The database stores restaurant orders from PetPooja (a POS system).
- It tracks Customers, Menu Items, Orders, and Order Items.
- All monetary amounts are in INR.
- The restaurant operates in IST (Asia/Kolkata timezone).

## Database Schema
{schema}

## STRATEGY:
- For Menu Items Analysis, use `menu_items_summary_view` (cols: `total_revenue`, `total_sold`, `sold_as_item`, `sold_as_addon`).

## CRITICAL COLUMN MAPPINGS (use these EXACT names):
| Concept | Use This Column | DO NOT USE |
|---------|-----------------|------------|
| Order Revenue | `orders.total` | 'amount', 'revenue', 'value' |
| Order Date | `orders.created_on` | `occurred_at` (has invalid values!) |
| Order ID | `orders.order_id` | `orders.id` (does not exist) |
| Item Revenue | `order_items.total_price` | |
| Menu Item ID | `menu_items.menu_item_id` | `menu_items.id` (DOES NOT EXIST!) |
| Menu Item Name | `menu_items.name` | |
| Category/Type | `menu_items.type` | |
| Order Source | `orders.order_from` | Values: 'Swiggy', 'Zomato', 'POS', 'Home Website' |

## COMMON MISTAKES TO AVOID:
- ❌ JOINing on `menu_items.id` - THIS COLUMN DOES NOT EXIST!
- ✅ ALWAYS use `menu_items.menu_item_id` for JOINs
- ❌ JOINing on `orders.id` - THIS COLUMN DOES NOT EXIST!
- ✅ ALWAYS use `orders.order_id` for JOINs
- ❌ Using `orders.total` when grouping by Item or Category - THIS DUPLICATES REVENUE!
- ✅ ALWAYS use `order_items.total_price` OR `menu_items_summary_view.total_revenue` for Item/Category analysis
- ✅ ONLY use `orders.total` when grouping by Order-level attributes (Date, Source)

## YOUR TASK:
Generate a chart configuration with SQL query for the user's visualization request.

## RULES:
1. Dates: `date(orders.created_on)`.
2. 'today': `date(orders.created_on) = date('now', 'localtime')`
3. 'last X days': `orders.created_on >= date('now', '-X days', 'localtime')`
4. NEVER use `occurred_at` - it contains invalid data.
5. NEVER use `created_at` - this is system metadata. ALWAYS use `created_on` for business analysis.
6. Limit results to 20 rows for charts unless specified otherwise.
6. Always alias result columns to simple names like "label" and "value".
7. For JOINs: order_items.menu_item_id = menu_items.menu_item_id (NOT menu_items.id!)
8. ALWAYS filter by `orders.order_status = 'Success'` unless specified otherwise.

## OUTPUT FORMAT (JSON only, no markdown):
{{
    "chart_type": "bar" | "line" | "pie",
    "sql": "SELECT ... AS label, ... AS value FROM ...",
    "x_key": "label",
    "y_key": "value", 
    "title": "Human readable chart title"
}}

## CHART TYPE GUIDELINES:
- Use "bar" for comparisons (e.g., revenue by category, orders by source)
- Use "pie" for proportional data (e.g., % share by category)
- Use "line" for time series (e.g., daily revenue trend)
"""

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

