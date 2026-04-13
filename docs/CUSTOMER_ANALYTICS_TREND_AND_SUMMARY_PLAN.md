# Customer analytics: monthly trend tables & Summary / Customer List toggle

This document is a **product and engineering plan** only. It does not implement features. It describes extending the four customer analytics tabs (Customer Return Rate, Customer Retention Rate, Repeat Order Rate, Customer affinity) with **month-over-month trend tables** and a **view toggle** so operators can switch between an aggregated **Summary** and the existing per-customer **Customer List**.

---

## 1. Goals

1. **Trend visibility** — For each analytics tab, show how headline metrics move **by calendar month** (or by business-month, aligned with the rest of the app) so growth and seasonality are easy to scan.
2. **Preserve existing behavior** — The current sortable table with per-customer rows (as in the screenshot: Customer, Status, Lookback Orders, Eval Orders, etc.) remains available as **“Customer List”**.
3. **Summary vs Customer List** — Add a **segmented toggle** (two options: **Summary** | **Customer List**) on the **same horizontal band as “Export CSV”** (same vertical alignment / row height), **left-aligned** in that band, while **Export CSV** stays **right-aligned** (existing position). Only one of Summary table or Customer List table is visible at a time.

---

## 2. UI: toggle placement and behavior

### 2.1 Layout

- **Row structure** (below KPI tiles and the textual filter summary line, above the main content):
  - **Left:** Segmented control — **Summary** | **Customer List** (single component, e.g. same `TabButton` / segmented style used elsewhere on Customers).
  - **Right:** **Export CSV** (unchanged behavior; still exports whichever view is active, or only Customer List if Summary export is deferred — see §6.3).
- **Vertical alignment:** Toggle group and Export CSV share one flex row (`display: flex; align-items: center; justify-content: space-between` or equivalent) so their **centers** align with the Export button height (same “row” as today’s CSV control).

### 2.2 Default selection

- **Default:** `Customer List` (current experience) so existing users are not surprised.
- Optional later: remember last selection in `localStorage` per tab.

### 2.3 Summary view

- When **Summary** is selected, hide the per-customer table and show the **new monthly trend table** (columns defined per tab — §3).
- When **Customer List** is selected, show the **existing** table and behavior (sorting, columns, empty states) as today.

### 2.4 Scope of toggle

- Apply the **same pattern** to all four tabs that have a detail table today:
  - Customer Return Rate  
  - Customer Retention Rate  
  - Repeat Order Rate  
  - Customer affinity  

Each tab owns its own **Summary** column set and API (or shared contract with parameters).

---

## 3. Summary tables: columns and default windows

Definitions should use **business date** (`business_date` / existing customer metric pipeline) consistently with Return / Retention / Repeat / Affinity today.

### 3.1 Customer affinity — Summary table

| Column | Meaning |
|--------|--------|
| **Month** | Label for the row (e.g. `YYYY-MM`), representing the **evaluation window** for that row. |
| **Customers in window** | Count of unique verified customers with ≥1 order in that month’s evaluation slice. |
| **New** | Count (and optionally **%** of row total) — same rules as current affinity: prior last order before month start is absent or ≥365d gap. |
| **Repeat** | Count (optional %) — prior last order within **60d** before that month’s start. |
| **Lapsed** | Count (optional %) — gap **61–364d** before month start. |

**Default horizon (product decision to confirm):** e.g. **all calendar months that intersect the last 60 calendar days**, or **last N full months** (N=2–3) fully contained in the trailing 60 days. Document the chosen rule in the UI hint so numbers are reproducible.

**Implementation note:** Reuse `analyze_customer_affinity` from `customer_metric_affinity.py` in a loop: for each month `M`, set `evaluation_start` / `evaluation_end` to month bounds of `M`, fetch orders through `evaluation_end` (and full history if needed for prior-last), aggregate.

---

### 3.2 Customer Return Rate — Summary table

Product intent (from discussion): trend with **evaluation = each month in range** and comparison across **lookback windows** (30d, 60d, lifetime).

**Proposed columns (one row per month):**

| Column | Meaning |
|--------|--------|
| **Month** | Evaluation month `M` (full month bounds). |
| **Return rate (30d lookback)** | Return rate for eval `M` with lookback = **30 days** immediately before eval start (or clarified window). |
| **Return rate (60d lookback)** | Same with **60d** lookback. |
| **Return rate (lifetime lookback)** | Same with **lifetime** lookback (unbounded start, end = day before eval start — align with existing “lifetime” semantics in quick view / calculators). |

Optional extra columns: **Returning customers**, **Evaluation customers**, etc., if operators want counts not only rates.

**Default horizon:** e.g. last **6 or 12** full months ending at current business month, or “months touched by last 90 days” — pick one and document.

**Implementation note:** Reuse `calculate_customer_return_rate` / `build_customer_return_rate_analysis` with synthetic `CustomerMetricFilters` per row; ensure `fetch_customer_metric_orders` date range covers eval + longest lookback.

---

### 3.3 Customer Retention Rate — Summary table

Same structural idea as Return Rate:

| Column | Meaning |
|--------|--------|
| **Month** | Evaluation month `M`. |
| **Retention rate (30d lookback)** | Cohort from 30d lookback → orders in `M` meeting `min_orders` (default from tab, e.g. ≥2). |
| **Retention rate (60d lookback)** | Same with 60d cohort. |
| **Retention rate (lifetime lookback)** | Cohort lifetime prior to `M` (per existing retention semantics). |

