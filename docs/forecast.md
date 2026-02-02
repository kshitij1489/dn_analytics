# Forecasting Methodology and Algorithms

This document outlines the technical approach used to generate sales forecasts in the Analytics Dashboard.

## 1. Data Source & Preparation

### Training Data
- **Window**: The model uses a rolling window of the last **90 days** of sales history.
- **Source**: Daily aggregation of successful orders (`order_status = 'Success'`).
- **Input Features**: 
  - `ds`: Date (Daily frequency)
  - `y`: Total Revenue (Sum of order totals)

### Missing Data Handling
- **Zero-Filling**: The system automatically detects missing dates within the 90-day range (e.g., store closures) and explicitly fills them with `0` revenue. This ensures a continuous time-series index required by advanced arithmetic models.

---

## 2. Algorithms Used

The system employs a "Committee of Machines" approach, running three distinct algorithms in parallel to provide a range of perspectives.

### A. Weekday Average (Baseline)
A heuristic model specifically optimized for weekly seasonality in hospitality.
- **Logic**: To forecast next Monday, we take the average revenue of the *last 4 Mondays*.
- **Formula**: $\hat{y}_{t} = \frac{1}{4} \sum_{i=1}^{4} y_{t - 7i}$
- **Best For**: Capturing "same-day-of-week" behavior which is the strongest signal in restaurant data.
- **Pros**: Extremely robust to outliers; easy to interpret.
- **Cons**: Slow to react to recent trends (e.g., a sudden 20% growth last week).

### B. Holt-Winters (Exponential Smoothing)
A statistical model that decomposes data into Level, Trend, and Seasonality.
- **Implementation**: `statsmodels.tsa.holtwinters.ExponentialSmoothing`
- **Configuration**:
  - `trend='add'`: Additive trend (linear growth/decline).
  - `seasonal='add'`: Additive seasonality (weekly peaks are constant amount, not percentage).
  - `seasonal_periods=7`: Explicitly set to model weekly cycles.
  - `damped_trend=True`: The trend is dampened over time to prevent unrealistic long-term growth predictions.
- **Best For**: Short-term forecasting where recent data is more important than older data.

### C. Prophet (Meta)
A generalized additive model designed for business time series.
- **Implementation**: `prophet.Prophet`
- **Configuration**:
  - `weekly_seasonality=True`: Models the 7-day cycle.
  - `daily_seasonality=False`: Turned off (we aggregate by day).
  - `yearly_seasonality=False`: Turned off (90 days is insufficient to learn yearly patterns).
  - `changepoint_prior_scale=0.05`: Controls flexibility to trend changes.
- **Best For**: Handling data with irregularities and finding the "underlying curve" amidst noise.

---

## 3. Training & Validation Strategy

### Production Training
- **Approach**: Refit-Every-Request.
- **Why**: In a live dashboard, we want the forecast to include the *very latest* data (even yesterday's sales). Therefore, we do not split data into "Train/Test" sets for the live API. We train on the full 90-day history to predict the next 7 days.

### Validation (Offline Verification)
During development, the models were validated using a "Time Slice" approach:
1.  **Training**: Days 0-83.
2.  **Validation**: Days 84-90 (Hidden from model).
3.  **Metric**: Models were evaluated on visual fit and non-negativity.

### Model Limitations
- **New Stores**: If data < 14 days, complex models (Prophet/HW) revert to zeros or error out safely.
- **Holidays**: Currently, we rely on Prophet's built-in flexibility rather than an explicit holiday calendar (e.g., Diwali/Christmas specific flags are not yet hardcoded).
