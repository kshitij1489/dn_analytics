# Central Forecasting Nightly Job Implementation Plan

Prepared: 2026-07-08 IST<br>
Last updated: 2026-08-08 IST (multi-restaurant cutover; earlier: 2026-07-09 production deploy handoff)<br>
Target repo: `db.dachnona` (central server) + `analytics` (desktop cutover, Phase 5)<br>
Source algorithm repo reviewed: `analytics`

> **As-built delta (multi-restaurant cutover, central production 2026-08-08).**
> This document is the original design plan; where its sketches disagree with the
> shipped central code, the code is authoritative. The differences that matter to
> a desktop reader:
>
> - **Scope is per restaurant, never global and never defaulted.** There is no
>   `scope_key = "default"` design constant and no model field default; every
>   scoped table carries a database check that `scope_key` is present and
>   non-blank. The central nightly job runs per restaurant and takes a per-scope
>   lock. Clients never name a scope: `scope_key` as a query parameter is refused
>   with `400 retired_parameter` (contract §24), and a request selects its
>   restaurant with the required `X-Restaurant-ID` header (§23).
> - **Weather is cached per location, not per city** — `location_key` unique with
>   `business_date`, and a row is reused only when the full stored profile matches.
> - **`forecasts/bootstrap` is a `404`.** `forecasts/central-bootstrap` is the only
>   name; the alias was removed in contract 1.2.
> - **There is no locator import.** The server materializes assignment locators
>   from its own current order-line facts; a client-supplied `order_line_key` on
>   an assignment write is refused with `400 retired_field`.

## Implementation status (2026-08-08)

| Phase | Scope | Status |
|-------|--------|--------|
| **0** | Contracts, order-line key decision, API namespace | **Done** |
| **1** | Central revenue forecasts (models, nightly job, delta API) | **Deployed** — revenue family live in production |
| **2** | Order-line identity + assignment locator coverage | **Deployed** — locators materialized server-side |
| **3** | Central item demand forecasts | **Deployed** (code) — publish gated until assignment coverage ≥ 95% |
| **4** | Central volume forecasts + variant unit/value | **Deployed** (code) — publish gated until assignment coverage ≥ 95% |
| **5** | Desktop pull-only cutover | **Done** — central cache pull, projection (revenue/items/volume), router cutover, UI/ML cleanup |

Item and volume publishing is gated by the 95% assignment-coverage threshold, which a restaurant crosses once it has assignment state. The original restaurant's forced revenue run succeeded; the second restaurant's failed on short history (needs at least eight positive-revenue business dates) and is a data-maturity follow-up. Desktop Phase 1 uses the sole contract-1.2 `forecasts/central-bootstrap` route and keeps forecast cursors/caches profile-local.

**API verification (prod):**

```bash
TOKEN="<sync-read-token>"
curl -sk -H "Host: webhooks.db1-prod-dachnona.store" \
  -H "Authorization: Bearer $TOKEN" \
  -H "X-Restaurant-ID: <id>" \
  "https://127.0.0.1/desktop-analytics-sync/forecasts/status/" | python3 -m json.tool
```

A `failed` run from an intentional `--dry-run-extract` is expected and does not block serving; APIs serve the latest `status=success` run.

## Goal

Move forecasting computation completely out of the desktop analytics app and into the central Django/PostgreSQL server. The central server becomes the only place that trains, scores, stores, and publishes sales, item-demand, volume, and weather-backed forecasts.

The desktop app becomes a pull-only forecast consumer. During Sync DB it pulls server-authored forecast deltas, stores them in local forecast cache tables, and renders existing charts from that cache. It must not train, backtest, upload, or repair forecasts locally.

## Owner Direction / Target Behavior

- Forecasting is independent of the desktop app. Desktop code should contain no production ML/model logic after cutover.
- Treat the central forecasting schema as a fresh, clean-slate database surface. No forecast backfill or backward-compatibility migration is required.
- The nightly job runs once per completed business day and publishes that generated date. It should not scan for missing generated dates or run automatic gap filling.
- If a nightly run fails, keep serving the previous successful forecast. Operators can run a one-time command for the failed/backdated `generated_on` date when needed.
- Forecast sync is downstream-only. The desktop never uploads forecast rows; multiple installations converge because they all pull the same server-authored rows.
- The central implementation should include the weather API/data needed by the existing forecast behavior.
- Prefer a simple published-row API that the desktop can cache locally over a bidirectional cache-ingest design.

## Forecasting Behavior Ported To Central

The desktop ML tree was deleted in Phase 5. This section is the behavioral spec the central implementation reproduces.

### Revenue Forecasting

Behavior:

- Builds a daily revenue series from successful orders only.
- Uses the 5 AM IST business-day boundary. In SQLite this is `DATE(created_on, '-5 hours')`.
- Pulls about 120 days for training so lag features are available, then uses a rolling 90-day model window for GP.
- Fills missing calendar dates with zero revenue for revenue models.
- Excludes the current incomplete business day from training.
- Produces 7 forward days and 30 historical backtest/overlay days.

Models:

- `weekday_avg`: average revenue/orders from the last 4 matching weekdays.
- `holt_winters`: statsmodels exponential smoothing, additive trend, additive weekly seasonality, damped trend.
- `prophet`: Prophet with weekly seasonality and weather regressors `temp_max` and `rain_sum`.
- `gp`: Gaussian Process over log revenue with cyclic year/week features, standardized temperature, lag1, and lag7. Current GP rolling historical overlay is point-in-time, not in-sample interpolation.

### Item Demand Forecasting

Behavior:

- Builds item-day rows from `order_items` joined to `orders` and `menu_items`.
- Filters successful orders.
- Uses verified/current menu mapping by `menu_item_id`.
- Densifies sparse item sales into a complete date by item grid.
- Marks active items as items sold in the last 14 days.
- Produces 14 forward days and 30 historical point-in-time backtest days.

Model:

- Two-stage global hurdle model.
- Stage 1: calibrated LightGBM classifier, falling back to XGBoost, for `P(quantity_sold > 0)`.
- Stage 2: LightGBM/XGBoost quantile regressors for P50 and P90 quantity conditional on sale.
- Final forecast multiplies probability by conditional quantity.
- Recommended prep is `ceil(0.7 * p90 + 0.3 * p50)`.

Feature safeguards worth preserving:

- Densified zero-sale rows so the classifier sees negative examples.
- Calendar-day lag features, not previous sale-event lag features.
- Store-wide demand context that excludes the current item to avoid leakage.
- Price-ratio features.
- Category/global priors for cold starts.
- Quantile crossing correction so P90 is never below P50.
- Autoregressive multi-day prediction where each predicted day feeds into the next day lags.

### Volume Forecasting

Variant unit/value metadata inference lives in `utils/variant_metadata.py` (still present in `analytics`).

Behavior:

- Entity is `menu_item_id`, not variant.
- Aggregates order lines by menu item and business day.
- Converts variant quantities into a normalized target:
  - `COUNT` -> units
  - `ML`, `GMS`, `G` -> grams/ml-equivalent as `g`
  - `KG` -> `value * 1000`
  - `MG` -> `value / 1000`
- Excludes mixed-unit menu items that have both count and mass/volume variants.
- Uses the same two-stage hurdle architecture as item demand.
- Normalizes positive target by each item's median volume during regression and denormalizes at inference time.
- Recommended volume is `ceil(0.7 * p90 + 0.3 * p50)`.

## Important Findings From The Review

These should be addressed in the central implementation.

1. Revenue forecasting can be centralized immediately from central order facts. Item and volume forecasting need extra identity work first.

   The central server currently stores `analytics.order_item_fact.order_item_id` as Petpooja `itemid`. The menu assignment sync table `order_item_assignments.order_item_id` is keyed by the desktop SQLite `order_items.order_item_id` autoincrement primary key. Those are not the same identifier. Without a stable order-line key shared by central raw order facts and desktop assignment sync, central item/volume forecasts cannot reliably join order lines to verified menu mappings.

2. Central catalog variants lack volume metadata.

   The desktop `variants` table has `unit` and `value`. Central `MenuCatalogVariant` currently has `variant_id`, `variant_name`, and `is_verified`, but no `unit` or `value`. Volume forecasting needs unit/value. The server should either store these fields in the normalized catalog or infer them consistently with `utils/variant_metadata.py` and surface unknowns for cleanup.

3. Existing item forecast SQL has a SQLite-only `GROUP BY` bug.

   `forecast_items.py` selects `oi.unit_price` while grouping by business date and menu item. SQLite accepts this; PostgreSQL will reject it. The server extractor must use an explicit aggregate such as `AVG(unit_price)` or `MAX(unit_price)`.

4. Cache freshness is currently row-existence based.

   Local cache helpers often treat a generated date as fresh if any rows exist, while route handlers separately check completeness. The central server should publish only completed forecast runs via a `forecast_runs` table and serve `latest status=success` rows. Clients should never see partial nightly output.

5. Model freshness should not use model-file mtime.

   Desktop item/volume freshness uses artifact modification time. On a server with containers, restored volumes, multiple workers, and deploys, this is fragile. Store freshness in `forecast_runs.generated_on`, `training_window_end`, `completed_at`, and artifact version metadata.

