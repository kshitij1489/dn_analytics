-- ============================================================================
-- Restaurants Table Schema
-- ============================================================================
-- This schema defines the restaurants table for storing restaurant information.
-- Currently single restaurant, but designed to support multiple locations.
--
-- ============================================================================

-- ============================================================================
-- RESTAURANTS TABLE
-- ============================================================================
-- Stores restaurant information from PetPooja.
-- Currently single restaurant (Dach & Nona), but designed for multi-location support.

CREATE TABLE IF NOT EXISTS restaurants (
    restaurant_id SERIAL PRIMARY KEY,
    
    -- Restaurant identification
    petpooja_restid VARCHAR(100) NOT NULL UNIQUE,  -- From Restaurant.restID
    name VARCHAR(255) NOT NULL,                     -- From Restaurant.res_name
    address TEXT,                                   -- From Restaurant.address
    contact_information VARCHAR(50),                -- From Restaurant.contact_information
    
    -- Status
    is_active BOOLEAN DEFAULT TRUE,
    
    -- Timestamps
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

-- ============================================================================
-- INDEXES FOR PERFORMANCE
-- ============================================================================

CREATE INDEX IF NOT EXISTS idx_restaurants_petpooja_restid 
    ON restaurants(petpooja_restid);

CREATE INDEX IF NOT EXISTS idx_restaurants_name 
    ON restaurants(name);

CREATE INDEX IF NOT EXISTS idx_restaurants_is_active 
    ON restaurants(is_active);

-- ============================================================================
-- COMMENTS FOR DOCUMENTATION
-- ============================================================================

COMMENT ON TABLE restaurants IS 
    'Stores restaurant information from PetPooja. '
    'Currently single restaurant (Dach & Nona), but designed for multi-location support.';

COMMENT ON COLUMN restaurants.petpooja_restid IS 
    'PetPooja restaurant ID (from Restaurant.restID). Unique identifier from PetPooja system.';

-- ============================================================================
-- EXAMPLE QUERIES
-- ============================================================================

-- Get restaurant by PetPooja ID
-- SELECT * FROM restaurants WHERE petpooja_restid = '1c8w7fp500';

-- Get all active restaurants
-- SELECT * FROM restaurants WHERE is_active = TRUE;