Optional: cohort sizes (prior cohort size, retained count) as extra columns.

**Default horizon:** Same as Return Rate for consistency.

---

### 3.4 Repeat Order Rate — Summary table

| Column | Meaning |
|--------|--------|
| **Month** | Evaluation month `M`. |
| **Repeat order rate** | Share of evaluation-window customers in `M` with order count ≥ threshold (reuse tab’s `min_orders_per_customer`). |

If the product also wants **30d / 60d / lifetime** here, clarify: Repeat Order Rate today is **only evaluation-window** (no lookback). A reasonable extension is **one rate per month** only, **or** add columns for different **thresholds** (e.g. ≥2 vs ≥3) instead of lookbacks. **Decision required** before build.

**Default horizon:** Align with other tabs (e.g. last 6–12 months).

---

## 4. Backend plan

### 4.1 API shape (options)

**Option A — Dedicated endpoints (clear, verbose)**  
- `GET .../customer/affinity_trend?months=...` or `start_date=&end_date=`  
- `GET .../customer/return_rate_trend?...`  
- `GET .../customer/retention_rate_trend?...`  
- `GET .../customer/repeat_order_rate_trend?...`  

**Option B — Single trend endpoint**  
- `GET .../customer/metric_trend?metric=affinity|return|retention|repeat&...`  

Return JSON: `{ "rows": [ { "month": "2026-03", ... }, ... ], "defaults": { ... } }`.

### 4.2 Computation strategy

1. **MVP:** Server loops months, calls existing pure builders / `analyze_customer_affinity` with constructed filters. Simple, matches drill-down logic exactly.  
2. **Scale-up (if slow):** SQL or batched queries that aggregate orders by `customer_id` × `business_month` once, then derive rates in memory — larger refactor.

### 4.3 Validation & errors

- Reject invalid ranges (end before start, range too large).  
- Cap maximum months per request (e.g. 24) to protect DB and timeouts.

---

## 5. Frontend plan

### 5.1 Shared component

- **`CustomerAnalyticsTrendToolbar`** (name TBD): flex row with **Summary | Customer List** on the left and **slot** or **Export CSV** on the right.  
- Used inside (or above) each of: `ReturnRateView`, `RetentionRateView`, `RepeatOrderRateView`, `AffinityView`.

### 5.2 State

- Local state per tab: `viewMode: 'summary' | 'customerList'`.  
- Default `'customerList'`.

### 5.3 Summary table

- Reuse `standard-table` styling, sortable headers if useful (month usually desc by default).  
- Loading / error consistent with existing views.  
- Empty state when no months in range.

### 5.4 Export CSV

- **Customer List:** Keep current export (per-customer rows).  
- **Summary:** Either **disable** CSV when Summary is selected, or **export summary rows** — product choice; document in release notes.

---

## 6. Open product questions (resolve before implementation)

1. **Affinity “previous 60 days”** — Exact mapping to **month rows** (partial month vs. only full months).  
2. **Return / Retention “30d / 60d / lifetime”** — Exact lookback boundaries (calendar vs. rolling business days) and alignment with quick-view “lifetime” definition.  
3. **Repeat Order Rate** — Confirm whether multi-column trend is **threshold-based**, **single rate per month**, or something else.  
4. **Export CSV** in Summary mode — supported or not.  
5. **Order source filter** — Should Summary respect the same **Order Source** filter as Customer List for each tab? (Recommended: yes.)

---

## 7. Testing plan

- Unit tests: for a fixed in-memory DB and frozen “today”, assert month rows for affinity and one rate type.  
- Contract tests: API response shape and required keys.  
- UI: smoke test toggle + data load + Export in Customer List mode.

---

## 8. Effort recap (from prior discussion)

- **MVP:** on the order of **a few days** once §6 is decided.  
- **Hardened** (performance caps, exports, edge cases, full tests): **up to ~1–2 weeks**.

---

## 9. Toggle UX requirement (explicit)

| Requirement | Detail |
|-------------|--------|
| **Control** | Two-option toggle: **Summary** \| **Customer List**. |
| **Position** | Same **vertical band / row** as **Export CSV** (aligned height). |
| **Horizontal layout** | Toggle **left-aligned**; **Export CSV** **right-aligned** in that row. |
| **Summary** | Shows the **new** month-level trend table for that tab. |
| **Customer List** | Shows the **existing** per-customer table (current screenshot behavior). |

This row should sit **immediately above** the active table (whether Summary or Customer List), matching the screenshot’s mental model: filters → KPIs → summary line → **[ Toggle … Export CSV ]** → table.

---

## 10. File / module touch list (when implementing)

**Backend (indicative):**  
`src/core/queries/customer_metric_affinity.py`, `customer_analytics_queries.py`, `customer_analytics.py` router, possibly `customer_metric_calculators.py` / analysis builders for thin wrappers.

**Frontend (indicative):**  
`ReturnRateView.tsx`, `RetentionRateView.tsx`, `RepeatOrderRateView.tsx`, `AffinityView.tsx`, shared toolbar component, `api.ts`, `types/api.ts`, `Customers.css` / analytics styles as needed.

---

*Document version: planning only. No feature flags or runtime behavior are changed by this file.*
