-- ============================================================================
-- LLM Cache Database Schema (SQLite)
-- ============================================================================
-- This schema applies to the separate LLM cache DB (llm_cache.db), not the
-- main application DB. Path is set in ai_mode/cache/cache_config.py
-- (same directory as main DB or cwd).
--
-- Cloud sync: The client should sync this DB (or export entries with
-- is_incorrect = 1) so the cloud can use human feedback for learning.
-- ============================================================================

CREATE TABLE IF NOT EXISTS llm_cache (
    key_hash TEXT PRIMARY KEY,
    call_id TEXT NOT NULL,
    value TEXT NOT NULL,
    created_at TEXT NOT NULL,
    last_used_at TEXT,
    is_incorrect INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_llm_cache_lru ON llm_cache (last_used_at, created_at);

-- Optional: index for cloud sync to fetch entries marked incorrect
CREATE INDEX IF NOT EXISTS idx_llm_cache_is_incorrect ON llm_cache (is_incorrect) WHERE is_incorrect = 1;
