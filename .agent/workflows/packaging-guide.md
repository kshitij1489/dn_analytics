---
description: Guide for managing dependencies and packaging the Python backend for .dmg builds
---

# Packaging & Dependency Management Guide

## 🚨 CRITICAL: Development vs. Production (.dmg)

When adding new Python libraries or changing file paths, you **MUST** ensure they work in both:
1.  **Development** (`npm run dev` / `python src/api/main.py`)
2.  **Production Frozen Build** (`.dmg` created by PyInstaller)

---

## 1. Adding New Dependencies

If you `pip install` a new package (e.g., `scikit-learn`, `joblib`), you must:

### A. Update `requirements.txt`
```bash
pip freeze > requirements.txt
# OR manually add the package version
```

### B. Update PyInstaller Spec (`installer/backend.spec`)
Many complex libraries (sklearn, pandas, numpy, scipy) require explicit collection of data files and binaries.

**Action:**
Open `installer/backend.spec` and check the `collect_all` or `hiddenimports` section.

```python
from PyInstaller.utils.hooks import collect_all

# Example: Collecting scikit-learn and joblib
datas = []
binaries = []
hiddenimports = []

# Collect all data/binaries for complex packages
tmp_ret = collect_all('sklearn')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]

tmp_ret = collect_all('joblib')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
```

**Failure to do this will result in `ModuleNotFoundError` or `DLL load failed` in the built .dmg, even if it works locally.**

---

## 2. File Paths & Read-Only Filesystem

In the `.dmg` (frozen) environment, the application runs from a **Read-Only** directory (e.g., `/Applications/D&N Analytics.app/...`).

### ❌ NEVER Write to Relative Paths
```python
# BAD - Works in dev, FAILS in .dmg (Read-only error)
with open("data/model.pkl", "wb") as f:
    f.write(data)
```

### ✅ ALWAYS Use User Data Directory
Write dynamic data (logs, databases, models) to the user's `Application Support` directory. The path is often passed via environment variables (e.g., `DB_URL`) or can be resolved using `platformdirs`.

```python
# GOOD - Dynamic path resolution
import os

# Use DB_URL directory (set by Electron main.js) or fallback to local 'data' for dev
db_url = os.environ.get('DB_URL')
if db_url:
    base_dir = os.path.dirname(db_url) # e.g., ~/Library/Application Support/D&N Analytics/
    storage_path = os.path.join(base_dir, 'my_file.pkl')
else:
    storage_path = 'data/my_file.pkl' # Dev fallback

with open(storage_path, "wb") as f:
    ...
```

---

## 3. Debugging Production Issues

Failures in the `.dmg` often fail silently or crash immediately.

### A. Logging is Mandatory
Wrap imports of heavy libraries in try-except blocks and log the error.

```python
import logging
logger = logging.getLogger(__name__)

try:
    import built_dependency
except ImportError as e:
    logger.error(f"Failed to import built_dependency: {e}")
```

### B. Viewing Logs
Production logs are written to:
`~/Library/Application Support/D&N Analytics/logs/backend.log`
