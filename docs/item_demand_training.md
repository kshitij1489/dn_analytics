# Item-Level Demand Forecasting

## Overview

This document explains the reasoning, modeling strategy, and implementation plan for forecasting item-level sales in a young ice cream cafe business (~7 months old). The objective is to produce reliable daily preparation quantities to minimize waste while avoiding stockouts.

The system complements existing revenue forecasting (Gaussian Process, Prophet, Holt-Winters, Weekday Average) and is specifically designed for intermittent retail demand.

---

## Why Item Forecasting is Different From Revenue Forecasting

Revenue behaves like a continuous signal with seasonal patterns. Individual menu items behave like sparse retail events:

Characteristics observed:

* High percentage of zero sales days (~51%)
* Bursty spikes (0 → 12 units suddenly)
* Weekend-heavy demand
* Strong weather influence
* New item cold-start problem

This means traditional time series assumptions break:

| Time Series Assumption        | Reality in Cafe        |
| ----------------------------- | ---------------------- |
| Demand oscillates around mean | Demand is mostly zero  |
| Autocorrelation driven        | Context driven         |
| Stable seasonality            | Behavioral seasonality |
| Continuous values             | Intermittent values    |

Therefore Prophet / ARIMA / Holt-Winters are unsuitable for item forecasting.

---

## Observations From Data Exploration

### 1. Right Skewed Distribution

Most sales are 0–2 units, rare spikes up to ~25.
Implication: Mean prediction is useless for inventory decisions.

### 2. High Zero Ratio (~51%)

Implies forecasting must first determine whether the item sells at all.

### 3. Strong Day-of-Week Behavior

Weekend demand significantly higher → behavioral demand.

### 4. Temperature Effect

Temperature affects probability of purchase, not quantity per buyer.

Conclusion:

> We must model probability of sale separately from quantity sold.

---

## Modeling Strategy

### Two-Stage Demand Model

**Stage 1 — Classification (Probability of Sale)**
Predicts $P(\text{quantity} > 0)$ for a given item on a given day.
*   **Model:** Calibrated LightGBM Classifier (Isotonic Regression)
*   **Why Calibration?** Raw tree output is uncalibrated. We calibrate on held-out data to ensure $P(\text{sale})=0.6$ truly means 60% chance of selling.

**Stage 2 — Regression (Quantity Estimation)**
Predicts quantity sold *given* that a sale occurs.
*   **Model:** LightGBM Regressor (Quantile Objective)
*   **Targets:** Median (P50) and Conservative Upper Bound (P90)
*   **Training Data:** Only positive sales rows (zeroes excluded)

**Final Expected Demand:**

$$ \text{Expected Demand} = P(\text{sale}) \times \text{Predicted Quantity (P50)} $$

This hurdle model approach is standard in grocery & QSR demand forecasting for intermittent items.

---

## Global Model Strategy

We train a **single global model** across all items (with item encoding) instead of individual models per item because:

*   **Data Sparsity:** Individual items often have <100 data points.
*   **Shared Patterns:** "Hot weekends boost fruit flavors" is a generalizable rule.
*   **Cold Start:** New items can leverage patterns learned from established items via category features.

To prevent data leakage during training:
1.  **Temporal Split:** Sort by date.
2.  **Train:** First 80% of dates (classifier wrapped with `CalibratedClassifierCV`, cv=3).
3.  **Evaluate:** Next 20% of dates (held-out, never seen during training).
4.  **Production Retrain:** Re-create and refit all models (including fresh calibration) on **100%** of data.

---

## Features Used

### Time Features
*   `day_of_week`, `is_weekend`, `month`
*   `days_since_launch` (lifecycle)
*   `temp_weekend` (interaction: temperature × weekend)

### Lag Features (Per Item)
History densification ensures `shift(1)` = yesterday (calendar day).
*   `lag_1`, `lag_7`
*   `rolling_mean_7`, `rolling_mean_14`
*   `rolling_trend_3` (momentum)

### Store Context & Price
*   `store_total_last3`, `store_total_last7`: Overall store traffic (proxy for footfall)
*   `item_median_price`: Item's typical price point
*   `price_ratio`: Current price / Median price (detects discounts)

