"""Recoverable, profile-scoped database reset for controlled clean rebuilds."""

from __future__ import annotations

import hashlib
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Optional

from src.core.db.connection import apply_analytics_schema
from src.core.db.control import app_data_root, control_db_path
from src.core.profiles import (
    PROFILE_SCHEMA_VERSION,
    mark_profile_clean_rebuild,
)
from src.core.services.cloud_pull_orchestrator import CLOUD_PULL_LOCK


def _profile_target(profile) -> Path:
    root = app_data_root().resolve()
    target = Path(profile.database_path).expanduser().resolve()
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise RuntimeError(f"Profile database path is outside app-data root: {target}") from exc
    if target == control_db_path().resolve():
        raise RuntimeError("Refusing to reset the analytics control database")
    return target


def _archive_destination(target: Path, restaurant_id: str) -> Path:
    restaurant_key = hashlib.sha256(str(restaurant_id).encode("utf-8")).hexdigest()[:16]
    archive_dir = app_data_root().resolve() / "profile-archives" / restaurant_key
    archive_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    destination = archive_dir / f"{target.name}.{stamp}.db"
    if destination.exists():
        raise RuntimeError(f"Profile archive already exists: {destination}")
    return destination


def _sqlite_file_set(main_path: Path) -> tuple[Path, Path, Path, Path]:
    return (
        main_path,
        Path(f"{main_path}-wal"),
        Path(f"{main_path}-shm"),
        Path(f"{main_path}-journal"),
    )


def _archive_profile_files(target: Path, restaurant_id: str) -> tuple[Optional[Path], Dict[Path, Path]]:
    if not target.exists():
        return None, {}
    archive = _archive_destination(target, restaurant_id)
    moved: Dict[Path, Path] = {}
    destinations = _sqlite_file_set(archive)
    try:
        for source, destination in zip(_sqlite_file_set(target), destinations):
            if not source.exists():
                continue
            os.replace(source, destination)
            moved[source] = destination
    except Exception:
        for source, destination in reversed(tuple(moved.items())):
            if destination.exists() and not source.exists():
                os.replace(destination, source)
        raise
    return archive, moved


def _restore_archive_after_failure(
    target: Path,
    archive: Optional[Path],
    moved: Dict[Path, Path],
) -> None:
    """Retain a failed new file and put the pre-reset profile back in place."""
    if target.exists():
        failed_dir = (archive.parent if archive else target.parent / "profile-archives")
        failed_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
        failed_main = failed_dir / f"failed-rebuild-{target.name}.{stamp}"
        for source, destination in zip(_sqlite_file_set(target), _sqlite_file_set(failed_main)):
            if source.exists():
                os.replace(source, destination)
    for source, destination in reversed(tuple(moved.items())):
        if destination.exists() and not source.exists():
            os.replace(destination, source)


def reset_database(profile):
    """Archive and recreate exactly one captured physical restaurant profile.

    The old file is treated as opaque bytes: this path never connects to it,
    validates its identity, or applies revision-1.7 DDL in place. The control
    database and explicit recovery exports are outside the target and remain
    untouched. Ordinary Sync DB hydrates the canonical catalog afterwards.
    """
    target = None
    archive: Optional[Path] = None
    moved: Dict[Path, Path] = {}
    try:
        target = _profile_target(profile)
        target.parent.mkdir(parents=True, exist_ok=True)
        with CLOUD_PULL_LOCK:
            archive, moved = _archive_profile_files(target, profile.restaurant_id)
            try:
                conn = sqlite3.connect(
                    str(target), check_same_thread=False, timeout=30.0
                )
                try:
                    apply_analytics_schema(conn)
                    conn.execute(
                        """
                        INSERT INTO restaurant_profile_identity
                            (singleton_id, restaurant_id, bound_at, profile_schema_version)
                        VALUES (1, ?, CURRENT_TIMESTAMP, ?)
                        """,
                        (profile.restaurant_id, PROFILE_SCHEMA_VERSION),
                    )
                    conn.commit()
                finally:
                    conn.close()
                mark_profile_clean_rebuild(
                    profile.restaurant_id,
                    "complete",
                    archive_path=str(archive) if archive else None,
                )
            except Exception:
                _restore_archive_after_failure(target, archive, moved)
                raise

        archive_message = (
            f" Archived previous profile at {archive}." if archive else " No previous profile file existed."
        )
        return (
            True,
            "Database reset successfully." + archive_message + " Run Sync DB to rebuild this profile.",
        )
    except Exception as exc:
        return False, str(exc)
