"""Test-suite isolation for the app-level control plane.

Global settings (cloud sync URL/key, integration credentials, AI keys) resolve
through `analytics-control.db` under the app-data root. Without this sandbox the
suite would read whatever the developer's real installation happens to hold, so
tests would pass or fail on machine state rather than on code.

Set here at import time — before any test module resolves a control path — and
torn down when the session ends. Tests that need their own root (profile binding,
hashed profile paths) still patch these variables locally with `patch.dict`.
"""

import atexit
import os
import tempfile
from pathlib import Path

_SANDBOX = tempfile.TemporaryDirectory(prefix="analytics-tests-")
_ROOT = Path(_SANDBOX.name)

os.environ["ANALYTICS_APP_DATA_ROOT"] = str(_ROOT)
os.environ["ANALYTICS_CONTROL_DB_PATH"] = str(_ROOT / "analytics-control.db")
os.environ["ANALYTICS_DB_PATH"] = str(_ROOT / "analytics.db")
os.environ["DB_URL"] = str(_ROOT / "analytics.db")

atexit.register(_SANDBOX.cleanup)
