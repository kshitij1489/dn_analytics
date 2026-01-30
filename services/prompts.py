"""
Legacy re-export: AI Mode prompts now live in ai_mode.prompt_ai_mode.
Import from ai_mode.prompt_ai_mode for new code.
"""

from ai_mode.prompt_ai_mode import (
    SYSTEM_ROUTER_PROMPT,
    SQL_GENERATION_PROMPT,
    CHART_GENERATION_PROMPT,
)

__all__ = ["SYSTEM_ROUTER_PROMPT", "SQL_GENERATION_PROMPT", "CHART_GENERATION_PROMPT"]
