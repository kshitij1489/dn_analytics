"""
AI Mode: chart configuration and data generation from natural language.
Config (chart_type, x_key, y_key, title, sql_query) is cached; SQL is re-run on
cache hit so chart data is always fresh. See docs/LLM_CACHE_PLAN.md.
"""

import json
from typing import Dict, Any

import pandas as pd

from src.api.utils import df_to_json
from ai_mode.cache import get, normalize_prompt, cache_set
from ai_mode.llm.client import get_ai_client, get_ai_model
from ai_mode.llm.schema import get_schema_context, get_schema_hash
from ai_mode.prompts.prompt_ai_mode import CHART_GENERATION_PROMPT

_CONFIG_KEYS = ("chart_type", "x_key", "y_key", "title", "sql_query")


def _generate_chart_config_impl(conn, prompt: str) -> Dict[str, Any]:
    """Call LLM to generate chart config, run SQL, return config + data."""
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
                {"role": "user", "content": prompt},
            ],
            temperature=0,
            response_format={"type": "json_object"},
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
            "sql_query": sql_query,
        }
    except Exception as e:
        print(f"❌ Error in generate_chart_config: {str(e)}")
        return {"error": str(e)}


def _run_sql_and_attach_data(conn, config: Dict[str, Any]) -> Dict[str, Any]:
    """Re-run sql_query from config against conn and return config + fresh data."""
    sql_query = config.get("sql_query", "")
    try:
        df = pd.read_sql_query(sql_query, conn)
        return {**config, "data": df_to_json(df)}
    except Exception as e:
        print(f"❌ Error re-running chart SQL: {e}")
        return {**config, "data": [], "error": str(e)}


def generate_chart_config(conn, prompt: str) -> Dict[str, Any]:
    """Generate chart configuration and fetch data for visualization."""
    client = get_ai_client(conn)
    model = get_ai_model(conn)
    if not client:
        return {"error": "API Key Missing"}

    schema_hash = get_schema_hash()
    normalized = normalize_prompt(prompt)
    cached = get("generate_chart_config", (model, schema_hash, normalized))
    if cached is not None:
        try:
            from ai_mode.debug_log import append_entry
            out_preview = json.dumps(cached, default=str)[:800]
            append_entry("generate_chart_config", "cache", out_preview)
        except Exception:
            pass
        return _run_sql_and_attach_data(conn, cached)

    result = _generate_chart_config_impl(conn, prompt)
    if "error" in result:
        return result
    try:
        from ai_mode.debug_log import append_entry
        out_preview = json.dumps(result, default=str)[:800]
        append_entry("generate_chart_config", "llm", out_preview)
    except Exception:
        pass
    config_only = {k: result[k] for k in _CONFIG_KEYS if k in result}
    cache_set("generate_chart_config", (model, schema_hash, normalized), config_only)
    return result
