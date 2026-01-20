---
description: Python/FastAPI coding standards for src/api to prevent spaghetti code
---

# Python/FastAPI Coding Standards

## 🚨 CRITICAL RULES (Must Follow)

### 1. NO Duplicated Dependencies - Use `src/api/dependencies.py`
```python
# ❌ BAD - Duplicating get_db in router file
def get_db():
    conn, err = get_db_connection()
    ...

# ✅ GOOD - Import from dependencies
from src.api.dependencies import get_db
```

### 2. NO Duplicated Utilities - Use `src/api/utils.py`
```python
# ❌ BAD - Duplicating df_to_json in router file
def df_to_json(df):
    ...

# ✅ GOOD - Import from utils
from src.api.utils import df_to_json
```

### 3. ALL Pydantic Models in `src/api/models.py`
```python
# ❌ BAD - Defining model in router file
class VerifyRequest(BaseModel):
    menu_item_id: str

# ✅ GOOD - Import from models
from src.api.models import VerifyRequest
```

---

## File Organization

```
src/api/
├── main.py              # FastAPI app setup, routers
├── dependencies.py      # Shared dependencies (get_db)
├── utils.py            # Shared utilities (df_to_json)
├── models.py           # ALL Pydantic models
├── job_manager.py      # Async job handling
└── routers/
    ├── insights.py     # /api/insights/* endpoints
    ├── menu.py         # /api/menu/* endpoints
    ├── orders.py       # /api/orders/* endpoints
    └── resolutions.py  # /api/resolutions/* endpoints
```

---

## Router File Template

```python
"""
Router Name - Brief description

Provides endpoints for X, Y, Z.
"""

from fastapi import APIRouter, Depends, HTTPException
from src.api.dependencies import get_db
from src.api.utils import df_to_json
from src.api.models import MyRequest, MyResponse
from src.core.queries import my_queries

router = APIRouter()


@router.get("/endpoint")
def my_endpoint(conn=Depends(get_db)):
    """Brief docstring"""
    df = my_queries.fetch_data(conn)
    return df_to_json(df)
```

---

## File Size Limits

| File Type | Max Lines | Action if Exceeded |
|-----------|-----------|-------------------|
| Router | 150 | Split into multiple routers |
| Query file | 200 | Split by domain |
| Utility | 100 | Split by functionality |

---

## Endpoint Creation Checklist

When creating a NEW endpoint:
1. [ ] Add Pydantic model to `src/api/models.py` if needed
2. [ ] Use `get_db` from dependencies
3. [ ] Use `df_to_json` for DataFrame responses
4. [ ] Add docstring to endpoint
5. [ ] Handle errors with HTTPException

---

## Import Order

```python
# 1. Standard library
from typing import List, Optional
import json

# 2. Third-party
from fastapi import APIRouter, Depends, HTTPException
import pandas as pd

# 3. Local - API layer
from src.api.dependencies import get_db
from src.api.utils import df_to_json
from src.api.models import MyRequest

# 4. Local - Core layer
from src.core.queries import my_queries
```

---

## Error Handling Pattern

```python
@router.post("/action")
def my_action(req: MyRequest, conn=Depends(get_db)):
    result = some_function(conn, req.field)
    if result['status'] == 'error':
        raise HTTPException(status_code=400, detail=result['message'])
    return result
```
