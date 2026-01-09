# Order Management Analytics System

## Overview

Build a complete order management database and analytics pipeline for the cafe business. The system will:
- Fetch order data from PetPooja API via a Django webhook server
- Process and normalize the data
- Create relational tables for comprehensive analytics
- Enable predictions and business insights

---

## Data Source

**Django Webhook Server:**
```
BASE_URL: https://webhooks.db1-prod-dachnona.store/analytics
API_KEY: f3e1753aa4c44159fa7218a31cd8db1e
```

**Data Types:**
- PetPooja backfill data (historical orders)
- Real-time order webhook calls

---

## Task Breakdown

### Task 1: Fetch Raw JSON Payloads

**Objective:** Create a Python function to fetch order data from the Django server.

**Subtasks:**
- [x] 1.1 Explore available API endpoints (list orders, get order by ID, date range queries)
- [x] 1.2 Write authentication handler with API key
- [x] 1.3 Create pagination handler for large datasets
- [x] 1.4 Implement rate limiting to avoid server overload
- [x] 1.5 Add error handling and retry logic
- [x] 1.6 Save raw JSON responses locally for analysis
- [x] 1.7 Create a data refresh mechanism (incremental vs full sync)

**Status:** ✅ **COMPLETE** - See `fetch_orders.py`

**Expected Output:**
```python
def fetch_orders(start_date=None, end_date=None, limit=None) -> List[dict]:
    """Fetch order payloads from the webhook server"""
    pass

def fetch_order_by_id(order_id: str) -> dict:
    """Fetch a single order by ID"""
    pass
```

**Questions to Answer:**
- What endpoints are available on the Django server?
- How is the data paginated?
- What date range of historical data is available?
- Is there a webhook payload schema documentation?

---

### Task 2: Analyze Payloads & Design Database Schema

**Objective:** Understand the PetPooja order JSON structure and design normalized tables.

**Subtasks:**
- [ ] 2.1 Collect sample payloads (at least 50-100 orders)
- [ ] 2.2 Document all JSON fields and their data types
- [ ] 2.3 Identify nested structures (items, modifiers, discounts, taxes)
- [ ] 2.4 Map PetPooja fields to business concepts
- [ ] 2.5 Design normalized database schema (3NF)
- [ ] 2.6 Define primary keys, foreign keys, and indexes
- [ ] 2.7 Handle edge cases (cancelled orders, refunds, partial orders)

**Actual PetPooja Order Structure (Validated):**
```json
{
  "stream_id": 111,
  "event_id": "uuid",
  "aggregate_type": "order",
  "aggregate_id": "110",  // Order ID
  "event_type": "orderdetails",
  "occurred_at": "2026-01-03T17:40:38.027940+00:00",
  "raw_event": {
    "source": "petpooja",
    "command_id": "hash",
    "raw_payload": {
      "event": "orderdetails",
      "token": "",
      "properties": {
        "Tax": [
          {
            "rate": 9.0,
            "type": "P",  // Percentage
            "title": "SGST@9",
            "amount": 120.6
          }
        ],
        "Order": {
          "orderID": 110,
          "total": 1661.0,
          "core_total": 1440.0,
          "tax_total": 241.2,
          "discount_total": 100.0,
          "delivery_charges": 0.0,
          "packaging_charge": 80.0,
          "service_charge": 0,
          "round_off": "-0.20",
          "created_on": "2025-06-08 20:55:58",
          "order_type": "Delivery|Dine In|Takeaway",
          "order_from": "Zomato|POS|Swiggy",
          "sub_order_type": "Zomato|AC|...",
          "payment_type": "Online|Card|Cash",
          "status": "Success",
          "biller": "Zomato|POS",
          "order_from_id": "6054307452",
          "customer_invoice_id": "110",
          "table_no": "",
          "token_no": "",
          "assignee": "",
          "no_of_persons": 0,
          "comment": ""
        },
        "Customer": {
          "name": "Mudita Roy",
          "phone": "",
          "address": "Sector 66, Gurgaon Delhi NCR India",
          "gstin": ""
        },
        "OrderItem": [
          {
            "itemid": 1282571499,
            "name": "Old Fashion Vanilla Ice Cream (Perfect Plenty (300ml))",
            "itemcode": "VANILLAICE",
            "quantity": 1,
            "price": 360.0,
            "total": 360.0,
            "tax": 60.3,
            "discount": 25.0,
            "addon": [
              {
                "addonid": "53392899",
                "name": "Cup",
                "price": 0,
                "quantity": "1",
                "group_name": "Cuporcone",
                "addon_sap_code": ""
              }
            ],
            "category_name": "Comfort Classics",
            "specialnotes": "",
            "sap_code": "",
            "vendoritemcode": ""
          }
        ],
        "Discount": [
          {
            "rate": 0.0,
            "type": "F",  // Fixed
            "title": "Special Discount",
            "amount": 100.0
          }
        ],
        "Restaurant": {
          "restID": "1c8w7fp500",
          "res_name": "Dach & Nona",
          "address": "House 2173, Ramgarh Dhani...",
          "contact_information": "7428846234"
        }
      }
    }
  }
}
```

