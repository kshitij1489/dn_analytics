-- ITEM PARSING TABLE
-- Stores the mapping from Raw Name (API/PetPooja) to Internal Cleaned Attributes.
-- Acts as the source of truth for the ItemMatcher in Version 2.

CREATE TABLE IF NOT EXISTS item_parsing_table (
    id SERIAL PRIMARY KEY,
    raw_name TEXT NOT NULL UNIQUE,
    cleaned_name TEXT NOT NULL,
    type VARCHAR(50) NOT NULL,
    variant VARCHAR(100) NOT NULL,
    is_verified BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- Index for fast lookup by raw_name
CREATE INDEX IF NOT EXISTS idx_item_parsing_raw_name ON item_parsing_table(raw_name);

-- Index for finding unverified items (conflicts)
CREATE INDEX IF NOT EXISTS idx_item_parsing_unverified ON item_parsing_table(is_verified) WHERE is_verified = FALSE;
