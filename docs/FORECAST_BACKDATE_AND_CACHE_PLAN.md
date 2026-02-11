# Forecast Backdate Overlay, Cache & Cloud Sync — Implementation Plan

**Author:** AI Assistant  
**Date:** 2026-02-11  
**Status:** Implemented (pending testing)

---

## 1. Overview

This plan covers five interconnected changes to the revenue and item-demand forecasting system:

| # | Goal | Summary |
|---|------|---------|
| 1 | **GP backdated overlay** | Show Gaussian Process fitted values for historical dates (last 30 days), matching WeekDay Avg / Holt-Winters / Prophet |
| 2 | **Remove Forecast Audit (Replay Mode)** | The overlay makes the standalone replay chart redundant — remove UI section |
| 3 | **Item demand backdated overlay** | Show ML-based backtest predictions overlaid on historical item sales chart |
| 4 | **Precompute & cache in DB** | Persist all forecast values (revenue + item) in SQLite tables; fill missing dates on demand |
| 5 | **Cloud sync placeholder API** | Push cached forecasts to cloud via the existing shipper pattern (swappable URI) |

### Guiding Principle
> If the first date on the chart X-axis is **11 Jan**, we train using data from **T−90 days** (≈ 13 Oct) before that.

---

## 2. Database Schema Changes

### 2.1 `forecast_cache` — Revenue model predictions

Stores historical fitted values **and** future forecasts for all four models.

```sql
CREATE TABLE IF NOT EXISTS forecast_cache (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    forecast_date DATE NOT NULL,          -- Target date being predicted
    model_name TEXT NOT NULL,             -- 'weekday_avg' | 'holt_winters' | 'prophet' | 'gp'
    generated_on DATE NOT NULL,           -- Business date when forecast was computed
    revenue FLOAT,
    orders INTEGER DEFAULT 0,
    pred_std FLOAT,                       -- GP: standard deviation
    lower_95 FLOAT,                       -- GP: 95% CI lower bound
    upper_95 FLOAT,                       -- GP: 95% CI upper bound
    temp_max FLOAT,                       -- Prophet: temperature regressor
    rain_category TEXT,                   -- Prophet: rain category
    uploaded_at TEXT,                      -- Cloud sync: set after upload
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(forecast_date, model_name, generated_on)
);
CREATE INDEX IF NOT EXISTS idx_forecast_cache_generated ON forecast_cache(generated_on);
CREATE INDEX IF NOT EXISTS idx_forecast_cache_uploaded ON forecast_cache(uploaded_at);
```

### 2.2 `item_forecast_cache` — Item demand predictions

Stores backtest (historical) and forward predictions per item.

```sql
CREATE TABLE IF NOT EXISTS item_forecast_cache (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    forecast_date DATE NOT NULL,          -- Target date
    item_id TEXT NOT NULL,
    generated_on DATE NOT NULL,           -- Business date when computed
    p50 FLOAT,
    p90 FLOAT,
    probability FLOAT,
    recommended_prep INTEGER,
    uploaded_at TEXT,                      -- Cloud sync tracking
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(forecast_date, item_id, generated_on)
);
CREATE INDEX IF NOT EXISTS idx_item_forecast_cache_generated ON item_forecast_cache(generated_on);
CREATE INDEX IF NOT EXISTS idx_item_forecast_cache_uploaded ON item_forecast_cache(uploaded_at);
```

---

## 3. GP Backdated Predictions (Revenue Chart)

### Current State
- WeekDay Avg, Holt-Winters, Prophet all return ~30 days of historical fitted values + 7 days of future predictions.
- GP returns **only** 7-day future predictions.
- The chart shows GP as a pink dashed line only in the forecast zone.

### Change
Add `predict_historical()` to `RollingGPForecaster`:
1. Use the fitted model to predict on the training data (last 30 days).
2. The GP model's `FeatureEngineer` is already fitted; `training_data` has `[ds, temp_max, lag1, lag7]`.
3. Return in-sample fitted values (mean, std, lower, upper) — same approach as HW `fittedvalues` and Prophet `predict()` on historical dates.