**Key Observations:**
- Order ID is in `aggregate_id` and `raw_payload.properties.Order.orderID`
- Item names need matching to `cleaned_menu.csv` (e.g., "Old Fashion Vanilla Ice Cream (Perfect Plenty (300ml))")
- Addons are nested within OrderItem
- Taxes and discounts are at order level
- Customer info may be empty for POS orders
- Multiple tax types (CGST, SGST) with same rate

**Actual Database Schema (Based on PetPooja Payloads):**

#### Core Tables:

```
┌─────────────────┐     ┌─────────────────┐     ┌─────────────────┐
│    customers    │     │     orders      │     │   order_items   │
├─────────────────┤     ├─────────────────┤     ├─────────────────┤
│ customer_id PK  │◄────│ customer_id FK  │     │ order_item_id PK│
│ name            │     │ order_id PK     │◄────│ order_id FK     │
│ phone           │     │ stream_id       │     │ menu_item_id FK │
│ address         │     │ event_id        │     │ petpooja_itemid │
│ gstin           │     │ occurred_at     │     │ itemcode        │
│ created_at      │     │ created_on      │     │ name_raw        │
│ updated_at      │     │ order_type      │     │ quantity        │
└─────────────────┘     │ order_from      │     │ unit_price      │
                        │ sub_order_type  │     │ total_price     │
                        │ order_status    │     │ tax_amount      │
                        │ payment_type    │     │ discount_amount │
                        │ biller          │     │ category_name   │
                        │ order_from_id   │     │ specialnotes    │
                        │ customer_invoice│     │ sap_code        │
                        │ table_no        │     │ vendoritemcode  │
                        │ token_no        │     └────────┬────────┘
                        │ assignee        │              │
                        │ no_of_persons   │     ┌────────▼────────┐
                        │ comment         │     │ order_item_     │
                        │ core_total      │     │    addons       │
                        │ tax_total       │     ├─────────────────┤
                        │ discount_total  │     │ id PK           │
                        │ delivery_charges│     │ order_item_id FK│
                        │ packaging_charge│     │ petpooja_addonid │
                        │ service_charge  │     │ addon_name      │
                        │ round_off       │     │ group_name      │
                        │ total           │     │ quantity        │
                        │ created_at      │     │ price           │
                        │ updated_at      │     │ addon_sap_code  │
                        └─────────────────┘     └─────────────────┘

┌─────────────────┐
│   menu_items    │
├─────────────────┤
│ menu_item_id PK │
│ name            │  (from cleaned_menu.csv)
│ type            │  (Ice Cream, Dessert, etc.)
│ variant         │  (MINI_TUB_160GMS, etc.)
│ base_price      │
│ is_active       │
│ petpooja_itemid │  (for matching)
│ itemcode        │  (VANILLAICE, etc.)
└─────────────────┘
```

#### Supporting Tables:

```
┌─────────────────┐     ┌─────────────────┐     ┌─────────────────┐
│     addons      │     │  order_taxes    │     │ order_discounts │
├─────────────────┤     ├─────────────────┤     ├─────────────────┤
│ addon_id PK     │     │ id PK           │     │ id PK           │
│ petpooja_addonid│     │ order_id FK     │     │ order_id FK     │
│ name            │     │ tax_title       │     │ discount_title  │
│ group_name      │     │ tax_rate        │     │ discount_type   │
│ base_price      │     │ tax_type        │     │ discount_rate   │
│ is_active       │     │ tax_amount      │     │ discount_amount │
└─────────────────┘     └─────────────────┘     └─────────────────┘

┌─────────────────┐
│  restaurants    │
├─────────────────┤
│ restaurant_id PK│
│ petpooja_restid │
│ name            │
│ address         │
│ contact_info    │
└─────────────────┘
```

**Key Design Decisions:**
1. **Stream ID tracking:** Store `stream_id` for incremental updates
2. **Raw name preservation:** Keep original item names in `order_items.name_raw` for matching
3. **PetPooja ID mapping:** Store PetPooja IDs for reconciliation
4. **Customer deduplication:** Use phone number as primary identifier (may be empty for POS)
5. **Tax breakdown:** Store individual tax components (CGST, SGST separately)
6. **Addon groups:** Track addon groups (e.g., "Cuporcone") for analytics
7. **Data volume:** Designed for 300K+ orders (indexes on order_id, created_on, customer_id)
```

---

### Task 3: Clean & Process Data

**Objective:** Apply data cleaning similar to `clean_menu_data.py` for order data.

**Subtasks:**
- [ ] 3.1 Map raw item names to normalized `menu_items` table (using `cleaned_menu.csv`)
- [ ] 3.2 Handle item name variations and typos in orders
- [ ] 3.3 Standardize customer phone numbers (remove country code, format)
- [ ] 3.4 Deduplicate customers (same phone = same customer)
- [ ] 3.5 Validate and fix timestamps (timezone handling)
- [ ] 3.6 Handle missing/null values appropriately
- [ ] 3.7 Detect and flag anomalies (negative prices, unrealistic quantities)
- [ ] 3.8 Create mapping tables for PetPooja IDs → internal IDs

**Item Name Matching Strategy:**
```python
# Use fuzzy matching to map order item names to cleaned menu items
from fuzzywuzzy import fuzz

def match_menu_item(raw_name: str, cleaned_menu: pd.DataFrame) -> int:
    """
    Match raw order item name to menu_item_id
    Returns: menu_item_id or None if no match
    """
    # 1. Exact match
    # 2. Fuzzy match with threshold
    # 3. Manual review queue for low-confidence matches
    pass
```

**Data Quality Checks:**
- [ ] No duplicate order IDs
- [ ] All orders have at least one item
- [ ] Total = Subtotal + Taxes - Discounts + Delivery
- [ ] Valid timestamps (not in future, not too old)
- [ ] Customer phone numbers are valid
- [ ] Item prices are positive

---

### Task 4: Create Final Tables with Proper Mappings

**Objective:** Populate the database tables with cleaned, normalized data.

**Subtasks:**
- [ ] 4.1 Generate `menu_items` table from `cleaned_menu.csv`
- [ ] 4.2 Create customer deduplication logic
- [ ] 4.3 Build order ingestion pipeline
- [ ] 4.4 Create order items with menu_item_id foreign keys
- [ ] 4.5 Process addons with addon_id foreign keys
- [ ] 4.6 Store taxes and discounts properly
- [ ] 4.7 Build incremental update mechanism (new orders only)
- [ ] 4.8 Create data validation layer

**Pipeline Flow:**
```
Raw JSON → Validate → Clean → Transform → Load to Tables
    │           │         │         │            │
    └───────────┴─────────┴─────────┴────────────┘
                    │
              Error Queue (manual review)
