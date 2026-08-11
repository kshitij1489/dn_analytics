"""AI Mode: LLM response cache (config + SQLite backend)."""

from ai_mode.cache.cache_config import (
    CACHE_DB_PATH,
    DIVERSITY_CACHE_SIZE,
    MAX_ENTRIES,
)
from ai_mode.cache.llm_cache import (
    build_key,
    bump_cache_counter,
    clear_cache,
    get,
    get_cache_counters,
    get_or_call,
    get_or_call_diversity,
    list_entries,
    normalize_prompt,
    set as cache_set,
    set_incorrect,
)

__all__ = [
    "CACHE_DB_PATH",
    "DIVERSITY_CACHE_SIZE",
    "MAX_ENTRIES",
    "build_key",
    "bump_cache_counter",
    "clear_cache",
    "get",
    "get_cache_counters",
    "get_or_call",
    "get_or_call_diversity",
    "list_entries",
    "normalize_prompt",
    "cache_set",
    "set_incorrect",
]