### Modified Files
- `src/core/learning/revenue_forecasting/gaussianprocess.py` — add `predict_historical()` method
- `src/api/routers/forecast.py` — call `predict_historical()` and merge results with future predictions in `forecast_gp()`

### Data Flow
```
RollingGPForecaster.load()
    ├── predict_historical(n_days=30) → [ds, pred_mean, pred_std, lower, upper]
    └── predict_next_days(future_weather) → [ds, pred_mean, pred_std, lower, upper]
    → merged into single array → sent to frontend → GP line spans entire chart
```

---

## 4. Remove Forecast Audit (Replay Mode)

### Rationale
With all 4 models showing backdated overlays on the main chart, the separate replay chart (which showed GP-only past predictions vs actuals) is redundant. The main chart now serves the same purpose — users can visually compare each model's fitted values against the historical blue line.

### Change
- **Remove** from `ForecastPage.tsx`: replay state variables, replay data fetching, replay chart section.
- **Keep** the `/api/forecast/replay` endpoint and `forecast_snapshots` table — they're used for DB persistence and will be useful for cloud sync.
- **Keep** the GP snapshot saving during training — feeds the forecast cache.

### Modified Files
- `ui_electron/src/pages/ForecastPage.tsx` — remove replay section (~100 lines)

---

## 5. Item Demand Backdated Overlay (Point-in-Time T→T+1)

### Design
For each historical date D, we train a model on 120 days ending at D-1, then predict D.
This gives "at T, what did we predict for T+1" (e.g. at 11 Jan, predict 12 Jan).

### Cache
- **item_backtest_cache**: (forecast_date, item_id, model_trained_through, p50, p90, probability)
- model_trained_through = forecast_date - 1 day
- Only train when forecast for a backdated date is missing; then cache the result

### Current State
- `ItemDemandForecast.tsx` shows: Historical sales (solid blue) + P50/P90 forecast (dashed green/orange) — forward-looking only.
- No overlay of model predictions on historical dates.

### Change
Add `backtest_items()` function that generates in-sample predictions for the last 30 days:
1. Take the full 90-day historical data (already densified).
2. Build features using actual lag values (no autoregressive feedback needed — we have real data).
3. Run the classifier + regressors to get P50, P90, P(sale) for each (date, item).
4. Return as `backtest` array alongside `forecast`.

### Modified Files
- `src/core/learning/revenue_forecasting/item_demand_ml/predict.py` — add `backtest_items()` function
- `src/api/routers/forecast_items.py` — call `backtest_items()`, add `backtest` to response
- `ui_electron/src/pages/ItemDemandForecast.tsx` — merge backtest data into chart

### Data Flow
```
GET /api/forecast/items
    ├── df_history (90 days) → forecast_items() → future predictions
    └── df_history (90 days) → backtest_items() → historical predictions (last 30d)
    Response: { items, history, forecast, backtest, ... }
```

### Frontend Overlay
- Backtest P50: Dashed green line overlaying on historical period
- Backtest P90: Dotted orange line overlaying on historical period
- Uses same color scheme but with lower opacity for distinction

---

## 6. Forecast Cache Layer

### Item Demand: Read-Through Cache

Item forecasts now use a **cache-first** strategy (matching revenue forecast):

- If `item_forecast_cache` has rows for today's `generated_on`: load from DB, filter by active items and optional `item_id`, split into forecast vs backtest by date, return immediately.
- If cache is empty or stale: compute via `forecast_items()` + `backtest_items()`, save to cache, return.
- Only recomputes when cache is cold (e.g. first request of the day, new business day).

### Purpose
1. **Performance**: Avoid recomputing all 4 revenue models on every page load.
2. **Persistence**: Historical predictions survive app restarts.
3. **Cloud sync**: Cached values are pushed to cloud with `uploaded_at` tracking.
4. **Gap filling**: If a specific date is missing, recompute for that date.

### Cache Strategy (Revenue)
```
On API request:
  1. today = get_current_business_date()
  2. cached = load_revenue_forecasts(conn, generated_on=today)
  3. if cached has all 4 models AND gp has data:
       return cached (fast path)
  4. else:
       compute all 4 models fresh
       save_revenue_forecasts(conn, results, generated_on=today)
       return computed results
```