6. Current code mixes computation and HTTP handlers.

   Desktop routers contain training, extraction, cache writes, and API response shaping. On the server, computation should live in services/management commands/Celery tasks; API handlers should be read-only.

7. Volume unit naming is inconsistent in docs/comments.

   Some comments say `mg`, but current code returns `g` or `units`. The central contract should explicitly use `unit: "g"` for mass/volume and `unit: "units"` for count-like products. Keep `volume_value`, `p50`, `p90`, and `recommended_volume` in that unit.

8. Forecast dependencies are absent from the central server image.

   `backend/requirements.txt` currently lacks `pandas`, `numpy`, `statsmodels`, `prophet`, `scikit-learn`, `joblib`, `lightgbm`, and `xgboost`. The Dockerfile has `gcc` but may need additional native dependencies depending on final Prophet/LightGBM wheels. Prefer pinned versions and a dedicated forecast worker image/queue.

9. Celery task limits may be too short.

   Current global hard limit is 30 minutes. Full retraining plus point-in-time backtests can exceed that. Split work per forecast family and/or set task-specific limits.

10. Weather data is local-only today.

   The desktop stores `weather_daily` with observed values and forecast snapshots. Central server uses Open-Meteo API (`FORECAST_WEATHER_LAT` / `FORECAST_WEATHER_LON` env vars, default Gurugram 28.4595°N 77.0266°E). See Day-One section for first-run fetch + fallback strategy.

### Findings grounded in the current central schema

Confirmed by reading `analytics/management/commands/{bootstrap_analytics,run_analytics_ingestor,rebuild_order_fact}.py`.

11. Revenue dedup is already solved by the projector; do not re-dedup in the extractor.

    `analytics.order_fact` is a **latest-per-orderID projection**: `run_analytics_ingestor` does `DELETE ... WHERE aggregate_id = orderID` then re-inserts the newest event, so there is exactly one row per order. The revenue extractor must therefore `SUM(total)` directly over `order_fact` and must NOT try to pick a latest revision itself. This dependency is load-bearing: if anyone repoints the extractor at the raw `core_event` stream instead of `order_fact`, revenue will double-count. State this contract explicitly in the extractor docstring and cover it with the "dedupe latest order revisions" test.

12. `order_fact` has no typed or indexed extraction columns.

    Schema is `(stream_id, event_id, aggregate_type, aggregate_id, event_type, occurred_at, raw_event JSONB)` with only a `stream_id` index. Filtering `status = 'Success'` and computing the IST business date both require parsing `raw_event` JSON for every row. A nightly `GROUP BY business_date` over the full table is a sequential scan plus per-row JSON parse that grows unbounded with history. Fix with expression indexes or stored generated columns (see PostgreSQL Extraction Rules) so extraction is bounded by the training window, not table size.

13. `order_item_fact` cannot currently support item/volume extraction at all.

    Schema is `(stream_id, event_id, order_pk, order_item_id, raw_item JSONB)`, where `order_item_id = raw_item->>'itemid'` (Petpooja itemid). There is no line index, no order status, no business date, no quantity/price columns, and no join key to menu assignments. This both confirms Finding 1 (no stable per-line identity) and means the item/volume extractors need the denormalized typed columns proposed in the order-line prerequisite, or an expensive `order_pk -> order_fact.aggregate_id` join on every run. Prefer denormalizing `business_date`, `order_status`, `quantity`, `unit_price`, `line_index`, and `order_line_key` into `order_item_fact` inside the projector.

14. No one-shot incremental projector command exists.

    Job step 6 assumes a "safe incremental projector command," but today there are only `run_analytics_ingestor` (an infinite `while True` `LISTEN/NOTIFY` loop) and `rebuild_order_fact` (a full rebuild). The nightly job must not launch the infinite loop and must not full-rebuild every night. Add a `project_pending --once` command that runs a single `project_new_events` pass to the current watermark and exits, and call that before extraction. Keep `rebuild_order_fact` for recovery only.

15. Forecast upload compatibility should not shape the new design.

    The forecasting system is server-authored and downstream-only. Do not add code paths where desktop clients upload generated forecast rows, request automatic backfills, or participate in model freshness decisions.

## Recommended Target Architecture

Add a new Django app named `forecasting`.

Do not put forecast computation in the `analytics` app. The `analytics` app is documented as read-only and no business logic. Do not put the main server-authored forecast model in `desktop_analytics_app_sync`; that app should expose only the authenticated desktop sync/read endpoints. `forecasting` should own computation, durable run metadata, weather integration, and published forecast rows.

Proposed module structure:

```text
backend/forecasting/
  __init__.py
  apps.py
  constants.py
  models.py
  urls.py
  views.py
  serializers.py
  tasks.py
  admin.py
  management/commands/run_nightly_forecast.py
  services/
    business_date.py
    weather.py
    extractors.py
    revenue.py
    item_demand/
      dataset.py
      features.py
      train.py
      predict.py
      model_io.py
      backtest.py
    volume_demand/
      dataset.py
      features.py
      train.py
      predict.py
      model_io.py
      backtest.py
    publish.py
    responses.py
```

Import/adapt the algorithm modules from the desktop repo rather than importing across repos at runtime.

## Required Data Model

### Forecast Runs

Use this as the publish gate for all APIs.

```python
class ForecastRun(models.Model):
    run_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    scope_key = models.CharField(max_length=64, default="default", db_index=True)
    generated_on = models.DateField(db_index=True)
    training_window_start = models.DateField(null=True, blank=True)
    training_window_end = models.DateField(null=True, blank=True)
    order_fact_max_stream_id = models.BigIntegerField(null=True, blank=True)
    started_at = models.DateTimeField()
    completed_at = models.DateTimeField(null=True, blank=True)
    failed_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=16)  # running | success | failed | superseded
    code_version = models.CharField(max_length=64, blank=True)
    metrics = models.JSONField(default=dict, blank=True)
    artifact_paths = models.JSONField(default=dict, blank=True)
    error_message = models.TextField(blank=True)
```

Indexes:

- `(scope_key, generated_on, status)`
- `(scope_key, completed_at)`
- Publish rule (make this mandatory, not optional): a partial unique index enforcing at most one active `success` run per `(scope_key, generated_on)`:

  ```sql
  CREATE UNIQUE INDEX uniq_active_success_run
    ON forecasting_forecastrun (scope_key, generated_on)
    WHERE status = 'success';
  ```

  This makes double-publish a database error rather than a silent data race. The supersede-old + mark-new-success transition (job steps 13–14) must run in a single DB transaction so readers never observe zero or two success rows. Reads should still defensively order by `(generated_on DESC, completed_at DESC) LIMIT 1`.

### Revenue Forecast Points

One table can store forward and backtest rows.

```python
class ForecastRevenuePoint(models.Model):
    run = models.ForeignKey(ForecastRun, on_delete=models.CASCADE, related_name="revenue_points")
    scope_key = models.CharField(max_length=64, db_index=True)
    generated_on = models.DateField(db_index=True)
    forecast_date = models.DateField(db_index=True)
    kind = models.CharField(max_length=16)  # forward | backtest
    model_name = models.CharField(max_length=32)
    model_trained_through = models.DateField(null=True, blank=True)
    revenue = models.FloatField(null=True, blank=True)
    orders = models.IntegerField(default=0)
    pred_std = models.FloatField(null=True, blank=True)
    lower_95 = models.FloatField(null=True, blank=True)
    upper_95 = models.FloatField(null=True, blank=True)
    temp_max = models.FloatField(null=True, blank=True)
    rain_category = models.CharField(max_length=16, null=True, blank=True)
```

Constraints/indexes:

- Unique `(run, kind, forecast_date, model_name)`.
- Index `(scope_key, generated_on, kind, model_name, forecast_date)`.

### Item Forecast Points

```python
class ForecastItemPoint(models.Model):
    run = models.ForeignKey(ForecastRun, on_delete=models.CASCADE, related_name="item_points")
    scope_key = models.CharField(max_length=64, db_index=True)
    generated_on = models.DateField(db_index=True)
    forecast_date = models.DateField(db_index=True)
    kind = models.CharField(max_length=16)  # forward | backtest
    item_id = models.CharField(max_length=128, db_index=True)
    item_name = models.CharField(max_length=512, blank=True)
    model_trained_through = models.DateField(null=True, blank=True)
    p50 = models.FloatField(null=True, blank=True)
    p90 = models.FloatField(null=True, blank=True)
    probability = models.FloatField(null=True, blank=True)
    recommended_prep = models.IntegerField(null=True, blank=True)
```

Constraints/indexes:

- Unique `(run, kind, forecast_date, item_id)`.
- Index `(scope_key, generated_on, kind, item_id, forecast_date)`.

### Volume Forecast Points

