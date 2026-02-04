"""
Client learning / cloud upload configuration.

All URLs are read from environment variables. When the cloud server is ready,
set these to the real endpoints for plug-and-play. Empty string = skip that upload.

Placeholder defaults are used when env is not set so the full code path runs;
replace with real URLs via env when deploying.
"""

import os

# Placeholder base; replace via env for production
#_PLACEHOLDER_BASE = "https://placeholder-client-learning.example.com"
_PLACEHOLDER_BASE = "http://localhost"

# Error log ingest: POST batch of error records
CLIENT_LEARNING_ERROR_INGEST_URL = os.environ.get(
    "CLIENT_LEARNING_ERROR_INGEST_URL",
    f"{_PLACEHOLDER_BASE}/api/errors/ingest",
).strip()

# AI pipeline metadata + feedback: POST ai_logs + ai_feedback
CLIENT_LEARNING_INGEST_URL = os.environ.get(
    "CLIENT_LEARNING_INGEST_URL",
    f"{_PLACEHOLDER_BASE}/api/learning/ingest",
).strip()

# Menu bootstrap: POST id_maps + cluster_state (from backup JSON files)
CLIENT_LEARNING_MENU_BOOTSTRAP_INGEST_URL = os.environ.get(
    "CLIENT_LEARNING_MENU_BOOTSTRAP_INGEST_URL",
    f"{_PLACEHOLDER_BASE}/api/menu-bootstrap/ingest",
).strip()

# Optional: Bearer token for cloud API auth
CLIENT_LEARNING_API_KEY = os.environ.get("CLIENT_LEARNING_API_KEY", "").strip()


def _is_placeholder(url: str) -> bool:
    """True if URL is the placeholder (no real server)."""
    return "placeholder-client-learning.example.com" in (url or "")


def should_upload_errors() -> bool:
    """True if error upload is configured (non-empty URL)."""
    return bool(CLIENT_LEARNING_ERROR_INGEST_URL)


def should_upload_learning() -> bool:
    """True if learning ingest is configured (non-empty URL)."""
    return bool(CLIENT_LEARNING_INGEST_URL)


def should_upload_menu_bootstrap() -> bool:
    """True if menu bootstrap upload is configured (non-empty URL)."""
    return bool(CLIENT_LEARNING_MENU_BOOTSTRAP_INGEST_URL)
