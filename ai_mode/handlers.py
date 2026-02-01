"""
AI Mode: action handlers — each takes (prompt, context, conn) and returns (part, updated_context).
Phase 3: multi-step execution with context. Phase 4: summary and report.
"""

import json
from typing import Dict, Any, Tuple, List

import pandas as pd

from src.api.utils import df_to_json

from ai_mode.context import add_part
from ai_mode.client import get_ai_client, get_ai_model
from ai_mode.sql_gen import generate_sql
from ai_mode.explanation import generate_explanation
from ai_mode.chart import generate_chart_config
from ai_mode.prompt_ai_mode import (
    SUMMARY_GENERATION_PROMPT,
    REPORT_GENERATION_PROMPT,
    NO_DATA_HINT_PROMPT,
)


def _clarification_part(content: str, sql_query: str = None) -> Dict[str, Any]:
    """Build a part that asks for clarification; orchestrator will set query_status=incomplete."""
    part = {"type": "text", "content": content, "clarification": True}
    if sql_query is not None:
        part["sql_query"] = sql_query
    return part


def _is_effectively_empty(df: pd.DataFrame) -> bool:
    """True if no rows, or every cell is null/NaN (e.g. SELECT AVG(...) with zero matching rows returns one row of NULL)."""
    if df.empty:
        return True
    return df.isna().all().all()


# Columns we can show "valid values" for when a query returns no data. Add (table, column, label) to extend.
# Set to [] to show no hints and only the next-steps message.
# TODO: These hints will be replaced with a learned cache (e.g. cached distinct values or LLM-suggested hints).
FILTER_HINT_COLUMNS: List[Tuple[str, str, str]] = [
    # orders: type, source, status (commonly filtered)
    ("orders", "order_type", "Order type"),
    ("orders", "order_from", "Order source"),
    ("orders", "order_status", "Order status"),
    ("orders", "sub_order_type", "Sub order type"),
    # menu_items: category/type (e.g. Beverage, Main)
    ("menu_items", "type", "Menu category/type"),
]

NO_DATA_NEXT_STEPS = (
    " You can ask e.g. \"What order types do we have?\" or \"What are the valid values for order source?\" "
    "to see options for a specific field."
)


def _suggest_no_data_hint(conn, user_prompt: str, sql_query: str) -> str:
    """
    Use LLM to suggest a short, smart hint. Valid values come from FILTER_HINT_COLUMNS (or NO_DATA_NEXT_STEPS).
    TODO: Will be replaced with a learned cache (e.g. precomputed or cached hints).
    """
    # Build valid values string from FILTER_HINT_COLUMNS; to be replaced with learned cache.
    if not FILTER_HINT_COLUMNS:
        valid_values_str = NO_DATA_NEXT_STEPS
    else:
        hints: List[str] = []
        for table, column, label in FILTER_HINT_COLUMNS:
            try:
                df = pd.read_sql_query(
                    f"SELECT DISTINCT {column} FROM {table} WHERE {column} IS NOT NULL AND {column} != '' ORDER BY 1",
                    conn,
                )
                if not df.empty:
                    values = [str(v) for v in df.iloc[:, 0].tolist()]
                    hints.append(f"{label}: {', '.join(values)}")
            except Exception:
                pass
        valid_values_str = " Valid values: " + "; ".join(hints) + "." if hints else NO_DATA_NEXT_STEPS

    client = get_ai_client(conn)
    model = get_ai_model(conn)
    if not client:
        return valid_values_str
    try:
        user_content = f"""User asked: {user_prompt}

SQL that returned no data: {sql_query}

Valid values from our system: {valid_values_str}"""
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": NO_DATA_HINT_PROMPT},
                {"role": "user", "content": user_content},
            ],
            temperature=0.3,
            max_tokens=150,
        )
        suggestion = (response.choices[0].message.content or "").strip()
        return " " + suggestion if suggestion else valid_values_str
    except Exception as e:
        print(f"⚠️ No-data hint suggestion failed, using raw hints: {e}")
        return valid_values_str