```python
class ForecastVolumePoint(models.Model):
    run = models.ForeignKey(ForecastRun, on_delete=models.CASCADE, related_name="volume_points")
    scope_key = models.CharField(max_length=64, db_index=True)
    generated_on = models.DateField(db_index=True)
    forecast_date = models.DateField(db_index=True)
    kind = models.CharField(max_length=16)  # forward | backtest
    item_id = models.CharField(max_length=128, db_index=True)
    item_name = models.CharField(max_length=512, blank=True)
    unit = models.CharField(max_length=16)  # g | units
    model_trained_through = models.DateField(null=True, blank=True)
    volume_value = models.FloatField(null=True, blank=True)
    p50 = models.FloatField(null=True, blank=True)
    p90 = models.FloatField(null=True, blank=True)
    probability = models.FloatField(null=True, blank=True)
    recommended_volume = models.FloatField(null=True, blank=True)
```

Constraints/indexes:

- Unique `(run, kind, forecast_date, item_id)`.
- Index `(scope_key, generated_on, kind, item_id, forecast_date)`.

### Weather

```python
class ForecastWeatherDaily(models.Model):
    business_date = models.DateField()
    city = models.CharField(max_length=128, default="Gurugram")
    temp_max = models.FloatField(null=True, blank=True)
    temp_min = models.FloatField(null=True, blank=True)
    temp_mean = models.FloatField(null=True, blank=True)
    precipitation_sum = models.FloatField(null=True, blank=True)
    rain_sum = models.FloatField(null=True, blank=True)
    weather_code = models.IntegerField(null=True, blank=True)
    forecast_snapshot = models.JSONField(default=dict, blank=True)
    observed_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)
```

Unique `(business_date, city)`.

### Forecast Staging Tables

These can be Django models or materialized SQL tables. They are not the user-facing source of truth; they exist to make nightly training fast and auditable.

```text
forecast_daily_sales
  scope_key
  business_date
  revenue
  orders
  temp_max
  rain_sum
  weather_code

forecast_item_daily_sales
  scope_key
  business_date
  item_id
  item_name
  category
  price
  quantity_sold
  temperature
  rain

forecast_volume_daily_sales
  scope_key
  business_date
  item_id
  item_name
  category
  unit
  price
  volume_sold
  temperature
  rain
```

Refresh these at the start of each forecast run for the 120-day training window plus any backtest windows.

## Required Prerequisite: Stable Order-Line Identity

Central item and volume forecasting should not join raw order lines to menu assignments with the current IDs.

Add a stable `order_line_key` and include it everywhere an order line assignment is synced.

**Decision (2026-07-08): ship the deterministic SHA-256 key now.** Petpooja exposes no stable per-line unique id today, so the SHA-256 recipe below is the join identity. If Petpooja later adds a true per-line id, migrate to it then; until then this is authoritative.

Recommended deterministic key:

```text
sha256(
  petpooja_order_id + ":" +
  zero_based_line_index + ":" +
  petpooja_itemid + ":" +
  raw_item_name + ":" +
  quantity + ":" +
  total
)
```

Better: if Petpooja exposes a true per-line unique id in future, use that.

Note on `line_index`: the current projector expands order lines with `jsonb_array_elements(... 'OrderItem')`, which does not expose position. To capture a stable, reproducible `line_index` the projector must switch to `jsonb_array_elements(...) WITH ORDINALITY` and store `ordinality - 1`. The desktop ingest must compute the key from the same zero-based array order so both sides agree byte-for-byte; document the exact field order and normalization (trim, casing, numeric formatting) used in the sha256 input, or the keys will silently diverge and joins will miss.

Server changes:

- Extend `analytics.order_item_fact` with:
  - `petpooja_order_id`
  - `line_index`
  - `order_line_key`
  - `created_on`
  - `business_date`
  - `order_status`
  - `quantity`
  - `unit_price`
  - `total_price`
- Add a unique index on `order_line_key`.
- Extend `OrderItemAssignment` or add `OrderItemAssignmentLocator`:

```python
class OrderItemAssignmentLocator(models.Model):
    scope_key = models.CharField(max_length=64, db_index=True)
    order_line_key = models.CharField(max_length=64, db_index=True)
    order_item_id = models.CharField(max_length=128, db_index=True)  # legacy desktop key
    petpooja_order_id = models.CharField(max_length=128, blank=True)
    petpooja_itemid = models.CharField(max_length=128, blank=True)
    menu_item_id = models.CharField(max_length=128)
    variant_id = models.CharField(max_length=128, null=True, blank=True)
    is_verified = models.BooleanField(default=True)
    updated_at = models.DateTimeField(auto_now=True)
```

Client/sync contract changes:

- Include `order_line_key` in assignment snapshot rows and mutation commit payloads.
- During local ingest, compute the same key from the raw order payload and line index before insertion.
- Keep legacy `order_item_id` for existing replay compatibility.

Backfill:

- For historical central data, rebuild `analytics.order_item_fact` with `order_line_key`.
- Desktop client should ship an assignment baseline including `order_line_key`.
- Until this baseline exists, central item/volume forecasts should either be disabled or labeled as raw-item forecasts, not verified menu forecasts.

## Required Prerequisite: Variant Unit/Value Metadata

**Source of truth is the central menu catalog, not a forecast-specific sync.** Central menu and order tables are the only source of truth. The forecasting app reads `unit`/`value` from the central `MenuCatalogVariant`; it never pulls variant metadata from the desktop app and never defines its own variant sync. Forecast computation touches only central tables.

This is therefore a one-time **menu-catalog** change (owned by the menu sync path), done once, after which the central catalog owns `unit`/`value` permanently and forecast just joins to it.

Extend central variant catalog:

```python
class MenuCatalogVariant(models.Model):
    # existing fields...
    unit = models.CharField(max_length=16, null=True, blank=True)
    value = models.FloatField(null=True, blank=True)
```

Update the existing **menu catalog sync** paths (not forecast paths) to carry `unit` and `value` into central when present — this is how the central catalog becomes authoritative:

- menu bootstrap ingest
- menu mutation `catalog_delta`
- menu catalog snapshot response

Requires a small addition to `contracts/central_server_analytics_app_api_contract.md` §20 catalog shapes so `unit`/`value` ride the existing menu wire, not a new endpoint.

Fallback (runs centrally over the central catalog, no desktop involvement):

- Port `utils/variant_metadata.py` into `forecasting.services.variant_metadata`.
- Infer metadata from central variant names when explicit `unit`/`value` are absent.
- Store inferred metadata with `metadata_source = "explicit" | "inferred" | "unknown"` if adding another field is acceptable.
- Exclude unknown/mixed unit items from volume forecasting and log them in run metrics.

## PostgreSQL Extraction Rules

### Business Date

Use an explicit PostgreSQL expression. Exact implementation depends on whether `created_on` is stored as local IST text or a timezone-aware value.

For Petpooja local timestamp text:

```sql
((created_on_text::timestamp - interval '5 hours')::date)
```

For timezone-aware UTC timestamps:

```sql
(((created_at AT TIME ZONE 'Asia/Kolkata') - interval '5 hours')::date)
```

Do not use SQLite's `DATE(created_on, '-5 hours')` on the server.

### Make extraction index-bounded, not table-scan

Extracting `status` and business date from `raw_event` JSON on every row makes each nightly run a full sequential scan that gets slower as history grows. Add stored generated columns to `analytics.order_fact` and index them so extraction is bounded by the training window:

```sql
ALTER TABLE analytics.order_fact
  ADD COLUMN IF NOT EXISTS order_status TEXT
    GENERATED ALWAYS AS (raw_event->'raw_payload'->'properties'->'Order'->>'status') STORED,
  ADD COLUMN IF NOT EXISTS business_date DATE
    GENERATED ALWAYS AS (
      (( (raw_event->'raw_payload'->'properties'->'Order'->>'created_on')::timestamp
         - interval '5 hours')::date)
    ) STORED,
  ADD COLUMN IF NOT EXISTS order_total NUMERIC
    GENERATED ALWAYS AS ((raw_event->'raw_payload'->'properties'->'Order'->>'total')::numeric) STORED;

CREATE INDEX IF NOT EXISTS idx_order_fact_success_bdate
  ON analytics.order_fact (business_date)
  WHERE order_status = 'Success';
```

Generated columns are maintained by the projector's insert with no extra code and keep the JSON path definitions in one place. If the deployment cannot cast `created_on` cleanly (dirty/empty timestamps), use a functional index on the same expression instead and coalesce nulls in the extractor. Apply the equivalent typed columns to `order_item_fact` via the projector (Finding 13) so item/volume extraction is also index-bounded.

### Revenue Daily Sales

Source can be `analytics.order_fact.raw_event`.

Fields:

- order id: `raw_event->'raw_payload'->'properties'->'Order'->>'orderID'`
- status: `...->>'status'`
- total: `...->>'total'`
- created_on: `...->>'created_on'`

Filter:

- status exactly `Success`
- business date between `training_start` and `training_end`

Output columns:

- `business_date as ds`
- `SUM(total) as y`
- `COUNT(*) as orders`
- weather fields left joined by business date

### Item Daily Sales

Use enriched/staged order-line facts after the `order_line_key` prerequisite.

Join:

```text
analytics.order_item_fact.order_line_key
  -> OrderItemAssignmentLocator.order_line_key
  -> MenuCatalogItem.menu_item_id
```

Filter:

- `order_status = 'Success'`
- `menu_item_id is not null`
- optionally `is_verified = true` for high-trust training, or include unverified with run metrics

Aggregate:

- `SUM(quantity) as quantity_sold`
- `AVG(unit_price) as price`
- `MIN(item_name) or catalog item name as item_name`
- `category = MenuCatalogItem.item_type`

### Volume Daily Sales

Use the same order-line assignment join plus variant metadata:

```text
OrderItemAssignmentLocator.variant_id
  -> MenuCatalogVariant.variant_id
```

Aggregate by `business_date, menu_item_id`.

Normalize:

- `COUNT` -> quantity * value, unit `units`
- `ML`, `GMS`, `G` -> quantity * value, unit `g`
- `KG` -> quantity * value * 1000, unit `g`
- `MG` -> quantity * value / 1000, unit `g`

Exclude:

- item ids with both count and mass/volume variants in the training window
- rows with missing variant metadata
- rows with missing assignment locators

Record exclusion counts in `ForecastRun.metrics`.

## Nightly Job

### Schedule

Current server timezone is UTC. The business day is IST and ends at 05:00 IST.

Recommended initial schedule:

- Existing Petpooja nightly sync remains at 02:00 UTC unless changed.
- Forecast job runs at 02:45 UTC, which is 08:15 IST, after the business day is complete and after the current Petpooja t-1 sync has time to finish.

Add to `backend/config/celery.py`:

```python
app.conf.beat_schedule["forecasting-nightly"] = {
    "task": "forecasting.tasks.run_nightly_forecast",
    "schedule": crontab(hour=2, minute=45),
    "options": {"queue": "forecasting"},
}
```

Use a dedicated Celery queue/worker if possible:

```bash
celery -A config worker -Q forecasting --concurrency=1 --time-limit=7200 --soft-time-limit=6600
```

### Job Steps

1. Acquire a database advisory lock, e.g. `pg_try_advisory_lock(hashtext('forecasting-nightly'))`. Caveat: session-level advisory locks live on one connection. Under Django's connection pooling / `CONN_MAX_AGE`, the lock connection can be returned to the pool mid-run and the lock silently leaked or held. Take the lock on a **dedicated** connection (`connections.create_connection` or a `with connection.cursor()` held for the whole run) and release it explicitly in a `finally`; if that is awkward, use the `ForecastRun(status='running')` row itself as the guard (skip if a non-stale running row exists) and treat the advisory lock as best-effort. If `pg_try_advisory_lock` returns false, exit early — another run holds it.
2. Compute `generated_on` using IST business date.
3. Compute `training_window_end = generated_on - 1 day`.
4. Compute `training_window_start = training_window_end - 120 days`.
5. Create `ForecastRun(status='running')`.
6. Ensure analytics projections are current by calling the new `project_pending --once` command (single `project_new_events` pass to the current watermark, then exit — see Finding 14). Do NOT launch the infinite `run_analytics_ingestor` loop and do NOT `rebuild_order_fact` on every run; full rebuild is recovery-only.
7. Refresh weather for `training_window_start` through `generated_on + 14 days`.
8. Extract daily revenue data.
9. Train revenue models and stage:
   - 7 forward days
   - 30 backtest days
10. If stable order-line assignment coverage is available, extract item data, train item model, and stage:
   - 14 forward days
   - 30 backtest days
11. If variant metadata coverage is available, extract volume data, train volume model, and stage:
   - 14 forward days
   - 30 backtest days
12. Write staged forecast points with `bulk_create(..., batch_size=1000)` per family rather than per-row saves. Item/volume runs can produce thousands of rows (active_items x (forward + history days) per family); row-at-a-time inserts dominate write time and hold the transaction open.
13. Validate completeness:
   - revenue: four models present for each of 7 forward dates
   - item: all active items have 14 forward dates
   - volume: all active non-excluded items have 14 forward dates
   - backtest rows present for requested windows, or warnings recorded with explicit counts
14. In a single DB transaction: mark previous successful runs for that `scope_key/generated_on` as superseded, then mark this run `status='success'` with `completed_at=now`. The partial unique index guarantees no window with two active success rows.
15. Release lock in a `finally` so it is freed on both success and exception.

### Backdated Runs And Missed Nights

Do not build automatic missing-date detection into the nightly job. The scheduled task handles exactly one generated date: the just-completed business day. It should not scan `ForecastRun` looking for holes, queue historical catch-up tasks, or decide which dates need reruns.

Backdated generation is an operator action:

- For a missed nightly run, run `python manage.py run_nightly_forecast --generated-on YYYY-MM-DD --families revenue,item,volume --force`.
- For a historical what-if/backdated forecast, run the same command for the desired `generated_on`.
- The command publishes a normal `ForecastRun` for that date. Desktop clients learn about it through the regular `forecasts/delta` cursor.

Backtest/history rows are part of the run payload, not a separate self-healing cache. If runtime becomes too high, reduce the served history window or split by forecast family; do not reintroduce automatic missing-date recovery.

Failure behavior:

- Mark run `failed` with `error_message`.
- Do not delete previous successful rows.
- APIs continue serving the latest successful run.

### Day-One / Fresh DB Behavior

The forecasting schema starts empty (clean slate, no backfill). The first nightly run — or the first operator `run_nightly_forecast` — bootstraps everything it needs from live sources; there is no seed migration. Expected behavior on a fresh database:

1. **Weather: first run fetches the full training window fresh.**

   `ForecastWeatherDaily` is empty on day one. The revenue models need weather for the whole `training_window_start .. training_window_end` range (~120 days) plus the `generated_on + 14 day` forward horizon. On the first run the weather service must fetch this entire back-range from the provider (Open-Meteo at `FORECAST_WEATHER_LAT` / `FORECAST_WEATHER_LON`), not just the previous day.

   - This is a one-time fetch, not a forecast backfill. Subsequent nightly runs only fetch the new day(s) plus refresh the forward horizon; historical rows already cached in `ForecastWeatherDaily` are reused.
   - Historical (past-dated) weather uses the provider's archive/observed endpoint; forward dates use the forecast endpoint. The first run may therefore hit two provider endpoints.
   - Weather provider failure must not fail the forecast run. Fall back to recent observed averages for missing dates and record `weather_fallback_days` in `ForecastRun.metrics`. On a fresh DB with no observed history yet, a total weather outage means revenue models run without weather regressors (Prophet/GP degrade, not crash) — record this in metrics.

2. **Item/volume produce nothing until order-line coverage accumulates.**

   Revenue forecasting works immediately from `analytics.order_fact` — it needs no assignment identity. Item and volume forecasting need `order_line_key` locators (Phase 2) and, for volume, variant unit/value metadata (Phase 4). On a fresh central DB these are sparse until nightly order sync has populated enough lines.

   - Below `FORECAST_MIN_ASSIGNMENT_COVERAGE_PCT` (default 95), the nightly job skips the item/volume families and records the measured coverage in `ForecastRun.metrics`. The run still succeeds as a revenue-only run.
   - `GET forecasts/items` and `GET forecasts/volume` return the `awaiting_action` response (`missing_prerequisite: "order_line_key"`) until a run publishes those families. This is the correct day-one state, not an error.
   - Once enough nightly accumulation crosses the threshold, the next run publishes item/volume families automatically. No manual forecast backfill is needed — the models regenerate from whatever order history is present.
   - Day-one summary: **revenue = live immediately; item/volume = `awaiting_action` until coverage ≥ threshold.**

### Retention

Points tables grow every night. Item/volume can add thousands of rows nightly. Add a retention step (end of nightly job or a separate weekly task):

- Keep the latest N successful runs per `scope_key` (e.g. 14) plus any run referenced by a currently-served response; delete points and runs older than that.
- Deleting a `ForecastRun` cascades to its points via the FK `on_delete=CASCADE`.
- Never delete the single latest `success` run per scope even if it is old (protects charts if the nightly job stops).
- Prune orphaned model artifacts under `FORECAST_MODEL_DIR` for deleted runs.

Manual command:

```bash
python manage.py run_nightly_forecast --scope-key global --generated-on 2026-07-08
```

Useful options:

```text
--families revenue,item,volume
--skip-backtest
--backtest-days 30
--force
--dry-run-extract
```

## Model Artifacts

Serving APIs only require database rows, but training benefits from artifacts for warm starts and inspection.

Recommended storage:

- `FORECAST_MODEL_DIR=/app/data/forecasting/models` backed by a Docker volume, or object storage if the deployment already has durable object storage conventions.
- Store artifact paths and model metadata in `ForecastRun.artifact_paths`.

Do not rely on artifact mtime for freshness. Freshness comes from `ForecastRun`.

Suggested artifact paths:

```text
forecasting/models/{scope_key}/revenue/gp/{generated_on}/gp_model.pkl
forecasting/models/{scope_key}/item_demand/{generated_on}/...
forecasting/models/{scope_key}/volume_demand/{generated_on}/...
```