**GP population fix**: Cache was considered "fresh" with only 3 models. When GP was stale on first compute, `forecast_gp()` returns `[]` and no GP rows were saved. Subsequent requests served from cache forever with empty GP. Fix: require `cached.get("gp")` with non-empty data for cache to be fresh; otherwise fall through to compute.

### Cache Strategy (Items)
Same pattern — check `item_forecast_cache` for today's `generated_on`. If missing or incomplete, recompute and save.

### Gap Filling (Not Implemented)
Neither revenue nor item forecast implements per-date gap filling. Both use an all-or-nothing strategy:
- If cache is incomplete (missing GP, missing models, or empty for today): full recompute.
- Revenue models (HW, Prophet, GP) need the full time series; item models need full history for lag features.

### New Module
`src/core/learning/revenue_forecasting/forecast_cache.py`:
- `ensure_tables_exist(conn)` — CREATE TABLE IF NOT EXISTS
- `save_revenue_forecasts(conn, model_name, forecasts, generated_on)`
- `load_revenue_forecasts(conn, generated_on)` → Dict[model_name, List[Dict]]
- `save_item_forecasts(conn, forecasts, generated_on)`
- `load_item_forecasts(conn, generated_on)` → List[Dict]
- `is_cache_fresh(conn, generated_on)` → bool

---

## 7. Cloud Sync Placeholder API

### Pattern (Existing)
The app uses a modular "shipper" pattern:
- `learning_shipper.py` → uploads `ai_logs`, `ai_feedback`, Tier 3
- `error_shipper.py` → uploads error log records
- `menu_bootstrap_shipper.py` → uploads menu knowledge
- `client_learning_shipper.py` → orchestrates all shippers
- `cloud_sync_scheduler.py` → runs every 5 minutes, reads URL from `system_config`

### New Shipper: `forecast_shipper.py`
```python
def upload_pending(conn, endpoint=None, auth=None, uploaded_by=None):
    """
    Upload forecast_cache and item_forecast_cache rows where uploaded_at IS NULL.
    After successful POST, set uploaded_at = NOW().
    """
```

### New Endpoint in CLOUD_SYNC_API_SPEC.md
```
POST /desktop-analytics-sync/forecasts/ingest
```

**Request body:**
```json
{
  "revenue_forecasts": [
    {
      "forecast_date": "2026-02-11",
      "model_name": "gp",
      "generated_on": "2026-02-11",
      "revenue": 32500.0,
      "pred_std": 5000.0,
      "lower_95": 22700.0,
      "upper_95": 42300.0
    }
  ],
  "item_forecasts": [
    {
      "forecast_date": "2026-02-11",
      "item_id": "uuid-123",
      "generated_on": "2026-02-11",
      "p50": 4.0,
      "p90": 8.0,
      "probability": 0.72
    }
  ],
  "uploaded_by": { "employee_id": "...", "name": "..." }
}
```

### Postgres Schema (Cloud Side)
```sql
CREATE TABLE ingest_revenue_forecasts (
    id BIGSERIAL PRIMARY KEY,
    employee_id VARCHAR(64),
    forecast_date DATE NOT NULL,
    model_name VARCHAR(32) NOT NULL,
    generated_on DATE NOT NULL,
    revenue FLOAT,
    orders INTEGER DEFAULT 0,
    pred_std FLOAT,
    lower_95 FLOAT,
    upper_95 FLOAT,
    temp_max FLOAT,
    rain_category VARCHAR(16),
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
```

### Integration
- Add `upload_pending` call to `client_learning_shipper.py` → `run_all()`
- URL constructed as: `{base_url}/desktop-analytics-sync/forecasts/ingest`
- Bearer token auth (same as other shippers)

---

## 8. Files Modified / Created

