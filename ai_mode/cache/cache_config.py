"""
LLM cache configuration.

All limits and paths are centralized here so they can be read and updated
without changing the cache layer. Override via environment variables if needed.
"""

import os

# --- Path ---
# Default: same directory as process cwd, file llm_cache.db.
# Override with env LLM_CACHE_DB_PATH for a different path.
CACHE_DB_PATH = os.environ.get("LLM_CACHE_DB_PATH") or os.path.join(
    os.getcwd(), "llm_cache.db"
)

# --- Eviction ---
# Global max number of cache entries. When exceeded, oldest-by-last-used
# entries are evicted (LRU). Tune based on usage.
MAX_ENTRIES = int(os.environ.get("LLM_CACHE_MAX_ENTRIES", "10_000").replace("_", ""))

# --- Diversity cache (run_general_chat only) ---
# Max distinct responses to store per prompt; when full, we randomly pick one
# instead of calling the LLM.
DIVERSITY_CACHE_SIZE = int(
    os.environ.get("LLM_CACHE_DIVERSITY_SIZE", "5").replace("_", "")
)
