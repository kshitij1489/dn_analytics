"""
AI Mode: chart configuration and data generation from natural language.
"""

import json
from typing import Dict, Any

import pandas as pd

from src.api.utils import df_to_json
from ai_mode.client import get_ai_client, get_ai_model
from ai_mode.prompt_ai_mode import CHART_GENERATION_PROMPT
from ai_mode.schema import get_schema_context


def generate_chart_config(conn, prompt: str) -> Dict[str, Any]:
    """Generate chart configuration and fetch data for visualization."""
    client = get_ai_client(conn)
    model = get_ai_model(conn)
    if not client:
        return {"error": "API Key Missing"}

    schema = get_schema_context()

    try:
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": CHART_GENERATION_PROMPT.format(schema=schema)},
                {"role": "user", "content": prompt}
            ],
            temperature=0,
            response_format={"type": "json_object"}
        )
        config = json.loads(response.choices[0].message.content)
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
