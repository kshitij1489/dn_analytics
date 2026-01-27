---
description: React/TypeScript coding standards for ui_electron to prevent spaghetti code
---

# React/TypeScript Coding Standards

## 🚨 CRITICAL RULES (Must Follow)

### 1. NO Inline Styles - Use CSS Classes
```tsx
// ❌ BAD - Inline styles create duplication
<div style={{ padding: '10px', background: 'var(--card-bg)' }}>

// ✅ GOOD - Use CSS classes from App.css
<div className="card">
```

### 2. NO Duplicated Components - Check `src/components/` First
Before creating ANY component, check if it exists:
- `<Pagination />` - For page navigation
- `<LoadingSpinner />` - For loading states
- `<ActionButton />` - For buttons (variants: primary/secondary/danger/success)
- `<Select />` - For dropdowns
- `<Card />`, `<CollapsibleCard />` - For content containers
- `<KPICard />` - For dashboard metrics
- `<TabButton />` - For tab navigation
- `<ResizableTableWrapper />` - For resizable tables

**Import from barrel**: `import { Pagination, LoadingSpinner } from '../components';`

### 3. NO Duplicated Utilities - Check `src/utils/` First
- `exportToCSV()` - CSV export
- `sortData()` - Array sorting
- `getNextSortConfig()` - Toggle sort direction

**Import from barrel**: `import { exportToCSV, sortData } from '../utils';`

### 4. Tables MUST Use `standard-table` Class
```tsx
// ❌ BAD
<table style={{ width: '100%', borderCollapse: 'collapse' }}>

// ✅ GOOD
<table className="standard-table">
```

### 5. Use TypeScript Types from `src/types/`
```tsx
import { MenuItemRow, PaginatedResponse } from '../types';
```

---

## File Size Limits

| File Type | Max Lines | Action if Exceeded |
|-----------|-----------|-------------------|
| Component | 200 | Split into smaller components |
| Page | 400 | Extract sub-components to separate files |
| Utility | 100 | Split by functionality |

---

## Component Creation Checklist

When creating a NEW component:
1. [ ] Check `src/components/` - does it already exist?
2. [ ] Add TypeScript interface for props
3. [ ] Use CSS classes instead of inline styles
4. [ ] Export from `src/components/index.ts`
5. [ ] Add CSS to `App.css` with section header

---

## CSS Organization

All CSS goes in `App.css` with section headers:
```css
/* =================================
   COMPONENT_NAME
   ================================= */
```

Use CSS variables for theming:
- `var(--text-color)` - Primary text
- `var(--text-secondary)` - Secondary text
- `var(--accent-color)` - Accent/primary color
- `var(--card-bg)` - Card backgrounds
- `var(--border-color)` - Borders
- `var(--hover-bg)` - Hover states

---

## Import Order

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
