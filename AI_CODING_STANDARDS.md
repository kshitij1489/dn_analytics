# Project: DN Analytics (Electron + React + Python)
# AI Instructions & Coding Standards

> **⚠️ AI AGENTS: Read this file BEFORE writing any code**

---

## Project Structure

```
analytics/
├── ui_electron/src/           # React/TypeScript frontend
│   ├── components/            # ✅ Shared components - CHECK HERE FIRST
│   ├── utils/                 # ✅ Shared utilities - CHECK HERE FIRST
│   ├── types/                 # ✅ TypeScript interfaces
│   ├── pages/                 # Page components
│   ├── api.ts                 # API client
│   └── App.css                # All CSS styles
│
├── src/api/                   # FastAPI backend
│   ├── dependencies.py        # ✅ Shared dependencies (get_db)
│   ├── utils.py              # ✅ Shared utilities (df_to_json)
│   ├── models.py             # ✅ ALL Pydantic models
│   └── routers/              # API route handlers
│
└── src/core/                  # Business logic
    ├── queries/               # SQL query functions
    └── db/                    # Database connection
```

---

## 🚨 CRITICAL RULES

### Rule 1: No Duplication - Check Existing Code First

**Frontend - Before creating components:**
```tsx
// ✅ Import from shared components
import { 
  Pagination,        // Page navigation
  LoadingSpinner,    // Loading states
  ActionButton,      // Buttons (primary/secondary/danger/success)
  Select,           // Dropdowns
  Card,             // Content cards
  CollapsibleCard,  // Expandable cards
  KPICard,          // Dashboard metrics
  TabButton,        // Tab navigation
  ResizableTableWrapper  // Resizable tables
} from '../components';

import { exportToCSV, sortData } from '../utils';
import { MenuItemRow, PaginatedResponse } from '../types';
```

**Backend - Before creating utilities:**
```python
# ✅ Import from shared modules
from src.api.dependencies import get_db
from src.api.utils import df_to_json
from src.api.models import MergeRequest, VerifyRequest
```

### Rule 2: No Inline Styles - Use CSS Classes

```tsx
// ❌ NEVER DO THIS
<div style={{ padding: '10px', background: 'var(--card-bg)' }}>

// ✅ ALWAYS DO THIS
<div className="card">
```

### Rule 3: Tables Must Use Standard Class

```tsx
// ❌ BAD
<table style={{ width: '100%', borderCollapse: 'collapse' }}>

// ✅ GOOD
<table className="standard-table">
  <th className="text-right">Amount</th>  // For right-aligned columns
  <th className="text-center">Status</th> // For centered columns
```

### Rule 4: All Pydantic Models in One File

```python
# ❌ BAD - Model in router file
class VerifyRequest(BaseModel):
    menu_item_id: str

# ✅ GOOD - All models in src/api/models.py
from src.api.models import VerifyRequest
```

---

## File Size Limits

| File Type | Max Lines | If Exceeded |
|-----------|-----------|-------------|
| React Component | 200 | Split into smaller components |
| React Page | 400 | Extract sub-components |
| Python Router | 150 | Split into multiple routers |
| Utility File | 100 | Split by functionality |

---

## Available Shared Components

### Pagination
```tsx
<Pagination 
  page={page}
  pageSize={pageSize} 
  total={total}
  onPageChange={setPage}
  onPageSizeChange={(size) => { setPageSize(size); setPage(1); }}
/>
```

### LoadingSpinner
```tsx
{loading ? <LoadingSpinner message="Loading data..." /> : <Content />}
```

### ActionButton
```tsx
<ActionButton variant="primary" onClick={handleSave}>Save</ActionButton>
<ActionButton variant="danger" onClick={handleDelete}>Delete</ActionButton>
<ActionButton variant="secondary" size="small">Cancel</ActionButton>
```

### Select
```tsx
<Select 
  value={selected}
  onChange={setSelected}
  options={[
    { value: 'opt1', label: 'Option 1' },
    { value: 'opt2', label: 'Option 2' }
  ]}
/>
```

---

## CSS Variables (Theme Support)

Use these in App.css for consistent theming:

```css
var(--text-color)       /* Primary text */
var(--text-secondary)   /* Secondary/muted text */
var(--accent-color)     /* Primary accent (blue) */
var(--card-bg)          /* Card backgrounds */
var(--border-color)     /* Borders */
var(--hover-bg)         /* Hover states */
var(--input-bg)         /* Input backgrounds */
var(--table-header-bg)  /* Table headers */
```

---

## Import Order

**React/TypeScript:**
```tsx
// 1. React
import { useState, useEffect } from 'react';
// 2. External libraries
import { Resizable } from 'react-resizable';
// 3. Local components
import { Pagination, LoadingSpinner } from '../components';
// 4. Local utilities
import { exportToCSV } from '../utils';
// 5. Types
import { MenuItemRow } from '../types';
// 6. API
import { endpoints } from '../api';
```

**Python:**
```python
# 1. Standard library
from typing import List, Optional
# 2. Third-party
from fastapi import APIRouter, Depends, HTTPException
# 3. Local API layer
from src.api.dependencies import get_db
from src.api.utils import df_to_json
# 4. Local core layer
from src.core.queries import menu_queries
```

---

## Anti-Patterns to AVOID

| ❌ Don't | ✅ Do Instead |
|----------|--------------|
| Copy-paste code | Extract to shared component/utility |
| Inline `style={{}}` | Use CSS classes |
| Define component in page file | Put in `src/components/` |
| Define Pydantic model in router | Put in `src/api/models.py` |
| Duplicate `get_db()` | Import from `dependencies.py` |
| Duplicate `df_to_json()` | Import from `utils.py` |
| Create file >400 lines | Split into smaller files |

---

## Checklist Before Submitting Code

- [ ] Checked `src/components/` for existing components
- [ ] Checked `src/utils/` for existing utilities
- [ ] No inline styles used
- [ ] Tables use `className="standard-table"`
- [ ] File is under size limit
- [ ] Imports follow correct order
- [ ] TypeScript types defined/imported
