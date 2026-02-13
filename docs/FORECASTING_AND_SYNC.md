# Forecasting & Cloud Sync Documentation

**Combined Documentation**
**Last Updated:** February 2026

---

# PART 1: FORECASTING ARCHITECTURES

## 1. Revenue Forecasting

The system employs a "Committee of Machines" approach, running four distinct algorithms in parallel to provide a range of perspectives on future revenue.

### 1.1 Data Source & Preparation
- **Window**: Rolling window of the last **90 days** of sales history.
- **Source**: Daily aggregation of successful orders (`order_status = 'Success'`).
- **Missing Data**: The system automatically detects missing dates (e.g., closures) and fills them with `0` revenue to ensure a continuous time-series.

### 1.2 Algorithms

#### A. Weekday Average (Baseline)
A heuristic model optimized for weekly seasonality.
- **Logic**: Average revenue of the *last 4 same-weekdays*.
- **Formula**: $\hat{y}_{t} = \frac{1}{4} \sum_{i=1}^{4} y_{t - 7i}$
- **Pros**: Robust to outliers, interprets "same-day-of-week" behavior strongly.
- **Cons**: Slow to react to recent trends.

#### B. Holt-Winters (Exponential Smoothing)
A statistical model decomposing data into Level, Trend, and Seasonality.
- **Config**: Additive trend, Additive seasonality (7-day period), Damped trend.
- **Best For**: Short-term forecasting where recent data is weighted more heavily.

#### C. Prophet (Meta)
A generalized additive model designed for business time series.
- **Config**: Weekly seasonality=True, Changepoint prior=0.05. Uses `temp_max` and `rain_category` as regressors.
- **Best For**: Handling irregularities and finding the underlying curve.

#### D. Gaussian Process (GP)
A probabilistic model that provides uncertainty estimates.
- **Implementation**: `RollingGPForecaster`.
- **Feature**: Provides a 95% Confidence Interval (upper/lower bounds).
- **Backtesting**: Unlike the other models which fit in-sample, the GP implementation performs a **rolling point-in-time backtest** for the last 30 days (training on $t-N$ to predict $t$) to avoid evaluation leakage. This overlay is shown on charts to build user trust.

### 1.3 Caching Strategy
To improve performance and enable offline support, forecasts are cached in SQLite (`forecast_cache` table).
- **Cache-First**: On API request, checks for fresh data (generated today).
- **Staleness**: Models adhere to a **5 AM Business Day** boundary. A model trained at 3 AM belongs to the previous business day and is considered stale after 5 AM.
- **Persistence**: Historical predictions ("backtests") are persisted to allow "at time T, what did we predict for T+1?" analysis.

---

## 2. Item-Level Demand Forecasting

Designed for intermittent retail demand (sparse sales, high zero days) where traditional time-series models fail.

### 2.1 The Challenge
- **Sparsity**: ~50% of days have zero sales for many items.
- **Burstiness**: Sales jump from 0 to 12 randomly.
- **Drivers**: Strong day-of-week and weather influence.

### 2.2 Modeling Strategy: Two-Stage Hurdle Model
We model the **probability of sale** separately from the **quantity sold**.

1.  **Stage 1: Classification (Probability)**
    - **Model**: Calibrated LightGBM Classifier (Isotonic Regression).
    - **Output**: Probability $P(\text{sale} > 0)$.

2.  **Stage 2: Regression (Quantity)**
    - **Model**: LightGBM Regressor (Quantile Objective).
    - **Output**: Predicted P50 (Median) and P90 (Conservative Upper Bound) quantities, conditional on a sale occurring.

**Final Decision Rule:**
$$ \text{Recommended Prep} = \lceil\, 0.7 \times P_{90} + 0.3 \times P_{50} \,\rceil $$
This blends the likely outcome (P50) with a safety buffer (P90) to minimize stockouts while controlling waste.

### 2.3 Global Model
A single global model is trained across all items (using item encoding) to leverage shared patterns (e.g., "hot weekends boost fruit flavors") and solve the Code Start problem for new items.

---

## 3. Volume Forecasting

Predicts **volume** (gms, ml, units) rather than order counts. Mirrors the Item Demand architecture.

### 3.1 Volume Aggregation
- **Source**: `order_items` joined with `variants`.
- **Calculation**: $\sum (\text{quantity} \times \text{variant\_value})$.
- **Units**:
    - **Count**: 1 unit (e.g., Pastries).
    - **mg/ml**: Weight/Volume (e.g., Ice Cream, Shakes). Stored normalized in mg (1 ml = 1 g = 1000 mg).

### 3.2 Strategy
- **Entity**: Aggregated by `menu_item_id`.
- **Model**: Same Two-Stage LightGBM approach as Item Demand.
- **Output**: P50/P90 volume predictions for kitchen prep planning (e.g., "Defrost 5kg of Chocolate Ice Cream").

---

# PART 2: CLOUD SYNC API SPECIFICATION