## API Design

Use the existing forecast path namespace for desktop auth/routing:

- `GET /desktop-analytics-sync/forecasts/central-bootstrap`

This endpoint returns server-authored rows only. Do not accept desktop-uploaded forecast rows as fallback data.

Add row-shaped sync endpoints so Sync DB can cache central forecast rows locally. Chart-shaped read endpoints are optional server conveniences; the desktop production path should be:

1. Sync DB pulls forecast deltas from central.
2. Desktop stores rows in a local forecast cache table.
3. Existing local FastAPI forecast routes read the cache and render the current React pages.
4. No local training/retraining/backtesting/model artifact path remains in production code.

Authentication:

- Use the existing sync read bearer-token flow.
- Resolve `scope_key` with existing `get_sync_scope_key(request)` for now; default to global.

### Get Forecast Status

`GET /desktop-analytics-sync/forecasts/status`

Response:

```json
{
  "latest_run": {
    "run_id": "uuid",
    "scope_key": "default",
    "generated_on": "2026-07-08",
    "training_window_start": "2026-03-10",
    "training_window_end": "2026-07-07",
    "status": "success",
    "completed_at": "2026-07-08T02:58:10Z",
    "families": {
      "revenue": {"status": "success", "rows": 28},
      "items": {"status": "success", "rows": 840},
      "volume": {"status": "success", "rows": 630}
    },
    "metrics": {
      "active_items": 60,
      "volume_excluded_mixed_unit_items": 3,
      "assignment_coverage_pct": 99.2
    }
  }
}
```

### Forecast Sync Delta

`GET /desktop-analytics-sync/forecasts/delta?cursor=<optional>&limit=1000&families=revenue,items,volume,weather`

This is the main desktop Sync DB endpoint after cutover. The server returns append-only published run metadata and forecast rows newer than the cursor. The desktop stores these rows locally and advances the cursor only after the SQLite transaction commits.

Response:

```json
{
  "schema_version": 1,
  "scope_key": "default",
  "next_cursor": "123456",
  "has_more": false,
  "runs": [
    {
      "server_seq": 1200,
      "run_id": "uuid",
      "generated_on": "2026-07-08",
      "status": "success",
      "completed_at": "2026-07-08T02:58:10Z",
      "families": ["revenue", "items", "volume"]
    }
  ],
  "rows": [
    {
      "server_seq": 1201,
      "run_id": "uuid",
      "family": "revenue",
      "kind": "forward",
      "forecast_date": "2026-07-08",
      "model_name": "gp",
      "entity_id": null,
      "payload": {
        "revenue": 40500.0,
        "orders": 0,
        "gp_lower": 29000.0,
        "gp_upper": 52000.0
      }
    }
  ],
  "weather_rows": [
    {
      "server_seq": 1210,
      "weather_date": "2026-07-08",
      "payload": {
        "temp_max": 35.0,
        "rain_sum": 0.0,
        "rain_category": "none",
        "source": "forecast"
      }
    }
  ]
}
```

The cursor is monotonic per scope and covers runs, forecast rows, and weather rows. Rows are immutable; if a date is regenerated with `--force`, publish a new `run_id` and mark the prior run superseded rather than mutating old rows in place.

### Forecast Sync Central Bootstrap

`GET /desktop-analytics-sync/forecasts/central-bootstrap?families=revenue,items,volume,weather`

Use this only when the desktop forecast cache is empty, reset, or its cursor is invalid. It returns the current retained successful runs and rows in the same `runs` / `rows` / `weather_rows` shape as `delta`, plus `next_cursor`.

### Get Revenue Forecast

`GET /desktop-analytics-sync/forecasts/revenue?days=7&history_days=30&generated_on=latest`

Response should match the desktop `/api/forecast` shape:

```json
{
  "summary": {
    "generated_at": "2026-07-08",
    "projected_7d_revenue": 325000.0,
    "projected_7d_orders": 920
  },
  "historical": [
    {
      "sale_date": "2026-07-07",
      "revenue": 42100.0,
      "orders": 132,
      "temp_max": 34.1,
      "rain_category": "drizzle"
    }
  ],
  "forecasts": {
    "weekday_avg": [
      {"date": "2026-07-08", "revenue": 40250.0, "orders": 121}
    ],
    "holt_winters": [
      {"date": "2026-07-08", "revenue": 39880.0, "orders": 0}
    ],
    "prophet": [
      {"date": "2026-07-08", "revenue": 41010.0, "orders": 0, "temp_max": 35.0, "rain_category": "none"}
    ],
    "gp": [
      {"date": "2026-07-08", "revenue": 40500.0, "orders": 0, "gp_lower": 29000.0, "gp_upper": 52000.0}
    ]
  },
  "debug_info": {
    "served_from_central": true,
    "run_id": "uuid",
    "using_fallback": false
  }
}
```

### Get Item Demand Forecast

`GET /desktop-analytics-sync/forecasts/items?days=14&history_days=30&item_id=<optional>`

Response should match desktop `/api/forecast/items`:

```json
{
  "items": [
    {"item_id": "uuid", "item_name": "Chocolate Ice Cream"}
  ],
  "history": [
    {"date": "2026-07-07", "item_id": "uuid", "qty": 12}
  ],
  "forecast": [
    {
      "date": "2026-07-08",
      "item_id": "uuid",
      "item_name": "Chocolate Ice Cream",
      "p50": 5.2,
      "p90": 9.8,
      "probability": 0.74,
      "recommended_prep": 9
    }
  ],
  "backtest": [
    {
      "date": "2026-07-07",
      "item_id": "uuid",
      "item_name": "Chocolate Ice Cream",
      "p50": 4.8,
      "p90": 8.7,
      "probability": 0.71
    }
  ],
  "model_stale": false,
  "training_in_progress": false,
  "debug_info": {
    "served_from_central": true,
    "run_id": "uuid"
  }
}
```

If order-line assignment coverage is missing:

```json
{
  "items": [],
  "history": [],
  "forecast": [],
  "backtest": [],
  "awaiting_action": true,
  "message": "Central item forecasts are waiting for order-line assignment locators.",
  "debug_info": {
    "missing_prerequisite": "order_line_key"
  }
}
```

### Get Volume Forecast

`GET /desktop-analytics-sync/forecasts/volume?days=14&history_days=30&item_id=<optional>`

Response should match desktop `/api/forecast/volume`:

```json
{
  "items": [
    {"item_id": "uuid", "item_name": "Chocolate Ice Cream", "unit": "g"}
  ],
  "history": [
    {"date": "2026-07-07", "item_id": "uuid", "volume": 4200.0}
  ],
  "forecast": [
    {
      "date": "2026-07-08",
      "item_id": "uuid",
      "item_name": "Chocolate Ice Cream",
      "unit": "g",
      "p50": 3900.0,
      "p90": 6200.0,
      "probability": 0.77,
      "volume_value": 3900.0,
      "recommended_volume": 5600.0
    }
  ],
  "backtest": [
    {
      "date": "2026-07-07",
      "item_id": "uuid",
      "item_name": "Chocolate Ice Cream",
      "unit": "g",
      "p50": 4100.0,
      "p90": 6500.0,
      "probability": 0.80
    }
  ],
  "model_stale": false,
  "training_in_progress": false,
  "debug_info": {
    "served_from_central": true,
    "run_id": "uuid"
  }
}
```

## Desktop App Migration Plan

**Complete (2026-07-09).** As-built cutover path:

1. React pages unchanged.
2. Local FastAPI routes `/api/forecast`, `/api/forecast/items`, `/api/forecast/volume` retained.
3. Local cache tables `central_forecast_runs`, `central_forecast_rows`, `central_forecast_weather`, and `forecast_sync_cursor` in `system_config`.
4. During Sync DB, `forecast_sync` calls `forecasts/delta` after order/menu sync; bootstrap when cursor is empty.
5. Each page applied in one SQLite transaction; `next_cursor` persisted only after commit.
6. Forecast routes read only from `central_forecast_*` via `central_forecast_projection.py`; stale cache or `awaiting_action` when no run exists.
7. Local training, model artifacts, training endpoints, "Full Retrain" UI, forecast upload, and ML deps removed.
8. Dev-only `scripts/pull_forecasts.py` — pull only, no train/upload.

## Implementation Phases

### Phase 0: Preconditions and Contracts

- Add this plan to the repo.
- Central forecast scope: one `scope_key` per restaurant, derived server-side from the selected restaurant. No default, no client-supplied value.
- Update the cloud sync contract with downstream-only forecast bootstrap/delta endpoints.
- Stable order-line key: **finalized** as deterministic SHA-256(petpooja_order_id + line_index + petpooja_itemid + name + qty + total); migrate to a true Petpooja per-line id only if one appears later.
- Variant metadata: forecast reads `unit`/`value` from the central `MenuCatalogVariant` (source of truth). No forecast-specific variant sync; add `unit`/`value` to the central catalog via the existing menu catalog sync path (one-time menu-catalog change).
- Central forecast APIs live under `desktop-analytics-sync/forecasts/*` (existing desktop sync auth/routing; no new prefix).

