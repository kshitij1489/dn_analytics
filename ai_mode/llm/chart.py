"""
AI Mode: chart configuration and data generation from natural language.
Config (chart_type, x_key, y_key, title, sql_query) is cached by
(model, schema_hash, business_today, normalized_prompt); SQL is re-run on
cache hit so chart data is always fresh. See docs/LLM_CACHE_PLAN.md.
"""

import json
from typing import Dict, Any

from src.api.utils import df_to_json
from ai_mode.cache import get, normalize_prompt, cache_set, bump_cache_counter
from ai_mode.llm.client import get_ai_client, get_ai_model
from ai_mode.llm.completion import chat_completion
from ai_mode.llm.schema import get_schema_context, get_schema_hash
from ai_mode.telemetry import record_cache_hit
from ai_mode.llm.sql_gen import _business_date_context, ensure_read_only_sql, read_sql_readonly
from ai_mode.llm.schemas import ChartConfigResult
from ai_mode.prompts.prompt_ai_mode import CHART_GENERATION_PROMPT
from src.core.utils.business_date import get_current_business_date

_CONFIG_KEYS = ("chart_type", "x_key", "y_key", "title", "sql_query")


def _generate_chart_config_impl(conn, prompt: str) -> Dict[str, Any]:
    """Call LLM to generate chart config, run SQL, return config + data."""
    client = get_ai_client(conn)
    model = get_ai_model(conn)
    if not client:
        return {"error": "API Key Missing"}

    schema = get_schema_context(conn)
    date_ctx = _business_date_context()
    try:
        response = chat_completion(
            client, "generate_chart_config", model,
            messages=[
                {"role": "system", "content": CHART_GENERATION_PROMPT.format(schema=schema, **date_ctx)},
                {"role": "user", "content": prompt},
            ],
            temperature=0,
            response_format={"type": "json_object"},
        )
        config = ChartConfigResult.model_validate(json.loads(response.choices[0].message.content or "{}"))
        sql_query = config.sql or ""
        ensure_read_only_sql(sql_query)
        df = read_sql_readonly(conn, sql_query)
        return {
            "chart_type": config.chart_type or "bar",
            "data": df_to_json(df),
            "x_key": config.x_key or "label",
            "y_key": config.y_key or "value",
            "title": config.title or "Chart",
            "sql_query": sql_query,
        }
    except Exception as e:
        print(f"❌ Error in generate_chart_config: {str(e)}")
        return {"error": str(e)}


def _run_sql_and_attach_data(conn, config: Dict[str, Any]) -> Dict[str, Any]:
    """Re-run sql_query from config against conn and return config + fresh data."""
    sql_query = config.get("sql_query", "")
    try:
        ensure_read_only_sql(sql_query)
        df = read_sql_readonly(conn, sql_query)
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

    schema_hash = get_schema_hash(conn)
    # business_today in the key: generated SQL embeds literal date boundaries,
    # so a config cached yesterday would re-run stale literals forever otherwise
    business_today = get_current_business_date()
    normalized = normalize_prompt(prompt)
    cached = get("generate_chart_config", (model, schema_hash, business_today, normalized))
    if cached is not None:
        record_cache_hit("generate_chart_config", model)
        bump_cache_counter("generate_chart_config", hit=True)
        try:
            from ai_mode.debug_log import append_entry
            out_preview = json.dumps(cached, default=str)[:800]
            append_entry("generate_chart_config", "cache", out_preview)
        except Exception:
            pass
        return _run_sql_and_attach_data(conn, cached)

    bump_cache_counter("generate_chart_config", hit=False)
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
    cache_set("generate_chart_config", (model, schema_hash, business_today, normalized), config_only)
    return result