**Audience:** Cloud server implementers (Django + PostgreSQL).
**Purpose:** Define the HTTP API and database schema for syncing errors, learning data, menu bootstrap, and forecasts from the analytics client to the cloud.

## 1. Overview

| Endpoint (client → cloud) | Method | Purpose |
|----------------------------|--------|---------|
| `/desktop-analytics-sync/errors/ingest` | POST | Batch of error/crash log records |
| `/desktop-analytics-sync/learning/ingest` | POST | AI logs, feedback, cache stats, counters |
| `/desktop-analytics-sync/menu-bootstrap/ingest` | POST | Menu knowledge (id_maps, cluster_state) |
| `/desktop-analytics-sync/conversations/sync` | POST | AI conversations + messages |
| `/desktop-analytics-sync/forecasts/ingest` | POST | Revenue, Item, and Volume forecasts + Backtests |
| `/desktop-analytics-sync/forecasts/bootstrap` | GET | Download latest forecasts (for fresh installs) |

## 2. Authentication
- **Header**: `Authorization: Bearer <API_KEY>` (client configured).
- **Validation**: Server must validate token.

## 3. Endpoints & Schema

### 3.1 Forecasting Ingest (`/forecasts/ingest`)
Syncs cached revenue, item, and volume predictions.

**Request Body:**
```json
{
  "revenue_forecasts": [
    { "forecast_date": "2026-02-11", "model_name": "gp", "revenue": 32500.0, "lower_95": 22000.0, ... }
  ],
  "item_forecasts": [
    { "forecast_date": "2026-02-11", "item_id": "uuid", "p50": 4.0, "p90": 8.0, ... }
  ],
  "volume_forecasts": [
    { "forecast_date": "2026-02-11", "item_id": "uuid", "volume_value": 2500.0, "unit": "mg", ... }
  ],
  "revenue_backtest": [...],
  "item_backtest": [...],
  "volume_backtest": [...]
}
```

**Postgres Schema (Forecasts):**
```sql
CREATE TABLE ingest_revenue_forecasts (
    id BIGSERIAL PRIMARY KEY,
    employee_id VARCHAR(64),
    forecast_date DATE NOT NULL,
    model_name VARCHAR(32) NOT NULL,
    generated_on DATE NOT NULL,
    revenue FLOAT,
    pred_std FLOAT,
    lower_95 FLOAT,
    upper_95 FLOAT,
    ingested_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(forecast_date, model_name, generated_on, employee_id)
);

CREATE TABLE ingest_item_forecasts (
    id BIGSERIAL PRIMARY KEY,
    employee_id VARCHAR(64),
    forecast_date DATE NOT NULL,
    item_id VARCHAR(64) NOT NULL,
    generated_on DATE NOT NULL,
    p50 FLOAT,
    p90 FLOAT,
    probability FLOAT,
    recommended_prep INTEGER,
    ingested_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(forecast_date, item_id, generated_on, employee_id)
);

CREATE TABLE ingest_volume_forecasts (
    id BIGSERIAL PRIMARY KEY,
    employee_id VARCHAR(64),
    forecast_date DATE NOT NULL,
    item_id VARCHAR(64) NOT NULL,
    generated_on DATE NOT NULL,
    volume_value FLOAT NOT NULL,
    unit VARCHAR(16) NOT NULL,
    p50 FLOAT,
    p90 FLOAT,
    ingested_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(forecast_date, item_id, generated_on, employee_id)
);
```
*(Backtest tables `ingest_revenue_backtest`, `ingest_item_backtest`, `ingest_volume_backtest` follow similar structure with `model_trained_through` instead of `generated_on`)*.

### 3.2 Learning Ingest (`/learning/ingest`)
Syncs AI usage logs and feedback.

**Postgres Schema (Learning):**
```sql
CREATE TABLE ingest_ai_logs (
    query_id UUID NOT NULL UNIQUE,
    user_query TEXT,
    intent VARCHAR(128),
    sql_generated TEXT,
    response_type VARCHAR(32),
    created_at TIMESTAMPTZ
);

CREATE TABLE ingest_ai_feedback (
    feedback_id BIGINT NOT NULL,
    query_id UUID NOT NULL,
    is_positive BOOLEAN,
    comment TEXT
);
```

### 3.3 Menu Bootstrap (`/menu-bootstrap/ingest`)
Syncs ID mappings and cluster states for cold-start bootstrapping.

```sql
CREATE TABLE ingest_menu_bootstrap (
    id_maps JSONB NOT NULL,
    cluster_state JSONB NOT NULL
);
```

### 3.4 Errors Ingest (`/errors/ingest`)
Syncs application crash logs.

```sql
CREATE TABLE ingest_error_records (
    record_id VARCHAR(64) NOT NULL UNIQUE,
    message TEXT,
    traceback TEXT,
    context JSONB
);
```

### 3.5 Conversations Sync (`/conversations/sync`)
Syncs chat history.

```sql
CREATE TABLE ingest_conversations (
    conversation_id UUID NOT NULL UNIQUE,
    title VARCHAR(512),
    messages JSONB -- or separate table
);
```
