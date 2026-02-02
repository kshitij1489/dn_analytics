---
description: General coding standards for the analytics project - READ FIRST before any code changes
---

# Analytics Project Coding Standards

> **AI agents:** Read this file BEFORE writing any code.

---

## Project Structure

```
analytics/
├── ui_electron/src/           # React/TypeScript frontend
│   ├── components/            # ✅ Shared components - CHECK HERE FIRST
│   ├── utils/                 # ✅ Shared utilities - CHECK HERE FIRST
│   ├── types/                 # TypeScript interfaces
│   ├── pages/                 # Page components
│   ├── api.ts                 # API client
│   └── App.css                # All CSS styles
│
├── src/api/                   # FastAPI backend
│   ├── dependencies.py        # ✅ Shared dependencies (get_db)
│   ├── utils.py               # ✅ Shared utilities (df_to_json)
│   ├── models.py              # ✅ ALL Pydantic models
│   └── routers/               # API route handlers
│
├── src/core/                  # Business logic + shared infra
│   ├── queries/               # SQL query functions
│   ├── db/                    # Database connection
│   ├── error_log.py           # ✅ Structured error logging (get_error_logger, log_error)
│   └── error_shipper.py       # Future: upload error logs to cloud
│
├── ai_mode/                   # AI pipeline (orchestrator, handlers, LLM cache)
│   ├── orchestrator.py        # Spelling → intent → actions → execute
│   ├── handlers.py            # RUN_SQL, GENERATE_CHART, summary, report, etc.
│   ├── logging.py             # ai_logs DB (log_interaction)
│   ├── cache/                 # LLM response cache (get_or_call, get_or_call_diversity)
│   ├── llm/                   # Spelling, intent, sql_gen, chart, followup
│   └── prompts/               # Prompt strings
│
└── docs/                      # Plans and design (e.g. AI_MODE_PLAN.md)
```

---

## 🔴 STOP - Read This First

Before writing ANY code, check for existing implementations:

### Frontend (`ui_electron/src/`)
| What you need | Check here |
|--------------|------------|
| UI Component | `src/components/` |
| Utility function | `src/utils/` |
| TypeScript type | `src/types/` |
| Table styling | Use `className="standard-table"` |
| Button | Use `<ActionButton variant="primary">` |
| Pagination | Use `<Pagination />` component |
| Loading state | Use `<LoadingSpinner />` component |

### Backend (`src/api/`, `src/core/`, `ai_mode/`)
| What you need | Check here |
|--------------|------------|
| DB connection | `dependencies.py` → `get_db` |
| DataFrame to JSON | `utils.py` → `df_to_json` |
| Pydantic models | `models.py` |
| Error / crash logging | `src.core.error_log` → `get_error_logger()`, `log_error()` |

---

## 🚨 Critical Rules

### Rule 1: No Duplication
Import from shared modules; do not copy-paste. Extract to shared component/utility and import in both places.

### Rule 2: No Inline Styles (React)
```tsx
// ❌ NEVER
<div style={{ padding: '10px', background: 'var(--card-bg)' }}>

// ✅ ALWAYS
<div className="card">
```

### Rule 3: Tables Use Standard Class
```tsx
<table className="standard-table">
  <th className="text-right">Amount</th>
  <th className="text-center">Status</th>
```

### Rule 4: All Pydantic Models in One File
```python
# ❌ BAD - Model in router file
# ✅ GOOD - All models in src/api/models.py
from src.api.models import VerifyRequest
```

### Rule 5: Date Field Conventions
- **`created_on`**: PRIMARY for analytics, business date, charts, reports.
- **`created_at`**: SYSTEM audit only. Do not use for business logic.
- **`occurred_at`**: INVALID. Do not use for date/time calculations (backend backfills only).

---

## File Size Limits

| File Type | Max Lines | If Exceeded |
|-----------|-----------|--------------|
| React Component | 200 | Split into smaller components |
| React Page | 400 | Extract sub-components |
| Python Router | 150 | Split into multiple routers |
| Utility File | 100 | Split by functionality |

---

## Shared Components (examples)

```tsx
<Pagination page={page} pageSize={pageSize} total={total} onPageChange={setPage} onPageSizeChange={...} />
{loading ? <LoadingSpinner message="Loading..." /> : <Content />}
<ActionButton variant="primary" onClick={handleSave}>Save</ActionButton>
<ActionButton variant="danger" size="small">Cancel</ActionButton>
<Select value={selected} onChange={setSelected} options={[{ value: 'opt1', label: 'Option 1' }, ...]} />
```

Full list: Pagination, LoadingSpinner, ActionButton, Select, Card, CollapsibleCard, KPICard, TabButton, ResizableTableWrapper. Import from `../components`.

---

## CSS Variables (Theme)

Use in App.css for consistent theming:

`var(--text-color)` `var(--text-secondary)` `var(--accent-color)` `var(--card-bg)` `var(--border-color)` `var(--hover-bg)` `var(--input-bg)` `var(--table-header-bg)`

---

## Import Order

**React/TypeScript:** 1) React 2) External libraries 3) Local components 4) Local utilities 5) Types 6) API.

**Python:** 1) Standard library 2) Third-party 3) Local API (dependencies, utils, models) 4) Local core (queries, etc.).

---

## Anti-Patterns to AVOID

| ❌ Don't | ✅ Do Instead |
|----------|---------------|
| Copy-paste code | Extract to shared component/utility |
| Inline `style={{}}` | Use CSS classes |
| Define component in page file | Put in `src/components/` |
| Define Pydantic model in router | Put in `src/api/models.py` |
| Duplicate `get_db()` | Import from `dependencies.py` |
| Duplicate `df_to_json()` | Import from `utils.py` |
| Use `occurred_at` for analytics | Use `created_on` |
| File >400 lines (page) or over limits | Split into smaller files |

---

## Checklist Before Submitting

- [ ] Checked `src/components/` for existing components
- [ ] Checked `src/utils/` for existing utilities
- [ ] No inline styles used
- [ ] Tables use `className="standard-table"`
- [ ] File is under size limit
- [ ] Imports follow correct order
- [ ] TypeScript types defined/imported
- [ ] Date fields: use `created_on` for analytics; never `occurred_at`

---

## Related Standards

- **react-standards.md** — Detailed React/TypeScript rules
- **python-standards.md** — Detailed Python/FastAPI rules
