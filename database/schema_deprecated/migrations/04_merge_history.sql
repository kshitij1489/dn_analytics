-- ============================================================================
-- MERGE HISTORY TABLE
-- ============================================================================

CREATE TABLE IF NOT EXISTS merge_history (
    merge_id SERIAL PRIMARY KEY,
    source_id UUID NOT NULL,
    target_id UUID NOT NULL,
    source_name TEXT NOT NULL,
    source_type TEXT NOT NULL,
    affected_order_items JSONB NOT NULL, -- Array of order_item_ids remapped
    merged_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_merge_history_target ON merge_history(target_id);
CREATE INDEX IF NOT EXISTS idx_merge_history_merged_at ON merge_history(merged_at);
