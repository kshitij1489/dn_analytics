---
description: General coding standards for the analytics project - READ FIRST before any code changes
---

# Analytics Project Coding Standards

## 🔴 STOP - Read This First

Before writing ANY code, you MUST check for existing implementations:

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

### Backend (`src/api/`)
| What you need | Check here |
|--------------|------------|
| DB connection | `dependencies.py` → `get_db` |
| DataFrame to JSON | `utils.py` → `df_to_json` |
| Pydantic models | `models.py` |

---

## Anti-Patterns to AVOID

### ❌ Copy-Paste Programming
If you're about to copy code from another file:
1. STOP
2. Extract to shared component/utility
3. Import in both places

### ❌ Inline Styles
Never use `style={{...}}` in React. Use CSS classes.

### ❌ Using `occurred_at` for Timestamps
**NEVER** use `occurred_at` for any date/time calculations. It is used for backend backfills and is unreliable.
**ALWAYS** use `created_on` (creation date) for all analytics, charts, and reports.

### ❌ Magic Numbers/Strings
```tsx
// ❌ BAD
style={{ padding: '10px' }}

// ✅ GOOD
className="card" // defined in App.css
```

### ❌ Large Files
Files over 400 lines need to be split.

---

## Quick Reference

### Shared Components
```tsx
import { 
  Pagination,
  LoadingSpinner,
  ActionButton,
  Select,
  Card,
  CollapsibleCard,
  KPICard,
  TabButton,
  ResizableTableWrapper
} from '../components';
```

### Shared Utilities
```tsx
import { exportToCSV, sortData, getNextSortConfig } from '../utils';
```

### Shared Types
```tsx
import { MenuItemRow, PaginatedResponse, KPIData } from '../types';
```

### Backend Imports
```python
from src.api.dependencies import get_db
from src.api.utils import df_to_json
from src.api.models import MergeRequest, VerifyRequest
```

---

## Related Standards
- `/react-standards` - Detailed React/TypeScript rules
- `/python-standards` - Detailed Python/FastAPI rules
