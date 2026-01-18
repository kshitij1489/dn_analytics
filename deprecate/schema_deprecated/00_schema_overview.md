# Database Schema Overview

## Complete Schema Structure

This document provides an overview of the complete database schema for the order management analytics system.

---

## Table Dependencies

```
restaurants (no dependencies)
    ↓
customers (no dependencies)
    ↓
menu_items (no dependencies)
    ↓
variants (no dependencies)
    ↓
menu_item_variants (depends on: menu_items, variants)
    ↓
orders (depends on: customers, restaurants)
    ↓
order_taxes (depends on: orders)
order_discounts (depends on: orders)
order_items (depends on: orders, menu_items, variants)
    │
order_item_addons (depends on: order_items, menu_items, variants)

item_parsing_table (single source of truth for name matching)
```

---

## Core Tables

### 1. **restaurants** (`database/schema/restaurants.sql`)
- Stores restaurant information
- Currently single restaurant (Dach & Nona)
- Designed for multi-location support

**Key Fields:**
- `restaurant_id` (PK)
- `petpooja_restid` (unique)
- `name`, `address`, `contact_information`

### 2. **customers** (`database/schema/customers.sql`)
- Stores customer information
- Deduplicated by phone number
- Tracks customer lifetime value

**Key Fields:**
- `customer_id` (PK)
- `phone` (unique, nullable)
- `name`, `address`, `gstin`
- `total_orders`, `total_spent` (denormalized)

### 3. **menu_items** (`database/schema/menu_items.sql`)
- Stores base menu items (products)
- No prices (prices in menu_item_variants)

**Key Fields:**
- `menu_item_id` (PK)
- `name`, `type`
- `petpooja_itemid`, `itemcode` (for matching)

### 4. **variants** (`database/schema/menu_items.sql`)
- Stores variant definitions (shared across items)
- Examples: MINI_TUB_160GMS, REGULAR_SCOOP_120GMS

**Key Fields:**
- `variant_id` (PK)
- `variant_name` (unique)

### 5. **menu_item_variants** (`database/schema/menu_items.sql`)
- Junction table: links items to variants with pricing
- Stores prices and eligibility flags

**Key Fields:**
- `menu_item_variant_id` (PK)
- `menu_item_id` (FK), `variant_id` (FK)
- `price`
- `addon_eligible`, `delivery_eligible`

### 6. **orders** (`database/schema/orders.sql`)
- Stores order-level information
- Links to customers and restaurants

**Key Fields:**
- `order_id` (PK)
- `petpooja_order_id` (unique)
- `stream_id` (for incremental updates)
- `customer_id` (FK), `restaurant_id` (FK)
- Financial fields: `core_total`, `tax_total`, `discount_total`, `total`
- Order metadata: `order_type`, `order_from`, `order_status`

### 7. **order_taxes** (`database/schema/orders.sql`)
- Stores individual tax components (CGST, SGST separately)

**Key Fields:**
- `order_tax_id` (PK)
- `order_id` (FK)
- `tax_title`, `tax_rate`, `tax_type`, `tax_amount`

### 8. **order_discounts** (`database/schema/orders.sql`)
- Stores individual discount components

**Key Fields:**
- `order_discount_id` (PK)
- `order_id` (FK)
- `discount_title`, `discount_type`, `discount_amount`

### 9. **order_items** (`database/schema/order_items.sql`)
- Stores individual items within orders
- Links to menu items and variants

**Key Fields:**
- `order_item_id` (PK)
- `order_id` (FK)
- `menu_item_id` (FK), `variant_id` (FK)
- `name_raw` (original PetPooja name)
- `quantity`, `unit_price`, `total_price`
- `match_confidence`, `match_method`

### 10. **order_item_addons** (`database/schema/order_items.sql`)
- Stores addons attached to order items
- Addons are also menu items

**Key Fields:**
- `order_item_addon_id` (PK)
- `order_item_id` (FK)
- `menu_item_id` (FK), `variant_id` (FK)
- `name_raw`, `group_name`
- `quantity`, `price`

### 11. **item_parsing_table** (`database/schema/item_parsing.sql`)
- Stores mappings between raw API names and cleaned attributes.
- Centralizes parsing logic for consistency.

**Key Fields:**
- `id` (PK)
- `raw_name` (unique)
- `cleaned_name`, `type`, `variant`
- `is_verified` (Boolean)

