"""
LLM cache configuration.

All limits and paths are centralized here so they can be read and updated
without changing the cache layer. Override via environment variables if needed.

In packaged (.dmg) builds the process cwd is often read-only; DB_URL is set by
Electron to a writable userData path, so we put the cache in the same directory.
"""

import os

# --- Path ---
# Prefer LLM_CACHE_DB_PATH; else same dir as DB_URL (packaged app); else cwd.
# Packaged app sets DB_URL to e.g. ~/Library/Application Support/.../analytics.db.
if os.environ.get("LLM_CACHE_DB_PATH"):
    CACHE_DB_PATH = os.environ["LLM_CACHE_DB_PATH"]
elif os.environ.get("DB_URL"):
    _dir = os.path.dirname(os.path.abspath(os.environ["DB_URL"]))
    CACHE_DB_PATH = os.path.join(_dir, "llm_cache.db")
else:
    CACHE_DB_PATH = os.path.join(os.getcwd(), "llm_cache.db")

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