def run_run_sql(prompt: str, context: Dict[str, Any], conn) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Execute RUN_SQL: generate SQL, run it, explain. Returns (part, updated_context). May return clarification part on error, empty result, or when we cannot answer."""
    try:
        sql_query = generate_sql(conn, prompt)
    except ValueError as e:
        part = _clarification_part(str(e))
        return part, add_part(context, "text", part["content"])
    try:
        df = pd.read_sql_query(sql_query, conn)
    except Exception as e:
        part = _clarification_part(
            f"I couldn't run that query: {str(e)}. Please rephrase or check what you're asking.",
            sql_query=sql_query,
        )
        new_ctx = add_part(context, "text", part["content"], None, sql_query)
        return part, new_ctx
    if _is_effectively_empty(df):
        smart_hint = _suggest_no_data_hint(conn, prompt, sql_query)
        part = _clarification_part(
            "No data found for that query. Please double-check filter values or rephrase."
            + smart_hint,
            sql_query=sql_query,
        )
        new_ctx = add_part(context, "text", part["content"], None, sql_query)
        return part, new_ctx
    explanation = generate_explanation(conn, prompt, sql_query, df)
    part = {
        "type": "table",
        "content": df_to_json(df),
        "explanation": explanation,
        "sql_query": sql_query,
    }
    new_ctx = add_part(context, "table", part["content"], explanation, sql_query)
    return part, new_ctx


def run_generate_chart(prompt: str, context: Dict[str, Any], conn) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Execute GENERATE_CHART: generate chart config and data. Returns (part, updated_context). May return clarification part on error or empty data."""
    chart_config = generate_chart_config(conn, prompt)
    if "error" in chart_config:
        part = _clarification_part(f"I couldn't generate the chart: {chart_config['error']}. Please rephrase or check filters.")
        new_ctx = add_part(context, "text", part["content"])
        return part, new_ctx
    data = chart_config.get("data")
    if data is None or (isinstance(data, list) and len(data) == 0):
        part = _clarification_part(
            "No data for that chart. Please check filter values (e.g. order type, date range) or rephrase."
        )
        new_ctx = add_part(context, "text", part["content"])
        return part, new_ctx
    part = {
        "type": "chart",
        "content": chart_config,
        "sql_query": chart_config.get("sql_query"),
    }
    new_ctx = add_part(context, "chart", chart_config, None, chart_config.get("sql_query"))
    return part, new_ctx


def run_ask_clarification(prompt: str, context: Dict[str, Any], conn,
                          reason: str = "I need a bit more info.") -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Execute ASK_CLARIFICATION: return a text part asking for more detail. Part has clarification=True so orchestrator sets query_status=incomplete."""
    part = {"type": "text", "content": reason, "clarification": True}
    new_ctx = add_part(context, "text", reason)
    return part, new_ctx


OUT_OF_SCOPE_MESSAGE = (
    "We can only answer questions related to our restaurant analytics data "
    "(orders, revenue, menu items, trends, etc.). Please ask something about our system."
)


def run_out_of_scope(prompt: str, context: Dict[str, Any], conn) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Execute OUT_OF_SCOPE: return a fixed message that we only answer questions about our system."""
    part = {"type": "text", "content": OUT_OF_SCOPE_MESSAGE}
    new_ctx = add_part(context, "text", part["content"])
    return part, new_ctx