### Phase 1: Server Revenue Forecasts

**Status: deployed (revenue family live in production as of 2026-07-09).**

This can ship before item/volume prerequisites.

- Add `forecasting` app.
- Add `ForecastRun` and `ForecastRevenuePoint`, including the partial unique success index.
- Add `project_pending --once` catch-up command (Finding 14).
- Add `order_fact` generated columns (`order_status`, `business_date`, `order_total`) and the partial `Success` index so revenue extraction is index-bounded.
- Port revenue model modules.
- Add business date helper for IST 5 AM boundary.
- Add weather table/service with fallback.
- Implement revenue extractor from `analytics.order_fact`.
- Implement `run_nightly_forecast --families revenue`.
- Add Celery task and beat schedule.
- Add `GET forecasts/status`, `GET forecasts/delta`, `GET forecasts/central-bootstrap`, and optionally `GET forecasts/revenue`.
- Add tests for:
  - business date boundary
  - extractor filters only `Success`
  - forecast run publish gate
  - response shape matches desktop expectation

### Phase 2: Stable Order-Line Assignment Coverage

**Status: deployed.** Locators are materialized centrally from current order-line facts.

- Extend analytics projection with `order_line_key` and order-line typed columns.
- Extend desktop assignment sync contract to include `order_line_key`.
- Add central assignment locator model/table.
- Add coverage report:
  - total successful order lines in 120-day window
  - lines with assignment locator
  - lines with verified menu item
  - lines with variant metadata
- Gate item/volume runs below a minimum coverage threshold, e.g. 95%, unless forced.

### Phase 3: Item Demand Forecasts

**Status: deployed (code).** Publish gated until assignment coverage reaches 95%.

- Add `ForecastItemPoint`.
- Port item-demand ML modules.
- Implement item extractor using assignment locators and catalog item names/types.
- Fix SQL aggregation with explicit `AVG(price)`.
- Implement item training and backtest tasks.
- Add `GET forecasts/items`.
- Add delta/bootstrap support for item rows.
- Add tests for:
  - densification creates zero-sale rows
  - active-item filter
  - quantile crossing correction
  - response shape
  - missing assignment coverage returns `awaiting_action`

### Phase 4: Volume Forecasts

**Status: deployed (code).** Publish gated until assignment coverage reaches 95%.

- Add `unit` and `value` to central variant catalog.
- Port variant metadata inference.
- Add `ForecastVolumePoint`.
- Port volume-demand ML modules.
- Implement volume extractor.
- Exclude unknown/mixed-unit items and record metrics.
- Add `GET forecasts/volume`.
- Add delta/bootstrap support for volume rows.
- Add tests for:
  - unit conversion
  - mixed-unit exclusion
  - missing metadata exclusion
  - response shape

### Phase 5: Desktop Pull-Only Cleanup

**Status: complete (2026-07-09).** Desktop is pull-only: `forecast_sync` + `central_forecast_*` cache, projection for all three families, training/upload/ML removed. Revenue charts populate from central; item/volume show `awaiting_action` until central publishes those families.

**Shipped:** `central_forecast_*` schema, `forecast_sync.py`, orchestrator hook, `central_forecast_projection.py` (revenue/items/volume), GET `/api/forecast` + `/items` + `/volume`, `scripts/pull_forecasts.py`, UI cleanup, ML tree removal, unit tests.

Phase 5 was **integration/refactor work**, not ML. The central server trains and publishes row-shaped deltas; the desktop pulls, caches, projects rows into existing chart JSON, and no longer trains or uploads locally.

**Prerequisite (met):** Central `GET /desktop-analytics-sync/forecasts/delta` and `.../central-bootstrap` return the `runs` / `rows` / `weather_rows` shape. Desktop uses `forecast_sync.py` only.

#### Phase 5 deliverables (summary) — Complete (2026-07-09)

1. SQLite `central_forecast_*` cache tables + `forecast_sync_cursor`.
2. `forecast_sync.py` (mirror `menu_merge_sync.py` cursor + transactional apply).
3. Forecast pull wired into Sync DB / `cloud_pull_orchestrator` (after menu/customer pulls).
4. Projection layer: central rows → chart responses for `/api/forecast`, `/api/forecast/items`, `/api/forecast/volume`.
5. Training endpoints, auto-retrain scheduler, forecast upload shipper, production UI controls, ML deps/artifacts removed.
6. Phase 5 tests in `tests/test_forecast_sync.py` (see §5.7).

#### 5.1 SQLite schema (desktop cache)

Add to `database/schema_sqlite.sql` and an idempotent `ensure_central_forecast_tables(conn)` helper (same pattern as `forecast_cache.ensure_tables_exist`).

```sql
-- Central forecast run metadata (immutable once pulled)
CREATE TABLE IF NOT EXISTS central_forecast_runs (
    run_id TEXT PRIMARY KEY,
    server_seq INTEGER NOT NULL,
    scope_key TEXT NOT NULL DEFAULT 'default',
    generated_on DATE NOT NULL,
    status TEXT NOT NULL,              -- success | superseded (only success rows are cached)
    completed_at TEXT,
    families TEXT NOT NULL,            -- JSON array: ["revenue","items","volume"]
    training_window_start DATE,
    training_window_end DATE,
    metrics TEXT,                      -- JSON object from server
    pulled_at TEXT DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(server_seq)
);
CREATE INDEX IF NOT EXISTS idx_central_forecast_runs_generated
    ON central_forecast_runs(scope_key, generated_on DESC);

-- Append-only forecast points from central delta/bootstrap
CREATE TABLE IF NOT EXISTS central_forecast_rows (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    server_seq INTEGER NOT NULL UNIQUE,  -- monotonic cursor unit
    run_id TEXT NOT NULL,
    scope_key TEXT NOT NULL DEFAULT 'default',
    family TEXT NOT NULL,              -- revenue | items | volume
    kind TEXT NOT NULL,                -- forward | backtest
    forecast_date DATE NOT NULL,
    model_name TEXT,                   -- revenue only: weekday_avg | holt_winters | prophet | gp
    entity_id TEXT,                    -- item_id for items/volume; NULL for revenue
    entity_name TEXT,
    unit TEXT,                         -- volume only: g | units
    payload TEXT NOT NULL,             -- JSON: family-specific fields (see §5.3)
    pulled_at TEXT DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (run_id) REFERENCES central_forecast_runs(run_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_central_forecast_rows_lookup
    ON central_forecast_rows(scope_key, family, kind, forecast_date);
CREATE INDEX IF NOT EXISTS idx_central_forecast_rows_run
    ON central_forecast_rows(run_id, family, kind);
CREATE INDEX IF NOT EXISTS idx_central_forecast_rows_entity
    ON central_forecast_rows(scope_key, family, entity_id, forecast_date);

-- Weather rows pulled with forecast deltas
CREATE TABLE IF NOT EXISTS central_forecast_weather (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    server_seq INTEGER NOT NULL UNIQUE,
    weather_date DATE NOT NULL,
    payload TEXT NOT NULL,             -- JSON: temp_max, rain_sum, rain_category, source
    pulled_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_central_forecast_weather_date
    ON central_forecast_weather(weather_date);

-- Cursor stored in existing system_config table:
--   key = 'forecast_sync_cursor', value = opaque server cursor string
```

**Retention (desktop):** Keep all rows referenced by the latest successful run per family plus runs from the last 14 `generated_on` values (mirror central retention spirit). Prune older `central_forecast_rows` / `central_forecast_weather` by `server_seq` during pull apply. Never delete rows for the single newest `success` run.

**Legacy tables:** `forecast_cache`, `*_backtest_cache`, `item_forecast_cache`, `volume_forecast_cache` are **not** written after cutover. Drop them in a follow-up migration once parity is verified (see Open Questions).

#### 5.2 Forecast sync module

**New file:** `src/core/forecast_sync.py` (pattern: `src/core/menu_merge_sync.py`).

| Function | Responsibility |
|----------|----------------|
| `FORECAST_SYNC_CURSOR_KEY = "forecast_sync_cursor"` | `system_config` key |
| `get_forecast_delta_endpoint(conn)` | `{cloud_sync_url}/desktop-analytics-sync/forecasts/delta` |
| `get_forecast_bootstrap_endpoint(conn)` | `{cloud_sync_url}/desktop-analytics-sync/forecasts/central-bootstrap` |
| `get_forecast_sync_cursor(conn)` / `set_forecast_sync_cursor(conn, cursor)` | Cursor read/write |
| `ensure_central_forecast_tables(conn)` | DDL idempotent create |
| `pull_and_apply_forecast_deltas(conn, *, families, limit=1000)` | Main entry |

**Pull algorithm:**

1. Resolve auth via `get_cloud_sync_config(conn)` (same bearer as menu sync).
2. If cursor is empty → `GET .../forecasts/central-bootstrap?families=revenue,items,volume,weather`.
3. Else → `GET .../forecasts/delta?cursor={cursor}&limit=1000&families=...`.
4. Loop while `has_more`.
5. For each page, in **one SQLite transaction**:
   - Upsert `runs` into `central_forecast_runs` (`INSERT OR REPLACE` on `run_id`; skip `status != 'success'` unless superseding a prior run).
   - Bulk insert `rows` into `central_forecast_rows` (`INSERT OR IGNORE` on `server_seq` for idempotency).
   - Bulk insert `weather_rows` into `central_forecast_weather`.
   - Prune per retention rules.
