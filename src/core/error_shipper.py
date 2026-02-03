"""
Pluggable shipper for error log files. Reads logs/errors*.jsonl, batches, POSTs to cloud.

Uses CLIENT_LEARNING_ERROR_INGEST_URL (placeholder by default). When cloud server is ready,
set the env var to the real URL for plug-and-play. Call upload_pending() periodically.
"""

import glob
import hashlib
import json
import os
from typing import List, Optional, Tuple

from src.core.config.client_learning_config import (
    CLIENT_LEARNING_API_KEY,
    CLIENT_LEARNING_ERROR_INGEST_URL,
)
from src.core.error_log import DEFAULT_LOG_DIR, DEFAULT_ERROR_FILE


def _error_log_dir_and_pattern(log_dir: Optional[str] = None) -> Tuple[str, str]:
    """Return (absolute log dir, glob pattern for errors*.jsonl)."""
    base = log_dir or os.environ.get("ERROR_LOG_DIR") or DEFAULT_LOG_DIR
    if not os.path.isabs(base):
        base = os.path.abspath(base)
    pattern = os.path.join(base, "errors*.jsonl")
    return base, pattern


def _collect_error_files(pattern: str) -> List[Tuple[str, List[dict]]]:
    """
    Read all matching error log files. Returns list of (filepath, list of parsed JSON records).
    """
    paths = sorted(glob.glob(pattern))
    result: List[Tuple[str, List[dict]]] = []
    for path in paths:
        try:
            with open(path, "r", encoding="utf-8") as f:
                lines = []
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        lines.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
                if lines:
                    result.append((path, lines))
        except OSError:
            continue
    return result


def _build_records_with_ids(filepath: str, records: List[dict]) -> List[dict]:
    """Add a stable record_id to each record for idempotency."""
    out = []
    for i, rec in enumerate(records):
        raw = json.dumps(rec, sort_keys=True, default=str)
        rid = hashlib.sha256(f"{filepath}:{i}:{raw}".encode()).hexdigest()[:32]
        out.append({"record_id": rid, "payload": rec})
    return out


def upload_pending(
    log_dir: Optional[str] = None,
    endpoint: Optional[str] = None,
    auth: Optional[str] = None,
    uploaded_by: Optional[dict] = None,
) -> dict:
    """
    Read pending error log lines from logs/errors*.jsonl, batch, and upload to the cloud API.
    On success, truncates/removes the sent files so lines are not re-sent.
    uploaded_by: optional {"employee_id": "...", "name": "..."} from app_users; appended to payload.
    Returns {"sent": count, "error": str or None}.
    """
    url = (endpoint or CLIENT_LEARNING_ERROR_INGEST_URL).strip()
    if not url:
        return {"sent": 0, "error": None}

    base_dir, pattern = _error_log_dir_and_pattern(log_dir)
    collected = _collect_error_files(pattern)
    if not collected:
        return {"sent": 0, "error": None}

    all_records: List[dict] = []
    for path, records in collected:
        with_ids = _build_records_with_ids(path, records)
        all_records.extend(with_ids)

    token = auth if auth is not None else CLIENT_LEARNING_API_KEY
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    body: dict = {"records": all_records}
    if uploaded_by:
        body["uploaded_by"] = uploaded_by

    try:
        import requests
        r = requests.post(
            url,
            json=body,
            headers=headers,
            timeout=30,
        )
        if r.status_code >= 400:
            return {"sent": 0, "error": f"HTTP {r.status_code}"}
    except Exception as e:
        return {"sent": 0, "error": str(e)}

    # Success: truncate or remove sent files so we don't re-send
    main_path = os.path.join(base_dir, DEFAULT_ERROR_FILE)
    for path, _ in collected:
        try:
            if path == main_path:
                with open(path, "w", encoding="utf-8") as f:
                    pass  # truncate
            else:
                os.remove(path)
        except OSError:
            pass

    return {"sent": len(all_records), "error": None}
