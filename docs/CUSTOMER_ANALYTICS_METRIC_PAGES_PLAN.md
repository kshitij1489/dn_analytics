# Customers Analytics Metric Pages Plan

## Goal

Implement dedicated `Customers -> Analytics` views for:

- `Customer Return Rate`
- `Customer Retention Rate`
- `Repeat Order Rate`

The first phase is a backend refactor so the KPI tiles and the `Customers -> Analytics -> Summary` table all use the same shared calculation layer before the detailed views are built.

## Current Touchpoints

- KPI tiles are rendered in `ui_electron/src/pages/Customers.tsx` and `ui_electron/src/pages/Insights.tsx`.
- `Customers -> Analytics` tabs live in `ui_electron/src/components/CustomerAnalyticsSection.tsx`.
- The current summary table is backed by `src/core/queries/customer_analytics_queries.py::fetch_customer_loyalty`.
- The current KPI tile calculations are backed by `src/core/queries/insights_queries.py::fetch_customer_quick_view`.
- A separate reorder KPI query also exists in `src/core/queries/customer_reorder_rate_queries.py::fetch_customer_reorder_rate`.

## Guiding Principles

- Keep metric definitions in one backend calculation layer.
- Preserve current KPI outputs in Phase 1 while moving the logic behind them.
- Make filter inputs configuration-driven so later analytics pages reuse the same logic.
- Reuse existing frontend primitives where possible, especially `DateSelector`, `Select`, `TabButton`, `KPICard`, and `ResizableTableWrapper`.
- Keep the first implementation inside the existing `Customers -> Analytics` tab structure unless routing becomes necessary later.

## Shared Metric Contract

Phase 1 should define a shared backend contract before any page work starts.

Suggested filter object:

```python
CustomerMetricFilters(
    evaluation_start_date: str,
    evaluation_end_date: str,
    lookback_start_date: str | None = None,
    lookback_end_date: str | None = None,
    lookback_days: int | None = None,
    min_orders_per_customer: int = 2,
    order_sources: tuple[str, ...] | None = None,  # None => All
    verified_only: bool = True,
    order_status: str = "Success",
)
```

Suggested shared responsibilities:

- Build one filtered order/customer base dataset.
- Normalize business-date boundaries the same way current analytics already do.
- Support order source filtering for `Swiggy`, `Zomato`, `POS`, `Home Website`, and `All`.
- Compute numerator, denominator, and percentage for each metric.
- Expose helper functions that can power both single KPI tiles and tabular analytics views.

Suggested outputs:

- `customer_return_rate`
- `customer_retention_rate`
- `repeat_order_rate`
- supporting counts such as evaluation customers, retained customers, returning customers, repeat-order customers
- reusable monthly or date-bucket rows for the summary table

## Phase 1: Consolidate Metric Calculations

### Scope

Refactor the existing calculations for:

- `Customer Return Rate`
- `Customer Retention Rate`
- `Repeat Order Rate`
- `Customers -> Analytics -> Summary` table

so they all depend on the same common helper layer.

### Work

- Create a shared metric module, for example under `src/core/queries/`, dedicated to customer rate calculations.
- Define the canonical input parameters for:
  - evaluation window begin and end dates
  - lookback window begin and end dates or `lookback_days`
  - repeat threshold such as `>= 2`, `>= 3`, or `>= n`
  - order source filter with `All` as no source restriction
- Split the logic into small reusable helpers:
  - filtered order base query
  - customer activity in evaluation window
  - customer activity in lookback window
  - metric calculators for return rate, retention rate, and repeat order rate
  - summary-row builder for the existing analytics summary table
- Refactor existing query call sites to use the shared helper:
  - `fetch_customer_quick_view`
  - `fetch_customer_loyalty`
  - `fetch_customer_reorder_rate` if it remains exposed