### Cold Start Priors
Used when item history is < 3 days:
*   `category_avg_last7`: Category-level sales velocity
*   `global_avg_last7`: Store-wide sales velocity

### Weather Features
*   `temperature`, `rain`

---

## Algorithms

**Classification Model**
*   **Base:** `LightGBMClassifier` (or `XGBClassifier` fallback)
*   **Calibration:** `CalibratedClassifierCV` (method='isotonic', cv=3)

**Regression Model**
*   `LightGBMRegressor` (objective='quantile', alpha=0.5 / 0.9)

**Why Gradient Boosted Trees?**
*   Handles non-linear relationships (e.g., temp > 25°C spikes sales)
*   Robust to missing values and scale differences
*   Interpretable feature importance
*   Industry standard for tabular demand forecasting

---

## Forecast Output

For each item per day:

| Field                 | Meaning                                            |
| --------------------- | -------------------------------------------------- |
| `probability_of_sale` | Calibrated probability the item sells today        |
| `predicted_p50`       | Expected quantity (Median)                         |
| `predicted_p90`       | High-confidence quantity (Upper Bound, ≥ p50)      |
| `recommended_prep`    | Production quantity = `ceil(0.7 × p90 + 0.3 × p50)` |

---

## Inventory Decision Rule

**Kitchen Preparation Quantity:**

$$ \text{recommended\_prep} = \lceil\, 0.7 \times P_{90} + 0.3 \times P_{50} \,\rceil $$

*   **P50:** Base expectation (likely outcome).
*   **P90:** Conservative stock level (90% chance actual demand $\le$ stock).
*   The 70/30 blend favours the upper bound to minimise stockouts while limiting waste.
*   **Quantile crossing guard:** `p90` is always ≥ `p50` (enforced post-prediction).

---

## Implementation Structure

**Directory:** `src/core/learning/revenue_forecasting/item_demand_ml/`

**Key Files:**
*   `dataset.py`: Data loading and **densification** (filling zero-sales days).
*   `features.py`: Feature engineering (lags, priors, context).
*   `train.py`: Training pipeline (split $\to$ train $\to$ calibrate $\to$ retrain).
*   `predict.py`: Inference pipeline.
*   `tune.py`: Hyper-parameter tuning script.
*   `model_io.py`: Serialization utils.
*   `item_demand_training.ipynb`: Experimentation notebook.

**Model Storage:** `data/models/item_demand_ml/`
*   `item_demand_classifier.pkl`
*   `item_demand_regressor_p50.pkl`
*   `item_demand_regressor_p90.pkl`
*   `feature_columns.json`
*   `best_params.json` (created by `tune.py`)

---

## API Endpoints

### Forecast

`GET /api/forecast/items?item_id=<id>&days=14`

**Response:**
```json
{
  "items": [{"item_id": "abc", "item_name": "Brownie Cheesecake"}],
  "history": [{"date": "2026-02-01", "item_id": "abc", "qty": 5}],
  "forecast": [
    {
      "date": "2026-02-11", "item_id": "abc", "item_name": "Brownie Cheesecake",
      "p50": 4, "p90": 8, "probability": 0.72, "recommended_prep": 7
    }
  ],
  "model_stale": false
}
```

`model_stale` is `true` when the saved models were trained before today's business date. In that case a background retrain has already been triggered — the next request (after training completes) will use the refreshed model.

### Manual Retrain

`POST /api/forecast/train-items`

Triggers item demand model retraining in the background. Returns immediately:
```json
{"message": "Item demand training started in background"}
```

---

## Automatic Rolling Retraining

The model retrains itself automatically so that yesterday's sales are always
incorporated:

1. **Staleness check** — On each `GET /api/forecast/items` request, `model_io.is_model_stale()` compares the saved model file's modification date against the current business date.
2. **Background retrain** — If the model is stale, a daemon thread is spawned that:
   - Acquires a threading lock (prevents concurrent runs).
   - Fetches 120 days of item-level sales from the database.
   - Calls `train_pipeline(evaluate=False)` to retrain all three models (classifier + p50/p90 regressors) on the latest data.
   - Clears the in-memory model cache so the next API request loads the fresh artifacts.
