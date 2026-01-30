-- Phase 6: Add pipeline metadata columns to ai_logs (run once on existing DBs).
-- New installs get these via schema_sqlite.sql; existing DBs run this migration.

-- SQLite does not support IF NOT EXISTS for ADD COLUMN; run each once.
ALTER TABLE ai_logs ADD COLUMN raw_user_query TEXT;
ALTER TABLE ai_logs ADD COLUMN corrected_query TEXT;
ALTER TABLE ai_logs ADD COLUMN action_sequence TEXT;
ALTER TABLE ai_logs ADD COLUMN explanation TEXT;