- Keep current response field names stable so the UI does not need to change yet.
- Add focused query-level tests covering:
  - current-month vs previous-month behavior
  - `lifetime` or multi-month lookback
  - different repeat thresholds
  - order source filtering
  - zero-denominator behavior

### Acceptance Criteria

- KPI tiles in `Customers` and `Insights` render the same values as before.
- The `Summary` table renders the same values as before.
- Metric logic exists in one place, with no duplicated SQL definitions for the same business rule.

## Phase 2: Customer Return Rate View

### Scope

Implement the `Customer Return Rate` analytics view inside `Customers -> Analytics`.

### UI

- Replace the current placeholder tab content in `CustomerAnalyticsSection.tsx`.
- Add filter controls for:
  - evaluation range start and end date
  - lookback range start and end date
  - return condition threshold such as `>= 2`, `>= 3`, `>= n`
  - order source dropdown with `All`, `Swiggy`, `Zomato`, `POS`, and `Home Website`
- Reuse current UI primitives for consistency with the rest of the app.
- Show at minimum:
  - headline return-rate KPI
  - numerator and denominator counts
  - detail table or date-bucket breakdown that can be exported

### Backend

- Add a dedicated endpoint for return-rate analytics that accepts the shared filter contract.
- Use the Phase 1 shared helper as the only calculation source.
- Return both percentage and supporting counts so the page can explain the result.

### Acceptance Criteria

- Users can change evaluation range, lookback range, threshold, and order source from the page.
- Changing filters updates the return-rate result without changing the underlying business definition in multiple places.

## Phase 3: Customer Retention Rate View

### Scope

Implement the `Customer Retention Rate` analytics view using the same shared filter model and UI pattern from Phase 2.

### UI

- Add the same evaluation-range and lookback-range calendar controls.
- Add the same threshold selector and order source dropdown.
- Keep layout and interaction consistent with the return-rate page.
- Show retention-specific counts clearly:
  - prior cohort size
  - retained customers
  - retention percentage

### Backend

- Add a dedicated retention-rate endpoint.
- Use the Phase 1 shared helper with retention-specific metric calculation only.
- Keep request and response shapes close to the return-rate page to reduce frontend branching.

### Acceptance Criteria

- The retention page feels identical in interaction model to the return-rate page.
- Retention logic is still driven only by the shared helper layer and not a new standalone SQL implementation.

## Phase 4: Repeat Order Rate View

### Scope

Implement the `Repeat Order Rate` analytics view using the same overall framework.

### UI

- Replace the current placeholder tab content.
- Add filter controls for:
  - evaluation range start and end date
  - repeat-order threshold such as `>= 2`, `>= 3`, `>= n`
  - order source dropdown with `All`
- Reuse the same visual structure as Phases 2 and 3 so the three analytics views feel like one system.
- Show at minimum:
  - repeat-order-rate KPI
  - repeat customers count
  - total evaluation customers
  - supporting table or breakdown

### Backend

- Add a dedicated repeat-order-rate endpoint.
- Use the same Phase 1 shared helper and shared filter model.
- Keep the threshold logic fully configurable rather than hard-coding `2+`.

### Acceptance Criteria

- Repeat-order-rate results are driven by the same filter semantics and shared calculation layer as the other two views.
- The page supports `All` and individual order-source filtering.

## Recommended Implementation Order

1. Build the shared filter contract and helper module.
2. Refactor current KPI and summary consumers to that helper without changing UI output.
3. Implement the return-rate view first and validate the filter model end to end.
4. Reuse the same page shell for retention.
5. Reuse the same page shell for repeat-order rate.

## Notes

- `All` order source should mean no `order_from` filter.
- The currently known order sources in the codebase are `Swiggy`, `Zomato`, `POS`, and `Home Website`.
- Phase 1 should preserve the existing verified-customer and successful-order assumptions unless product requirements change.
- Repeat Order Rate does not require lookback for the core formula, so its page can omit that control even if the other two views keep it.
