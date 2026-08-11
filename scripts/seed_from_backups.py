"""Explicit JSON export/restore CLI for menu catalog disaster recovery.

Runtime app paths must not read or write these JSON files. This script is a
manual support tool for dev/export snapshots and tier-3 recovery.

Typical live DB location:
    ~/Library/Application Support/dn-analytics/analytics.db

Point the script at a non-default DB with:
    DB_URL="/path/to/analytics.db" python3 scripts/seed_from_backups.py ...
or:
    python3 scripts/seed_from_backups.py --db "/path/to/analytics.db" ...
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Optional, Union

# Add project root to sys.path when run directly.
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.core.db.connection import get_db_connection
from src.core.menu_catalog_seed import build_cluster_state, build_id_maps, seed_catalog

DEFAULT_BACKUP_DIR = Path.home() / "dn-analytics-backups"
ID_MAPS_FILENAME = "id_maps_backup.json"
CLUSTER_STATE_FILENAME = "cluster_state_backup.json"


def _archive_paths(archive_dir: Path) -> tuple[Path, Path]:
    return archive_dir / ID_MAPS_FILENAME, archive_dir / CLUSTER_STATE_FILENAME


def _load_backup_payload(archive_dir: Path) -> tuple[dict, dict]:
    id_maps_path, cluster_state_path = _archive_paths(archive_dir)
    if not id_maps_path.exists() or not cluster_state_path.exists():
        raise FileNotFoundError(
            f"Backup files not found in {archive_dir}. Expected "
            f"{ID_MAPS_FILENAME} and {CLUSTER_STATE_FILENAME}."
        )

    with id_maps_path.open("r") as f:
        id_maps = json.load(f)
    with cluster_state_path.open("r") as f:
        cluster_state = json.load(f)
    return id_maps, cluster_state


def _print_seed_summary(counts: dict) -> None:
    print(
        f"Successfully seeded: {counts['items_seeded']} items, "
        f"{counts['variants_seeded']} variants, {counts['mappings_seeded']} mappings, "
        f"{counts['stub_count']} default variant stub(s); skipped "
        f"{counts['skipped_unmapped']} stale id_maps item(s) with no cluster_state key"
    )


def _rebuild_itemcodes_after_restore(conn) -> None:
    from src.core.itemcode_mapping import rebuild_itemcode_mappings_best_effort

    result = rebuild_itemcode_mappings_best_effort(conn)
    conn.commit()
    if result is None:
        print("Itemcode projection rebuild skipped; it can be rebuilt at next sync.")
    else:
        print(f"Itemcode projection rebuilt: {result.summary()}")


def perform_seeding(
    conn,
    seed_mappings: bool = False,
    archive_dir: Optional[Union[os.PathLike, str]] = None,
    rebuild_itemcodes: Optional[bool] = None,
) -> bool:
    """Restore menu data from explicit JSON backups.

    Defaults to catalog-only (menu_items, variants; no menu_item_variants
    upserts). seed_mappings=True is a contingency restore path for assignments
    and should be used only from an explicit support flow.
    """
    backup_dir = Path(archive_dir).expanduser() if archive_dir else DEFAULT_BACKUP_DIR
    should_rebuild_itemcodes = seed_mappings if rebuild_itemcodes is None else rebuild_itemcodes

    try:
        id_maps, cluster_state = _load_backup_payload(backup_dir)
        counts = seed_catalog(
            conn,
            id_maps,
            cluster_state,
            seed_mappings=seed_mappings,
        )
        _print_seed_summary(counts)
        if should_rebuild_itemcodes:
            _rebuild_itemcodes_after_restore(conn)
        return True
    except Exception as exc:
        print(f"Error during seeding: {exc}")
        return False


def export_to_backups(conn, out_dir: Optional[Union[os.PathLike, str]] = None) -> bool:
    """Dump database state to explicit JSON backups."""
    archive_dir = Path(out_dir).expanduser() if out_dir else DEFAULT_BACKUP_DIR
    archive_dir.mkdir(parents=True, exist_ok=True)
    id_maps_path, cluster_state_path = _archive_paths(archive_dir)

    try:
        print("Exporting ID Maps...")
        id_maps = build_id_maps(conn)

        print("Exporting Cluster State...")
        cluster_state = build_cluster_state(conn)

        with id_maps_path.open("w") as f:
            json.dump(id_maps, f, indent=2)
        with cluster_state_path.open("w") as f:
            json.dump(cluster_state, f, indent=2)

        print(f"Successfully exported backups to {archive_dir}")
        return True
    except Exception as exc:
        print(f"Error during export: {exc}")
        return False


def _confirm_restore(archive_dir: Path, *, catalog_only: bool, assume_yes: bool) -> bool:
    mode = "catalog only" if catalog_only else "catalog + assignment mappings"
    print(f"About to restore {mode} from: {archive_dir}")
    print("This writes to the configured SQLite database.")
    if assume_yes:
        return True
    try:
        response = input("Type RESTORE to continue: ").strip()
    except EOFError:
        return False
    return response == "RESTORE"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Export or restore menu catalog JSON backups explicitly.",
    )
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--export", action="store_true", help="Export DB catalog state to JSON.")
    action.add_argument("--restore", action="store_true", help="Restore DB catalog state from JSON.")
    parser.add_argument(
        "--out",
        default=str(DEFAULT_BACKUP_DIR),
        help=f"Export directory. Defaults to {DEFAULT_BACKUP_DIR}.",
    )
    parser.add_argument(
        "--from",
        dest="restore_from",
        help="Restore directory containing id_maps_backup.json and cluster_state_backup.json.",
    )
    parser.add_argument(
        "--catalog-only",
        action="store_true",
        help="For --restore, seed only menu_items and variants; skip assignment mappings.",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="For --restore, skip the interactive confirmation prompt.",
    )
    parser.add_argument(
        "--db",
        help=(
            "SQLite database path. Defaults to DB_URL, then the repo-root "
            "analytics.db used by get_db_connection."
        ),
    )
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.restore and not args.restore_from:
        parser.error("--restore requires --from DIR")
    if args.catalog_only and not args.restore:
        parser.error("--catalog-only is only valid with --restore")
    if args.yes and not args.restore:
        parser.error("--yes is only valid with --restore")

    conn, msg = get_db_connection(args.db)
    if not conn:
        print(f"Connection failed: {msg}")
        return 1

    try:
        print(f"Connected: {msg}")
        if args.export:
            return 0 if export_to_backups(conn, args.out) else 1

        restore_dir = Path(args.restore_from).expanduser()
        if not _confirm_restore(
            restore_dir,
            catalog_only=args.catalog_only,
            assume_yes=args.yes,
        ):
            print("Restore cancelled.")
            return 1
        return (
            0
            if perform_seeding(
                conn,
                seed_mappings=not args.catalog_only,
                archive_dir=restore_dir,
                rebuild_itemcodes=not args.catalog_only,
            )
            else 1
        )
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
