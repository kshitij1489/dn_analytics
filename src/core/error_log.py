"""
Structured error logging to a JSON Lines file for local persistence and future cloud upload.

See docs/AI_MODE_PLAN.md. All records go to logs/errors.jsonl (one JSON object per line).
Uses a rotating file handler so the file does not grow unbounded.
"""

import json
import logging
import os
import traceback
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from typing import Any, Dict, Optional

# Default path relative to cwd; can be overridden via env or config
DEFAULT_LOG_DIR = "logs"
DEFAULT_ERROR_FILE = "errors.jsonl"
MAX_BYTES = 5 * 1024 * 1024  # 5 MB
BACKUP_COUNT = 3

_error_logger: Optional[logging.Logger] = None


def _ensure_log_dir(log_dir: str) -> None:
    os.makedirs(log_dir, exist_ok=True)


def _record_to_dict(record: logging.LogRecord) -> Dict[str, Any]:
    """Build a single JSON-serializable dict from a LogRecord for one JSONL line."""
    ts = datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat()
    out = {
        "ts": ts,
        "level": record.levelname,
        "message": record.getMessage(),
    }
    if record.exc_info:
        exc_type, exc_val, _ = record.exc_info
        out["exception"] = f"{exc_type.__name__ if exc_type else 'Unknown'}: {exc_val}"
        out["traceback"] = "".join(traceback.format_exception(*record.exc_info))
    else:
        out["exception"] = getattr(record, "exception", None)
        out["traceback"] = getattr(record, "traceback", None)
    context = getattr(record, "context", None)
    if context is not None and isinstance(context, dict):
        out["context"] = context
    return out


class JsonlRotatingFileHandler(RotatingFileHandler):
    """Writes one JSON object per line (JSONL). Rotates by size."""

    def emit(self, record: logging.LogRecord) -> None:
        try:
            d = _record_to_dict(record)
            line = json.dumps(d, default=str) + "\n"
            self.stream.write(line)
            self.flush()
        except Exception:
            self.handleError(record)


def get_error_logger(
    log_dir: Optional[str] = None,
    filename: Optional[str] = None,
    max_bytes: int = MAX_BYTES,
    backup_count: int = BACKUP_COUNT,
) -> logging.Logger:
    """Return the app-wide error logger, creating it and attaching the JSONL file handler if needed."""
    global _error_logger
    if _error_logger is not None:
        return _error_logger

    log_dir = log_dir or os.environ.get("ERROR_LOG_DIR") or DEFAULT_LOG_DIR
    filename = filename or os.environ.get("ERROR_LOG_FILE") or DEFAULT_ERROR_FILE
    path = os.path.join(log_dir, filename)
    _ensure_log_dir(log_dir)

    logger = logging.getLogger("analytics.errors")
    logger.setLevel(logging.ERROR)
    logger.propagate = False

    handler = JsonlRotatingFileHandler(
        path,
        maxBytes=max_bytes,
        backupCount=backup_count,
        encoding="utf-8",
    )
    logger.addHandler(handler)
    _error_logger = logger
    return logger


def log_error(
    message: str,
    exception: Optional[Exception] = None,
    context: Optional[Dict[str, Any]] = None,
    error_kind: Optional[str] = None,
) -> None:
    """
    Log an error to the JSONL file with optional context (e.g. action, user_query, generated_sql).
    Use this from handlers that catch pipeline failures (e.g. RUN_SQL execution error).
    """
    logger = get_error_logger()
    extra: Dict[str, Any] = {"context": context or {}}
    if error_kind is not None:
        extra["context"] = {**(context or {}), "error_kind": error_kind}
    if exception is not None:
        logger.error(message, exc_info=True, extra=extra)
    else:
        logger.error(message, extra=extra)
