# Analytics Project - Order Management System

Complete order management and analytics pipeline for cafe business.

## Quick Start

### 1. Fetch Sample Orders
```bash
python3 fetch_orders.py
```
This will fetch 100 sample orders and save to `sample_payloads/sample_orders_100.json`

### 2. Analyze Schema
```bash
python3 analyze_schema.py
```
This analyzes the order structure and generates `docs/schema_analysis.md`

### 3. Fetch All Orders (Full Sync)
```python
from fetch_orders import fetch_stream_raw

all_orders = fetch_stream_raw(
    endpoint="orders",
    save_to_file="raw_data/all_orders.json"
)
```

### 4. Incremental Updates
```python
from fetch_orders import fetch_orders_incremental

# Fetch new orders since last_stream_id
new_orders = fetch_orders_incremental(
    last_stream_id=1000,
    save_to_file="raw_data/new_orders.json"
)
```

## Project Structure

```
analytics/
├── agents.md                    # Complete task documentation
├── README.md                    # This file
├── clean_menu_data.py           # Menu normalization (DONE)
├── cleaned_menu.csv             # Normalized menu items (138 items)
├── fetch_orders.py              # Task 1: API client (DONE)
├── analyze_schema.py            # Task 2: Schema analysis
├── sample_payloads/             # Sample order JSON files
├── raw_data/                    # Full order datasets
├── docs/                        # Documentation
│   └── schema_analysis.md       # Generated schema docs
├── data_cleaning/               # Task 3: Data cleaning scripts
├── database/                    # Task 4: Schema & loading scripts
└── analytics/                   # Task 5-6: Analytics & predictions
```

## Current Status

- ✅ **Task 0:** Menu normalization complete (138 items)
- ✅ **Task 1:** Order fetching API complete
- 🟡 **Task 2:** Schema analysis in progress
- 🔲 **Task 3:** Data cleaning (next)
- 🔲 **Task 4:** Database creation
- 🔲 **Task 5:** Analytics queries
- 🔲 **Task 6:** Predictions

## Data Volume

- Current: ~5,000 orders, ~10,000 order items
- Expected: 300,000 orders in 2 years

## API Configuration

```python
BASE_URL = "https://webhooks.db1-prod-dachnona.store/analytics"
API_KEY = "f3e1753aa4c44159fa7218a31cd8db1e"
```

See `agents.md` for complete documentation.

