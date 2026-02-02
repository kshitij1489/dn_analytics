"""
Pluggable shipper for error log files. Future: read logs/errors*.jsonl, batch, POST to cloud.

See docs/ERROR_LOGGING_DESIGN.md. Current implementation is a no-op; add upload logic when
the cloud API is ready. Call upload_pending() periodically (e.g. scheduler or cron).
"""

from typing import Optional


def upload_pending(
    log_dir: Optional[str] = None,
    endpoint: Optional[str] = None,
    auth: Optional[str] = None,
) -> None:
    """
    Read pending error log lines from the local file(s), batch, and upload to the cloud API.
    Current: no-op. Future: read logs/errors.jsonl (and rotated files), POST to endpoint,
    then truncate or archive so lines are not re-sent.
    """
    # Placeholder; implement when cloud ingest API exists
    pass