| File | Action | Description |
|------|--------|-------------|
| `database/schema_sqlite.sql` | Modified | Add `forecast_cache` + `item_forecast_cache` tables |
| `src/core/learning/revenue_forecasting/gaussianprocess.py` | Modified | Add `predict_historical()` to `RollingGPForecaster` |
| `src/core/learning/revenue_forecasting/forecast_cache.py` | **New** | Cache module: save/load/ensure tables |
| `src/api/routers/forecast.py` | Modified | GP historical predictions + cache layer |
| `src/core/learning/revenue_forecasting/item_demand_ml/predict.py` | Modified | Add `backtest_items()` function |
| `src/api/routers/forecast_items.py` | Modified | Include backtest in response + cache |
| `ui_electron/src/pages/ForecastPage.tsx` | Modified | Remove replay mode section |
| `ui_electron/src/pages/ItemDemandForecast.tsx` | Modified | Add backtest overlay to chart |
| `ui_electron/src/api.ts` | No change | Existing endpoints sufficient |
| `src/core/forecast_shipper.py` | **New** | Cloud sync shipper for forecast data |
| `src/core/client_learning_shipper.py` | Modified | Add forecast shipper to orchestrator |
| `docs/CLOUD_SYNC_API_SPEC.md` | Modified | Add §3.6 Forecasts Ingest + Postgres schema |
| `docs/FORECAST_BACKDATE_AND_CACHE_PLAN.md` | **New** | This document |

---

## 9. Verification Checklist

- [ ] **GP overlay**: GP line (pink dashed) spans entire chart (historical + future)
- [ ] **GP confidence band**: Pink shaded area appears for future dates AND optionally for historical
- [ ] **WeekDay Avg**: No regression — still shows historical + future
- [ ] **Holt-Winters**: No regression — still shows historical + future
- [ ] **Prophet**: No regression — still shows historical + future
- [ ] **Replay section**: Removed from UI (endpoint still works)
- [ ] **Item demand backtest**: Dashed lines overlay on historical sales in Menu Items tab
- [ ] **Forecast cache**: Second page load serves from cache (faster)
- [ ] **Cache gap fill**: If a date is missing from cache, it gets computed and saved
- [ ] **Cloud sync**: `forecast_shipper.py` sends unsent records when URL is configured
- [ ] **API spec**: `docs/CLOUD_SYNC_API_SPEC.md` includes forecasts ingest
- [ ] **No regressions**: All existing forecast models produce same-quality predictions

---

## 10. Business-Date-Aware Staleness (5 AM Boundary Fix)

### Problem
The item demand model's `is_model_stale()` compared the model file's **calendar date**
(from `os.path.getmtime()`) against the current business date. A model trained at 3 AM
(still business day yesterday) had calendar date = today, so it appeared "fresh" even
though it was trained during the previous business day and is missing yesterday's data.

### Fix
1. **Convert file mtime to business date** using `get_business_date_from_datetime()`:
   a model saved at 3 AM Feb 11 belongs to business day Feb 10.
2. **5 AM guard**: Before 5 AM IST, return `False` (not stale) — the previous business
   day hasn't ended yet, so retraining would still miss the final hours of data.
3. **Applied consistently** to both item demand (`model_io.is_model_stale()`) and
   GP model (`forecast.py:_load_and_check_stale()`).

### Trace (after fix)
```
Model trained at 3:00 AM Feb 11 IST (business day Feb 10):
  trained_biz = get_business_date_from_datetime(3am Feb 11) = "2026-02-10"
  today_biz   = get_current_business_date() at 12:35pm     = "2026-02-11"
  trained_biz < today_biz → True → STALE ✓ (triggers retrain)
```

### Revenue Models (WeekDay Avg, Holt-Winters, Prophet)
These models don't have persistent files — they recompute from scratch each request.
The cache layer uses `generated_on = get_current_business_date()` which already
respects the 5 AM boundary. No additional fix needed.

---

## 11. Risks & Mitigations

| Risk | Mitigation |
|------|-----------|
| GP in-sample predictions are too tight (overfitting appearance) | Expected for GP — in-sample std is low. Same behavior as HW fittedvalues. |
| Item backtest slow (30 days × N items) | Use vectorized prediction on all items at once per date. No autoregressive needed for historical. |
| Cache stale if business date changes mid-session | Cache is keyed by `generated_on`; stale entries are ignored. |
| Cloud sync payload too large | Batch by model and date range; use BATCH_LIMIT like learning_shipper. |