---

## Entity Relationship Diagram

```
┌─────────────────┐
│   restaurants   │
│ restaurant_id PK│
└────────┬────────┘
         │
         │ 1:N
         │
┌────────▼────────┐     ┌─────────────────┐
│     orders      │     │    customers    │
│   order_id PK   │◄────│  customer_id PK │
│ restaurant_id FK│     └─────────────────┘
│  customer_id FK │
└────────┬────────┘
         │
         │ 1:N
         │
┌────────▼────────┐     ┌─────────────────┐     ┌─────────────────┐
│  order_taxes    │     │ order_discounts  │     │  order_items    │
│ order_tax_id PK │     │order_discount_id│     │order_item_id PK │
│   order_id FK   │     │   order_id FK   │     │   order_id FK   │
└─────────────────┘     └─────────────────┘     │ menu_item_id FK │
                                                  │  variant_id FK  │
                                                  └────────┬────────┘
                                                           │
                                                           │ 1:N
                                                           │
                                                  ┌────────▼────────┐
                                                  │order_item_      │
                                                  │   addons        │
                                                  │order_item_addon │
                                                  │   _id PK        │
                                                  │order_item_id FK │
                                                  │ menu_item_id FK │
                                                  └─────────────────┘

┌─────────────────┐     ┌─────────────────┐
│   menu_items    │     │    variants     │
│ menu_item_id PK │     │  variant_id PK  │
└────────┬────────┘     └────────┬────────┘
         │                       │
         │ N:M                   │
         └───────────┬────────────┘
                     │
         ┌───────────▼───────────┐
         │ menu_item_variants    │
         │menu_item_variant_id PK│
         │  menu_item_id FK      │
         │   variant_id FK       │
         │   price               │
         │ addon_eligible         │
         │ delivery_eligible      │
         └───────────────────────┘
```

---

## Key Design Decisions

### 1. **Normalized Menu Structure**
- Menu items and variants are separate tables
- Prices stored in junction table (`menu_item_variants`)
- Allows flexible pricing and variant management

### 2. **Historical Price Preservation**
- Order items store prices at time of order
- Menu prices may change, but order prices remain historical
- Enables accurate revenue analysis

### 3. **Matching Confidence Tracking**
- `order_items.match_confidence` tracks matching quality
- Helps identify items needing manual review
- Enables data quality monitoring

### 4. **Stream ID for Incremental Updates**
- `orders.stream_id` enables efficient incremental updates
- Always fetch orders with `stream_id > last_processed_stream_id`
- Avoids reprocessing entire dataset

### 5. **Customer Deduplication**
- Phone number is primary identifier
- NULL allowed for anonymous customers (POS orders)
- Denormalized fields (`total_orders`, `total_spent`) for performance

### 7. **Data-Driven Parsing**
- `item_parsing_table` acts as the single source of truth for name matching.
- Decouples logic from code into the database.
- Enables UI-driven conflict resolution and verified mappings.

---

## Data Loading Order

When loading data, follow this order to respect foreign key constraints:

1. **restaurants** (no dependencies)
2. **customers** (no dependencies)
3. **menu_items** (no dependencies)
4. **variants** (no dependencies)
5. **menu_item_variants** (depends on menu_items, variants)
6. **orders** (depends on customers, restaurants)
7. **order_taxes** (depends on orders)
8. **order_discounts** (depends on orders)
9. **order_items** (depends on orders, menu_items, variants)
10. **order_item_addons** (depends on order_items, menu_items, variants)

---

## File Structure

```
database/schema/
├── 00_schema_overview.md    # This file
├── restaurants.sql           # Restaurant table
├── customers.sql             # Customer table
├── menu_items.sql           # Menu items, variants, menu_item_variants
├── orders.sql               # Orders, order_taxes, order_discounts
├── order_items.sql          # Order items, order_item_addons
└── item_parsing.sql         # Item parsing rules
```

---

## Next Steps

1. ✅ **Schema Design Complete** - All tables designed
2. **Create Data Loading Scripts** - Populate tables from cleaned data
3. **Implement Matching Logic** - Match order items to menu items
4. **Test with Sample Data** - Validate schema with 100 orders
5. **Full Data Load** - Process all 5,465 orders

---

*Last Updated: January 9, 2026*

