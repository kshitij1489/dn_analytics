"""
AI Mode: action handlers — each takes (prompt, context, conn) and returns (part, updated_context).
Phase 3: multi-step execution with context. Phase 4: summary and report.
"""

import json
from typing import Dict, Any, Tuple, List

import pandas as pd

from src.api.utils import df_to_json

from ai_mode.context import add_part
from ai_mode.sql_gen import generate_sql
from ai_mode.explanation import generate_explanation
from ai_mode.chart import generate_chart_config
from ai_mode.prompt_ai_mode import SUMMARY_GENERATION_PROMPT, REPORT_GENERATION_PROMPT


def run_run_sql(prompt: str, context: Dict[str, Any], conn) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Execute RUN_SQL: generate SQL, run it, explain. Returns (part, updated_context)."""
    sql_query = generate_sql(conn, prompt)
    try:
        df = pd.read_sql_query(sql_query, conn)
        explanation = generate_explanation(conn, prompt, sql_query, df)
        part = {
            "type": "table",
            "content": df_to_json(df),
            "explanation": explanation,
            "sql_query": sql_query,
        }
        new_ctx = add_part(context, "table", part["content"], explanation, sql_query)
        return part, new_ctx
    except Exception as e:
        part = {
            "type": "text",
            "content": f"I tried to query the database but encountered an error: {str(e)}",
            "sql_query": sql_query,
        }
        new_ctx = add_part(context, "text", part["content"], None, sql_query)
        return part, new_ctx


def run_generate_chart(prompt: str, context: Dict[str, Any], conn) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Execute GENERATE_CHART: generate chart config and data. Returns (part, updated_context)."""
    chart_config = generate_chart_config(conn, prompt)
    if "error" in chart_config:
        part = {"type": "text", "content": f"I couldn't generate the chart: {chart_config['error']}"}
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
    """Execute ASK_CLARIFICATION: return a text part asking for more detail."""
    part = {"type": "text", "content": reason}
    new_ctx = add_part(context, "text", reason)
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

    rows, sql_used = _get_data_for_summary(prompt, context, conn)
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
        rows, sql_used = _get_data_for_summary(prompt, context, conn)
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