6. **Only after commit succeeds**, persist `next_cursor` via `set_forecast_sync_cursor`.
7. Return summary: `{attempted, runs_inserted, rows_inserted, weather_inserted, pages, error}`.

**Orchestrator hook:** In `src/core/services/cloud_pull_orchestrator.py`, after customer merges (step 5 in file header), add step 6:

```text
6. Forecast deltas (after order/menu/customer ground truth)
```

Add `forecasts` to the summary dict. Follow menu pull error surfacing: forecast pull failure must be **best-effort** — log + non-fatal warning in sync summary (same pattern as customer quarantine warnings). **Must not fail POS order import or Sync DB overall.** **Resolved (OQ-3, 2026-07-09):** confirmed with ops.

**Scheduler changes** (`src/core/services/cloud_sync_scheduler.py`):

- **Remove** `check_and_trigger_auto_forecast()` and all calls to `_full_retrain_task`.
- **Remove** forecast upload from the 5-minute `run_client_learning_shippers` cycle (see §5.6).
- Forecast freshness becomes: "did the last Sync DB / background pull advance the cursor and cache a recent `generated_on`?"

#### 5.3 Projection layer (rows → chart JSON)

**New file:** `src/core/central_forecast_projection.py`

Read-only helpers used by the three forecast routers. No ML imports.

##### Run selection

```python
def get_served_run(conn, scope_key="default", family: str) -> Optional[ServedRun]:
    """
    1. Prefer latest success run where generated_on == current business date
       AND family is present in run.families AND completeness check passes.
    2. Else fall back to previous complete run (mirror current revenue fallback).
    3. Return None → router emits awaiting_action / empty charts.
    """
```

**Completeness checks (match central publish gate):**