```

---

## Future Analytics Tasks (Phase 2)

### Task 5: Basic Analytics Queries

**Objective:** Create SQL/Python queries for business insights.

- [ ] 5.1 Daily/Weekly/Monthly revenue
- [ ] 5.2 Top selling items by quantity and revenue
- [ ] 5.3 Average order value (AOV) trends
- [ ] 5.4 Order type distribution (dine-in vs delivery vs takeaway)
- [ ] 5.5 Peak hours analysis
- [ ] 5.6 Customer retention and repeat order rate
- [ ] 5.7 Payment method distribution
- [ ] 5.8 Delivery area heatmap

### Task 6: Advanced Analytics & Predictions

**Objective:** Build predictive models for business optimization.

- [ ] 6.1 **Demand Forecasting:** Predict order volume by day/hour
- [ ] 6.2 **Inventory Planning:** Predict ingredient needs based on popular items
- [ ] 6.3 **Customer Segmentation:** RFM analysis (Recency, Frequency, Monetary)
- [ ] 6.4 **Churn Prediction:** Identify customers likely to stop ordering
- [ ] 6.5 **Menu Optimization:** Identify underperforming items
- [ ] 6.6 **Pricing Analysis:** Price elasticity of demand
- [ ] 6.7 **Basket Analysis:** Market basket / association rules (items bought together)
- [ ] 6.8 **Delivery Time Prediction:** Estimate delivery duration

---

## File Structure (Proposed)

```
analytics/
├── agents.md                    # This documentation file
├── clean_menu_data.py           # Menu item normalization (DONE)
├── cleaned_menu.csv             # Normalized menu items (DONE)
├── fetch_orders.py              # Task 1: API client
├── analyze_schema.py            # Task 2: Payload analysis
├── sample_payloads/             # Raw JSON samples for analysis
│   └── *.json
├── data_cleaning/
│   ├── clean_orders.py          # Task 3: Order data cleaning
│   ├── customer_dedup.py        # Customer deduplication
│   └── item_matcher.py          # Match items to menu_items
├── database/
│   ├── schema.sql               # Task 4: Database schema
│   ├── load_data.py             # Data loading scripts
│   └── migrations/              # Schema migrations
├── analytics/
│   ├── basic_queries.sql        # Task 5: Analytics queries
│   ├── dashboards.py            # Visualization
│   └── predictions/             # Task 6: ML models
│       ├── demand_forecast.py
│       ├── customer_segmentation.py
│       └── basket_analysis.py
└── notebooks/
    ├── exploration.ipynb        # Data exploration
    └── analysis.ipynb           # Analysis notebooks
```

---

## Questions to Clarify (Remaining)

1. **Technical:**
   - Preferred database (PostgreSQL, SQLite, BigQuery)?
   - Where will the final tables live (same Django server, separate DB)?
   - Any existing dashboards/BI tools to integrate with?

2. **Business Requirements:**
   - Which analytics are highest priority?
   - Are there specific KPIs you track today?
   - Do you need real-time analytics or batch is sufficient?

3. **Data Quality:**
   - How are cancelled/refunded orders represented in the payload?
   - Are there any data quality issues we should be aware of?

---

## Next Steps (Immediate)

1. ✅ **Task 1 Complete:** `fetch_orders.py` is ready
2. **Run Schema Analysis:**
   ```bash
   # First, fetch sample orders
   python3 fetch_orders.py
   
   # Then analyze the schema
   python3 analyze_schema.py
   ```
3. **Task 3:** Create `data_cleaning/clean_orders.py` for item name matching
4. **Task 4:** Create database schema SQL and data loading scripts
5. **Test with Sample Data:** Load 100 orders to validate the pipeline

---

## Progress Tracking

| Task | Status | Notes |
|------|--------|-------|
| Task 0: Menu Normalization | ✅ Done | `cleaned_menu.csv` with 138 items |
| Task 1: Fetch Orders API | ✅ Done | `fetch_orders.py` - Ready to use |
| Task 2: Schema Design | 🟡 In Progress | Schema documented, need to run `analyze_schema.py` |
| Task 3: Data Cleaning | 🔲 Not Started | Depends on Task 2 completion |
| Task 4: Table Creation | 🔲 Not Started | Depends on Task 3 |
| Task 5: Basic Analytics | 🔲 Not Started | Depends on Task 4 |
| Task 6: Predictions | 🔲 Not Started | Depends on Task 5 |

**Current Data:**
- ~5,000 orders
- ~10,000 order items
- Expected growth: 300,000 orders in 2 years

---

*Last Updated: January 9, 2026*

