"""
Forecast Training Status — Centralized Singleton

Thread-safe module tracking whether any forecast training (revenue, item
demand, volume) is currently in progress.  Exposes:

  - start / update / log / finish — called by training tasks
  - get_status / is_training     — called by API handlers
  - signal_shutdown / is_shutting_down — graceful shutdown support

All public functions are protected by ``_lock`` so the request thread
and background training threads never see torn state.
"""

import threading
import time
from typing import Dict, Any

_lock = threading.Lock()

_MAX_LOG_LINES = 50

_status: Dict[str, Any] = {
    "active": False,
    "phase": "",           # "revenue" | "items" | "volume"
    "progress": 0,         # 0–100
    "message": "",         # short human-readable description
    "logs": [],            # rolling buffer of last N log lines
    "started_at": None,    # epoch float
}

_shutdown_event = threading.Event()


# ── Training lifecycle ──────────────────────────────────────────────

def start(phase: str, message: str) -> None:
    """Mark training as started.  Clears previous logs."""
    with _lock:
        _status["active"] = True
        _status["phase"] = phase
        _status["progress"] = 0
        _status["message"] = message
        _status["logs"] = [message]
        _status["started_at"] = time.time()


def update(progress: int, message: str) -> None:
    """Update progress (0–100) and message."""
    with _lock:
        _status["progress"] = progress
        _status["message"] = message
        _status["logs"].append(message)
        if len(_status["logs"]) > _MAX_LOG_LINES:
            _status["logs"] = _status["logs"][-_MAX_LOG_LINES:]


def log(line: str) -> None:
    """Append a log line visible to the frontend overlay."""
    with _lock:
        _status["logs"].append(line)
        if len(_status["logs"]) > _MAX_LOG_LINES:
            _status["logs"] = _status["logs"][-_MAX_LOG_LINES:]


def finish() -> None:
    """Mark training as complete.  Safe to call multiple times."""
    with _lock:
        _status["active"] = False
        _status["phase"] = ""
        _status["progress"] = 100
        _status["message"] = "Training complete"
        _status["started_at"] = None
        # Keep logs so the UI can show the final state briefly
        # before it refreshes


# ── Read helpers ────────────────────────────────────────────────────

def get_status() -> Dict[str, Any]:
    """Return a snapshot copy of the current status (safe to serialise)."""
    with _lock:
        snap = dict(_status)
        snap["logs"] = list(_status["logs"])  # copy the list
        return snap


def is_training() -> bool:
    with _lock:
        return _status["active"]


# ── Graceful shutdown ───────────────────────────────────────────────

def signal_shutdown() -> None:
    """Signal all training tasks to exit early."""
    _shutdown_event.set()


def is_shutting_down() -> bool:
    return _shutdown_event.is_set()