3. **Non-blocking** — The current (slightly stale) forecast is still returned immediately. The user never sees an empty or error response.

### First-Launch Auto-Training (Fresh Install / .dmg)

On a fresh machine where no pre-trained model files exist:

1. The endpoint detects `FileNotFoundError` from `get_models()`.
2. Instead of returning a hard 503, it checks if the database has enough sales data (≥ 30 rows in the last 90 days).
3. If data exists (e.g., pulled in via cloud sync), it triggers background training and returns:
   ```json
   {"items": [], "forecast": [], "model_stale": true, "training_in_progress": true,
    "message": "Item demand models are being trained — refresh in ~30 seconds."}
   ```
4. If the database is empty (truly fresh install with no sales history), it returns:
   ```json
   {"items": [], "forecast": [], "training_in_progress": false,
    "message": "Not enough sales data to train models yet."}
   ```
5. Once enough sales accumulate, the next request auto-triggers training.

No seed model files need to be bundled in the `.dmg`. The system bootstraps itself from data.

This mirrors the existing GP forecaster's staleness pattern (`forecast.py → _load_and_check_stale → train_gp_task`).

**Limitations:**
- Single-worker only. For multi-worker deployments (uvicorn `--workers > 1`), add a file lock to prevent model file corruption.
- Training takes ~10–30 seconds depending on data size. During this window, requests still serve the old model.

---

## UI Visualization

**Forecast → Menu Items page chart:**
*   **Historical sales:** Solid line
*   **P50 forecast:** Dashed line
*   **P90 confidence:** Shaded area
*   **Probability:** Tooltip / color intensity

---

## Prediction Pipeline Details

### Autoregressive Forecasting

Multi-day forecasts are generated **one day at a time**. Each day's predicted p50 quantity is fed back into the running history as "actual" sales, so that the next day's lag and rolling features reflect prior forecasts rather than defaulting to zero. This prevents the common failure mode where lag features collapse to 0 after day 1.

### Future Weather Inference

When future weather data is unavailable, the system estimates it from the **last 7 days of historical averages** (temperature and rain). This replaces the naive constant fill (25°C / 0mm) that would bias predictions toward a fixed climate assumption.

### Degenerate Classifier Guard

If the loaded classifier was trained on single-class data (e.g., from an older model before densification), the prediction pipeline detects this via `classes_` and bypasses the classification gate, setting $P(\text{sold}) = 1.0$ for all items. This prevents all-zero forecasts with legacy models.

---

## Hyperparameter Tuning

Tuning is run **manually** via `tune.py` and does not slow normal training.

**Strategy:** Rolling walk-forward validation (4 folds at 60%/70%/80%/90% of dates, each predicting 7 days ahead).

**Objective:** Minimise **business cost**, not RMSE:

$$ \text{Cost} = 2 \times \sum \text{stockout units} + 1 \times \sum \text{waste units} $$

Stockouts are penalised at 2× because a lost sale is costlier than preparing one extra unit.

**Search:** Random search (30–40 trials) over:

| Model      | Parameters                                           |
| ---------- | ---------------------------------------------------- |
| Classifier | `num_leaves`, `min_data_in_leaf`, `learning_rate`, `feature_fraction` |
| Regressors | `num_leaves`, `min_data_in_leaf`, `learning_rate`    |

Results are saved to `best_params.json`. The normal training pipeline loads this file automatically — if it exists, the tuned parameters override defaults; if absent, hard-coded defaults are used with no change in behaviour.

---

## Why Not Deep Learning (LSTM/Transformer)?

1.  **Data Size:** Deep learning requires massive datasets (years of history) to generalize. We have ~7 months.
2.  **Sparsity:** Neural nets struggle with 50%+ zero values without complex loss functions (e.g., Tweedie).
3.  **Speed/Cost:** Trees train in seconds; DL models take minutes/hours and require GPUs.

---

## Final Conclusion

This system treats cafe demand as a **probabilistic behavioral process** rather than a smooth time series. By explicitly modeling **purchase probability** (calibrated) separately from **quantity**, and incorporating **store-level context** and **cold-start priors**, we align the forecast with real customer behavior to minimize both waste and stockouts.