| Family | Required |
|--------|----------|
| `revenue` | 4 models × 7 `kind=forward` dates for served `generated_on` |
| `items` | All active items (from central rows' entity set) × 14 forward dates |
| `volume` | All non-excluded items × 14 forward dates |

Use `ForecastRun.metrics` JSON cached in `central_forecast_runs.metrics` when local row counts are ambiguous.

##### Payload field mapping (central → projection)

Central delta `payload` keys (already published by server):

| Family | `payload` keys |
|--------|----------------|
| `revenue` | `revenue`, `orders`, `pred_std`, `lower_95`, `upper_95`, `temp_max`, `rain_category`; GP uses `gp_lower`/`gp_upper` aliases → map to `gp_lower`/`gp_upper` in chart JSON |
| `items` | `p50`, `p90`, `probability`, `recommended_prep`, `item_name` |
| `volume` | `p50`, `p90`, `probability`, `volume_value`, `recommended_volume`, `unit`, `item_name` |

##### Revenue projection → `GET /api/forecast/`

Build response matching `ForecastPage.tsx` `ForecastResponse`:

- `summary.generated_at` → **current business date** (not run date — preserves existing UI).
- `summary.projected_7d_revenue` / `projected_7d_orders` → sum `kind=forward` weekday_avg rows with `forecast_date >= today` (same as today).
- `forecasts.{model}` → group `central_forecast_rows` by `model_name`; map `forecast_date` → `date`, `payload.revenue` → `revenue`, etc.
- **Backtest overlay:** reuse existing `_merge_backtest_and_forecast()` logic but feed it from `kind=backtest` rows instead of `revenue_backtest_cache`.
- `historical` → **still from local orders** via existing `get_historical_data()` (blue actuals line). Central does not ship order actuals in delta. Optionally enrich `temp_max`/`rain_category` from `central_forecast_weather` when local `weather_daily` is stale.
- `debug_info`: `{served_from_central: true, run_id, using_fallback, original_generated_on?}` — replace `served_from_cache`.

##### Item projection → `GET /api/forecast/items`

- `items` → distinct `entity_id` / `entity_name` from forward rows for served run.
- `forecast` → forward rows, filter `forecast_date >= today`, limit to `days` param distinct dates.
- `backtest` → backtest rows, last 30 days, include `item_name`.
- `history` → **still from local `order_items`** (actuals). Keep existing SQL aggregation.
- Remove `model_stale`, `training_in_progress` from production responses (always `false` / omit). React does not consume them today.
- `awaiting_action` when no served run or central status endpoint reports `missing_prerequisite: order_line_key`.

##### Volume projection → `GET /api/forecast/volume`

Same as items, plus:

- `unit` on each forecast point from row `unit` column (central contract: `g` | `units`).
- **UI labels:** Project and display `g` and `units` only; central is authoritative.

##### Weather

Prefer `central_forecast_weather` for forward-looking temp/rain on chart overlays when building Prophet/GP series. Revenue `historical` weather can fall back to local `weather_daily` then central weather table.

#### 5.4 Router cutover sequence — Complete (2026-07-09)

| Step | Action | Status |
|------|--------|--------|
| **5.4.1** | Schema + `forecast_sync.py`; wired into `cloud_pull_orchestrator`. | Done |
| **5.4.2** | `central_forecast_projection.py` + unit tests in `tests/test_forecast_sync.py`. | Done |
| **5.4.3** | `GET /api/forecast`, `/items`, `/volume` projection-only; cache-miss compute and 503 training gate removed. | Done |
| **5.4.4** | Shadow-compare (optional dev flag). | Skipped |
| **5.4.5** | Training endpoints and tasks deleted (see §5.5). | Done |
| **5.4.6** | Upload shipper + scheduler auto-retrain removed. | Done |
| **5.4.7** | UI cleanup (§5.6). | Done |
| **5.4.8** | ML packages + `src/core/learning/revenue_forecasting/` tree removed. Model files under `data/` still on disk — see §5.5 "Delete (post-parity)"; not yet deleted. | Partial |

**Endpoints to remove:**

| Endpoint | File |
|----------|------|
| `POST /api/forecast/train-gp` | `forecast.py` |
| `POST /api/forecast/full-retrain` | `forecast.py` |
| `POST /api/forecast/pull-from-cloud` | `forecast.py` (replace with dev-only `forecast_sync` CLI if needed) |
| `GET /api/forecast/training-status` | `forecast.py` |
| `GET /api/forecast/replay` | `forecast.py` (unused in UI; remove or dev-only) |
| `POST /api/forecast/items/train-items` | `forecast_items.py` |
| `POST /api/forecast/volume/train-volume` | `forecast_volume.py` |

**Keep:** `GET /api/forecast`, `GET /api/forecast/items`, `GET /api/forecast/volume` — same paths, cache-only implementation.

#### 5.5 File inventory — Complete (2026-07-09)

| Action | Path |
|--------|------|
| **Create** | `src/core/forecast_sync.py` |
| **Create** | `src/core/central_forecast_projection.py` |
| **Create** | `tests/test_forecast_sync.py` (sync + projection unit tests) |
| **Modify** | `database/schema_sqlite.sql` — §5.1 tables |
| **Modify** | `src/core/services/cloud_pull_orchestrator.py` — forecast pull step |
| **Modify** | `src/core/services/cloud_sync_scheduler.py` — remove auto-retrain |
| **Modify** | `src/api/routers/forecast.py` — projection-only GET |
| **Modify** | `src/api/routers/forecast_items.py` — projection-only GET |
| **Modify** | `src/api/routers/forecast_volume.py` — projection-only GET |
| **Modify** | `src/api/main.py` — remove training shutdown / GP import guards |
| **Modify** | `src/core/client_learning_shipper.py` — remove `upload_forecasts` |
| **Modify** | `src/core/config/client_learning_config.py` — remove ingest URL helpers |
| **Modify** | `src/api/routers/operations.py` — forecast pull in sync summary |
| **Modify** | `ui_electron/src/pages/ForecastPage.tsx` — remove retrain/overlay/pull buttons |
| **Modify** | `ui_electron/src/pages/ItemDemandForecast.tsx` — same |
| **Modify** | `ui_electron/src/pages/ItemVolumeForecast.tsx` — same |
| **Modify** | `ui_electron/src/api.ts` — remove `pullFromCloud`, `fullRetrain`, `trainingStatus` |
| **Modify** | `ui_electron/src/pages/Configuration.tsx` — forecast reset clears central cache only |
| **Delete/replace** | `src/core/forecast_bootstrap.py` — replace with `forecast_sync` bootstrap path |
| **Delete** | `src/core/forecast_shipper.py` |
| **Delete** | `src/api/routers/forecast_training_status.py` |
| **Delete** | `ui_electron/src/components/TrainingOverlay.tsx` (if no other consumers) |
| **Delete (done 2026-07-09)** | `src/core/learning/revenue_forecasting/**` (entire tree) |
| **Delete (post-parity)** | `data/gp_model.pkl`, `data/models/item_demand_ml/`, `data/models/volume_demand_ml/`, `data/models/gp_backtest/` — still present, parity not yet verified |
| **Modify** | `requirements.txt` — remove `prophet`, `statsmodels`, `lightgbm`, `xgboost`, etc. |

#### 5.6 UI changes

| Component | Change |
|-----------|--------|
| `ForecastPage.tsx` | Remove `TrainingOverlay`, training-status poll, **Pull from Cloud**, **Full Retrain**. Empty state: "Run Sync DB to fetch forecasts from central server" (no local retrain CTA). |
| `ItemDemandForecast.tsx` / `ItemVolumeForecast.tsx` | Same. |
| `Configuration.tsx` | `resetDb("sales_forecast" \| "item_demand" \| "volume_forecast")` → truncate `central_forecast_*` + reset cursor; do not delete nonexistent local models. |
| `api.ts` | Remove client methods for deleted endpoints. |
| 503 handling | Remove — no training-in-progress state. |

#### 5.7 Phase 5 tests — Complete (2026-07-09)

**Unit (`tests/test_forecast_sync.py`):**

- Delta page apply is idempotent (`INSERT OR IGNORE` on `server_seq`).
- Cursor not advanced if transaction rolls back.
- Bootstrap used when cursor is null.
- Pagination loops until `has_more` is false.
- Revenue: 4 models × 7 forward dates → valid `forecasts` dict.
- Revenue: backtest + forward merge matches `_merge_backtest_and_forecast` golden fixture.
- Items: `recommended_prep` integer rounding preserved.
- Volume: `unit` is `g` or `units` only (never `mg` — OQ-4 resolved).
- Fallback to previous `generated_on` when today's run incomplete.
- `awaiting_action` when no runs cached.

**Integration:**

- Seed `central_forecast_*` from JSON fixture for **offline CI unit tests** only.
- **Manual / dev integration:** pull from the deployed central server (sync-read token + configured cloud URL).
- `GET /api/forecast` response matches `ForecastPage.tsx` types (snapshot test).
- Sync DB run invokes forecast pull after menu pulls (live central or mocked in CI).

**Operational (manual / production central):**

- Fresh install: bootstrap populates cache; charts render without local ML.
- Incremental Sync DB: cursor advances; new nightly run appears after central deploy.
- Central run failure: desktop keeps serving previous run (`using_fallback: true` in `debug_info`).
- Airplane mode: last cached run still renders.

#### 5.8 Open questions

| # | Question | Decision | Status |
|---|----------|----------|--------|
| **OQ-1** | Should `historical` / `history` actuals come from local orders or central? | **Local orders** — central delta does not ship actuals; keeps blue line aligned with desktop DB. | Resolved |
| **OQ-2** | When to drop legacy six `*_forecast_cache` tables? | Drop in **follow-up migration** after one release with central cache live; keeps rollback path. | Open |
| **OQ-3** | Should forecast pull failure fail Sync DB? | **No** — best-effort with non-fatal warning in sync summary (like customer quarantine). | **Resolved 2026-07-09** |
| **OQ-4** | Volume `unit` in UI: central uses `g`/`units`; old UI sometimes shows `mg`. | Project and display **`g` / `units` only**; one-time UI label cleanup in `ItemVolumeForecast.tsx`. | **Resolved 2026-07-09** |
| **OQ-5** | Dev-only manual pull command? | `scripts/pull_forecasts.py` — calls `pull_and_apply_forecast_deltas`. | **Resolved 2026-07-09** |
| **OQ-6** | Call central `GET forecasts/status` on pull to detect `awaiting_action` / `missing_prerequisite`? | Yes — cache status in `system_config` for item/volume empty states with accurate message. | Open |
| **OQ-7** | How to develop Phase 5 before central deploy? | **Central is deployed (2026-07-09).** Use live prod central for dev integration; JSON fixtures for offline CI only. | **Resolved 2026-07-09** |
| **OQ-8** | Retain `GET /api/forecast/replay` for dev? | Removed from production. | **Resolved** |

## Testing Strategy

Server unit tests:

- Business date conversion at 04:59 and 05:00 IST.
- Revenue extractor filters cancellations and failed orders.
- Revenue extractor dedupes latest order revisions through `analytics.order_fact`.
- Item extractor requires assignment locators.
- Volume extractor requires variant unit/value.
- Publish gate only serves successful runs.
- Failed run does not hide prior successful run.

Server integration tests:

- Seed a small order dataset with two weeks of successful orders.
- Run revenue-only nightly command.
- Assert one successful `ForecastRun`.
- Assert all four revenue models publish 7 forward rows.
- Assert API response matches current desktop TypeScript interfaces.
- Seed order-line locators and catalog metadata.
- Run item/volume families and assert rows.

Performance tests:

- Measure extraction and training on 120 days of production-like data.
- Track run duration by family in `ForecastRun.metrics`.
- If backtests dominate runtime, split backtest fill into separate Celery subtasks and serve the latest available backtest window.

Operational tests:

- Kill a running task and verify run is marked failed or remains ignored by APIs.
- Re-run same `generated_on --force` and verify previous success is superseded, not mixed.
- Verify APIs keep serving prior success if tonight's run fails.
- Verify Sync DB applies forecast deltas transactionally and resumes from the prior cursor if local persistence fails.

Desktop Phase 5 tests (see [CENTRAL_FORECASTING_NIGHTLY_PLAN.md](./CENTRAL_FORECASTING_NIGHTLY_PLAN.md) §5.7):

- Forecast sync idempotency, cursor rollback safety, projection parity with React chart types.
- End-to-end: deployed central nightly run → Sync DB pull → charts render without local ML (revenue first; item/volume once coverage crosses the threshold).

## Deployment Notes

Dependencies to add or split into a **central** forecasting worker image. Pin exact versions to keep model behavior and pickle compatibility reproducible across deploys (mirror the versions the central nightly job trains with; unpinned `prophet`/`lightgbm`/`numpy` drift is a common source of "works on my machine" and unpicklable-artifact bugs). The desktop app no longer trains forecasts locally.

```text
pandas==<pin>
numpy==<pin>
statsmodels==<pin>
prophet==<pin>
scikit-learn==<pin>
joblib==<pin>
lightgbm==<pin>
xgboost==<pin>
```

Native runtime libs the wheels need at import time (not just build time `gcc`):

- `lightgbm` needs `libgomp1` (OpenMP) present in the runtime image.
- `prophet` pulls `cmdstanpy`/`holidays`; the first import can compile the Stan model — pre-warm it in the image build so the nightly worker does not pay it, and so a cold worker does not exceed the soft time limit on its first run.

Keep these off the web/API image; only the forecasting worker image needs them.

Recommended server env vars:

```text
FORECASTING_ENABLED=true
# Copy the complete PETPOOJA_RESTAURANT_SLUGS registry and every
# PETPOOJA_<SLUG>_<SUFFIX> peer block from .env.example. Do not configure a
# forecasting-only subset of a restaurant.
FORECAST_MODEL_DIR=/app/data/forecasting/models
FORECAST_MIN_ASSIGNMENT_COVERAGE_PCT=95
```

Scope and weather are derived per restaurant from its `PETPOOJA_<SLUG>_*` block. There is no deployment-wide scope or weather variable.

Operational commands (every one requires `--restaurant-id`):

```bash
python manage.py migrate
python manage.py run_nightly_forecast --restaurant-id <id> --families revenue --dry-run-extract
python manage.py run_nightly_forecast --restaurant-id <id> --families revenue
python manage.py run_nightly_forecast --restaurant-id <id> --families revenue,item,volume --force
python manage.py run_nightly_forecast --restaurant-id <id> --generated-on 2026-07-07 --families revenue,item,volume --force
```

## Clean-Slate Forecast Compatibility Policy

The central forecasting path is server-authored only:

1. Nightly or operator-triggered central jobs write `ForecastRun` and published forecast rows.
2. Desktop clients pull `forecasts/central-bootstrap` or `forecasts/delta`.
3. Desktop clients never upload forecast rows.
4. No automatic forecast backfill/gap-fill mechanism is required. For a missed date, run the one-time command for that `generated_on`.

## Key Risks

- Item/volume forecast correctness depends on solving stable order-line identity.
- Volume forecast correctness depends on central variant unit/value metadata.
- Prophet/LightGBM native dependencies can complicate Docker builds.
- Backtests are CPU-heavy; reduce the served history window or split by family if runtime exceeds Celery limits.
- Weather API failure should not fail the whole run; use recent observed averages as fallback and record the fallback in metrics.

## Definition Of Done

- A nightly central job creates a successful `ForecastRun` after each completed business day.
- Revenue, item, and volume forecast APIs return chart-shaped responses compatible with current React pages.
- APIs serve only latest successful complete runs.
- Desktop Sync DB pulls forecast deltas from the central server and local forecast routes read only from cached central rows.
- Desktop app has no production forecast model training, retraining, backtest, or forecast-upload path.
- Item/volume forecasts use verified menu assignments and variant metadata, not raw Petpooja names only.
- A failed nightly job does not break client charts; prior successful forecasts remain available.