def run_general_chat(prompt: str, context: Dict[str, Any], conn) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Execute GENERAL_CHAT: free-form LLM response. Returns (part, updated_context)."""
    from ai_mode.client import get_ai_client, get_ai_model
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
    part = {"type": "text", "content": content}
    new_ctx = add_part(context, "text", content)
    return part, new_ctx


def _get_data_for_summary(prompt: str, context: Dict[str, Any], conn) -> Tuple[List[Dict], str]:
    """
    Get table data for summary/report: use context.last_table_data if present, else run SQL.
    Returns (rows, sql_used_or_empty).
    """
    table_data = context.get("last_table_data")
    if table_data and isinstance(table_data, list) and len(table_data) > 0:
        return table_data, context.get("last_sql") or ""
    sql_query = generate_sql(conn, prompt)
    try:
        df = pd.read_sql_query(sql_query, conn)
        return df_to_json(df), sql_query
    except Exception:
        return [], sql_query


def run_generate_summary(prompt: str, context: Dict[str, Any], conn) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """
    Execute GENERATE_SUMMARY: produce short text summary of data.
    Uses context.last_table_data if from a prior RUN_SQL; else runs SQL, then summarizes.
    Returns (part, updated_context). Part type is "text" (Phase 4.2).
    """
    from ai_mode.client import get_ai_client, get_ai_model
    client = get_ai_client(conn)
    model = get_ai_model(conn)
    if not client:
        part = {"type": "text", "content": "AI not configured. Please add an API Key in Configuration."}
        return part, add_part(context, "text", part["content"])

    try:
        rows, sql_used = _get_data_for_summary(prompt, context, conn)
    except ValueError as e:
        part = {"type": "text", "content": str(e)}
        return part, add_part(context, "text", part["content"])
    if not rows:
        part = {"type": "text", "content": "I couldn't get any data to summarize. Try asking for a specific time range or metric."}
        return part, add_part(context, "text", part["content"])

    data_preview = json.dumps(rows[:50], default=str)  # cap for token limit
    if len(rows) > 50:
        data_preview += f"\n... ({len(rows) - 50} more rows)"
    user_prompt = f"""User question: {prompt}

Data (JSON rows):
{data_preview}

{SUMMARY_GENERATION_PROMPT}"""
    response = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": user_prompt}],
        temperature=0.5,
    )
    content = response.choices[0].message.content.strip()
    part = {"type": "text", "content": content}
    if sql_used:
        part["sql_query"] = sql_used
    new_ctx = add_part(context, "text", content, None, sql_used or None)
    return part, new_ctx


def _describe_parts_for_report(context: Dict[str, Any]) -> str:
    """Build a text description of context.parts for the report LLM."""
    parts = context.get("parts") or []
    if not parts:
        return "(No prior data or charts in this conversation.)"
    lines = []
    for i, p in enumerate(parts, 1):
        ptype = p.get("type", "")
        content = p.get("content")
        expl = p.get("explanation", "")
        if ptype == "table" and isinstance(content, list):
            lines.append(f"Table {i}: {len(content)} rows. {expl}".strip())
            lines.append("Sample: " + json.dumps(content[:5], default=str))
        elif ptype == "chart" and isinstance(content, dict):
            title = content.get("title", "Chart")
            lines.append(f"Chart {i}: {title}. {expl}".strip())
        elif ptype == "text" and isinstance(content, str):
            lines.append(f"Summary/Text {i}: {content[:500]}" + ("..." if len(content) > 500 else ""))
    return "\n".join(lines) if lines else "(No prior data.)"


def run_generate_report(prompt: str, context: Dict[str, Any], conn) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """
    Execute GENERATE_REPORT: produce narrative report using context (tables, charts, prior text).
    If context has no parts, fetches data via SQL first, then generates report (Phase 4.3).
    Returns (part, updated_context). Part type is "text".
    """
    from ai_mode.client import get_ai_client, get_ai_model
    client = get_ai_client(conn)
    model = get_ai_model(conn)
    if not client:
        part = {"type": "text", "content": "AI not configured. Please add an API Key in Configuration."}
        return part, add_part(context, "text", part["content"])

    parts_desc = _describe_parts_for_report(context)
    # If no prior data, get some via SQL so the report has something to work with
    if not context.get("parts"):
        try:
            rows, sql_used = _get_data_for_summary(prompt, context, conn)
        except ValueError as e:
            part = {"type": "text", "content": str(e)}
            return part, add_part(context, "text", part["content"])
        if rows:
            data_preview = json.dumps(rows[:30], default=str)
            if len(rows) > 30:
                data_preview += f"\n... ({len(rows) - 30} more rows)"
            parts_desc = f"Data from query:\n{data_preview}"
        else:
            parts_desc = "No data could be retrieved for the given question."
    else:
        sql_used = context.get("last_sql")

    user_prompt = f"""User request: {prompt}

Context / data already available:
{parts_desc}

{REPORT_GENERATION_PROMPT}"""
    response = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": user_prompt}],
        temperature=0.5,
    )
    content = response.choices[0].message.content.strip()
    part = {"type": "text", "content": content}
    if sql_used:
        part["sql_query"] = sql_used
    new_ctx = add_part(context, "text", content, None, sql_used or None)
    return part, new_ctx
