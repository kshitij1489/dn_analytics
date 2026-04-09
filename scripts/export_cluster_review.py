"""
Export human-readable cluster review artifacts from the live SQLite database.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.core.db.connection import DB_PATH, get_db_connection


NULL_VARIANT_LABEL = "UNASSIGNED"
LIST_SEPARATOR = " | "


def _sorted_join(values: Iterable[str]) -> str:
    cleaned = sorted({value.strip() for value in values if value and value.strip()})
    return LIST_SEPARATOR.join(cleaned)


def _normalize_variant_name(variant_id: Optional[str], variant_lookup: Dict[str, str]) -> str:
    if variant_id in (None, "", "None"):
        return NULL_VARIANT_LABEL
    return variant_lookup.get(str(variant_id), str(variant_id))


def _parse_payload(raw_payload: Any) -> Any:
    if raw_payload is None:
        return None
    if isinstance(raw_payload, str):
        try:
            return json.loads(raw_payload)
        except json.JSONDecodeError:
            return None
    return raw_payload


def _extract_variant_assignments(payload: Any, variant_lookup: Dict[str, str]) -> List[str]:
    if not isinstance(payload, dict):
        return []

    assignments: Set[Tuple[Optional[str], Optional[str]]] = set()
    top_level_target = payload.get("target_variant_id")
    if "source_variant_id" in payload or top_level_target is not None:
        assignments.add((payload.get("source_variant_id"), top_level_target))

    for section in ("mapping_rows", "order_items", "order_item_addons"):
        for row in payload.get(section, []):
            assignments.add((row.get("old_variant_id"), row.get("new_variant_id")))

    rendered = []
    for source_variant_id, target_variant_id in sorted(assignments, key=lambda pair: (str(pair[0]), str(pair[1]))):
        rendered.append(
            f"{_normalize_variant_name(source_variant_id, variant_lookup)} -> "
            f"{_normalize_variant_name(target_variant_id, variant_lookup)}"
        )
    return rendered


def _extract_affected_source_ids(payload: Any) -> List[str]:
    if isinstance(payload, list):
        return sorted({str(item) for item in payload if item is not None})

    if not isinstance(payload, dict):
        return []

    affected_ids = {
        str(row["order_item_id"])
        for row in payload.get("mapping_rows", [])
        if row.get("order_item_id") is not None
    }
    return sorted(affected_ids)


def _payload_section_count(payload: Any, section: str) -> int:
    if not isinstance(payload, dict):
        return 0
    value = payload.get(section, [])
    return len(value) if isinstance(value, list) else 0


def _load_menu_lookup(conn) -> Dict[str, Dict[str, Any]]:
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            SELECT menu_item_id, name, type, is_verified
            FROM menu_items
            """
        )
        return {
            str(row["menu_item_id"]): {
                "name": row["name"],
                "type": row["type"],
                "is_verified": bool(row["is_verified"]),
            }
            for row in cursor.fetchall()
        }
    finally:
        cursor.close()


def _load_variant_lookup(conn) -> Dict[str, str]:
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            SELECT variant_id, variant_name
            FROM variants
            """
        )
        return {str(row["variant_id"]): row["variant_name"] for row in cursor.fetchall()}
    finally:
        cursor.close()


def _load_source_contexts(conn) -> Dict[str, Dict[str, Any]]:
    contexts: Dict[str, Dict[str, Any]] = defaultdict(
        lambda: {
            "item_rows": 0,
            "addon_rows": 0,
            "item_raw_names": set(),
            "addon_raw_names": set(),
        }
    )

    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            SELECT CAST(petpooja_itemid AS TEXT) AS source_item_id, name_raw
            FROM order_items
            WHERE petpooja_itemid IS NOT NULL
            """
        )
        for row in cursor.fetchall():
            source_item_id = row["source_item_id"]
            if not source_item_id:
                continue
            contexts[source_item_id]["item_rows"] += 1
            if row["name_raw"]:
                contexts[source_item_id]["item_raw_names"].add(row["name_raw"])

        cursor.execute(
            """
            SELECT CAST(petpooja_addonid AS TEXT) AS source_item_id, name_raw
            FROM order_item_addons
            WHERE petpooja_addonid IS NOT NULL
            """
        )
        for row in cursor.fetchall():
            source_item_id = row["source_item_id"]
            if not source_item_id:
                continue
            contexts[source_item_id]["addon_rows"] += 1
            if row["name_raw"]:
                contexts[source_item_id]["addon_raw_names"].add(row["name_raw"])
    finally:
        cursor.close()

    return contexts


def _source_kind(context: Dict[str, Any]) -> str:
    has_items = bool(context["item_rows"])
    has_addons = bool(context["addon_rows"])
    if has_items and has_addons:
        return "item+addon"
    if has_items:
        return "item"
    if has_addons:
        return "addon"
    return "unknown"


def _load_merge_history_rows(
    conn,
    menu_lookup: Dict[str, Dict[str, Any]],
    variant_lookup: Dict[str, str],
    source_contexts: Dict[str, Dict[str, Any]],
) -> Tuple[List[Dict[str, Any]], Dict[str, List[Dict[str, Any]]]]:
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            SELECT merge_id, source_id, target_id, source_name, source_type, affected_order_items, merged_at
            FROM merge_history
            ORDER BY merged_at ASC, merge_id ASC
            """
        )
        raw_rows = cursor.fetchall()
    finally:
        cursor.close()

    source_to_target: Dict[str, str] = {}
    for row in raw_rows:
        source_to_target[str(row["source_id"])] = str(row["target_id"])

    resolved_targets: Dict[str, str] = {}

    def resolve_target(menu_item_id: str) -> str:
        if menu_item_id in resolved_targets:
            return resolved_targets[menu_item_id]

        seen: Set[str] = set()
        current = menu_item_id
        while current in source_to_target and current not in seen:
            seen.add(current)
            current = source_to_target[current]

        resolved_targets[menu_item_id] = current
        return current

    lineage_by_final_target: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    history_rows: List[Dict[str, Any]] = []

    for row in raw_rows:
        payload = _parse_payload(row["affected_order_items"])
        source_item_ids = _extract_affected_source_ids(payload)
        current_target_id = resolve_target(str(row["target_id"]))
        current_target = menu_lookup.get(current_target_id, {})
        event_target = menu_lookup.get(str(row["target_id"]), {})

        raw_names: Set[str] = set()
        source_kinds: Set[str] = set()
        item_rows = 0
        addon_rows = 0
        for source_item_id in source_item_ids:
            context = source_contexts.get(source_item_id)
            if not context:
                continue
            item_rows += int(context["item_rows"])
            addon_rows += int(context["addon_rows"])
            raw_names.update(context["item_raw_names"])
            raw_names.update(context["addon_raw_names"])
            source_kinds.add(_source_kind(context))

        history_row = {
            "merge_id": int(row["merge_id"]),
            "merged_at": row["merged_at"],
            "payload_kind": payload.get("kind") if isinstance(payload, dict) else "legacy_merge_v1",
            "source_id": str(row["source_id"]),
            "source_name": row["source_name"],
            "source_type": row["source_type"],
            "target_id_at_event": str(row["target_id"]),
            "target_name_at_event": event_target.get("name", str(row["target_id"])),
            "current_target_id": current_target_id,
            "current_target_name": current_target.get("name", current_target_id),
            "current_target_type": current_target.get("type", ""),
            "variant_assignments": _sorted_join(_extract_variant_assignments(payload, variant_lookup)),
            "affected_source_item_count": len(source_item_ids),
            "affected_source_item_ids": _sorted_join(source_item_ids),
            "source_item_kinds": _sorted_join(source_kinds),
            "item_row_count": item_rows,
            "addon_row_count": addon_rows,
            "raw_name_count": len(raw_names),
            "raw_names": _sorted_join(raw_names),
            "mapping_rows_count": max(len(source_item_ids), _payload_section_count(payload, "mapping_rows")),
            "order_item_rows_count": _payload_section_count(payload, "order_items"),
            "order_item_addons_count": _payload_section_count(payload, "order_item_addons"),
        }
        history_rows.append(history_row)
        lineage_by_final_target[current_target_id].append(history_row)

    history_rows.sort(key=lambda row: (row["merged_at"], row["merge_id"]), reverse=True)
    return history_rows, lineage_by_final_target


def _load_current_cluster_rows(
    conn,
    menu_lookup: Dict[str, Dict[str, Any]],
    variant_lookup: Dict[str, str],
    source_contexts: Dict[str, Dict[str, Any]],
    lineage_by_final_target: Dict[str, List[Dict[str, Any]]],
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            SELECT order_item_id, menu_item_id, variant_id, is_verified
            FROM menu_item_variants
            ORDER BY menu_item_id, variant_id, order_item_id
            """
        )
        mapping_rows = cursor.fetchall()
    finally:
        cursor.close()

    membership_rows: List[Dict[str, Any]] = []
    summary_index: Dict[Tuple[str, str], Dict[str, Any]] = {}

    for row in mapping_rows:
        source_item_id = str(row["order_item_id"])
        menu_item_id = str(row["menu_item_id"])
        variant_id = None if row["variant_id"] in (None, "", "None") else str(row["variant_id"])
        menu_info = menu_lookup.get(menu_item_id, {})
        variant_name = _normalize_variant_name(variant_id, variant_lookup)
        context = source_contexts.get(
            source_item_id,
            {
                "item_rows": 0,
                "addon_rows": 0,
                "item_raw_names": set(),
                "addon_raw_names": set(),
            },
        )
        raw_names = set(context["item_raw_names"]) | set(context["addon_raw_names"])
        merged_sources = {
            history_row["source_name"]
            for history_row in lineage_by_final_target.get(menu_item_id, [])
        }

        membership_row = {
            "parent_cluster_name": menu_info.get("name", menu_item_id),
            "parent_cluster_type": menu_info.get("type", ""),
            "child_cluster_name": variant_name,
            "menu_item_id": menu_item_id,
            "variant_id": variant_id or "",
            "source_item_id": source_item_id,
            "source_item_kind": _source_kind(context),
            "item_row_count": int(context["item_rows"]),
            "addon_row_count": int(context["addon_rows"]),
            "raw_name_count": len(raw_names),
            "raw_names": _sorted_join(raw_names),
            "mapping_is_verified": bool(row["is_verified"]),
            "historical_merged_source_names": _sorted_join(merged_sources),
        }
        membership_rows.append(membership_row)

        summary_key = (menu_item_id, variant_id or "")
        if summary_key not in summary_index:
            summary_index[summary_key] = {
                "parent_cluster_name": membership_row["parent_cluster_name"],
                "parent_cluster_type": membership_row["parent_cluster_type"],
                "child_cluster_name": membership_row["child_cluster_name"],
                "menu_item_id": menu_item_id,
                "variant_id": variant_id or "",
                "mapping_count": 0,
                "verified_mapping_count": 0,
                "source_item_ids": set(),
                "source_item_kinds": set(),
                "raw_names": set(),
                "item_row_count": 0,
                "addon_row_count": 0,
                "historical_merged_source_names": merged_sources.copy(),
            }

        summary = summary_index[summary_key]
        summary["mapping_count"] += 1
        summary["verified_mapping_count"] += int(bool(row["is_verified"]))
        summary["source_item_ids"].add(source_item_id)
        summary["source_item_kinds"].add(membership_row["source_item_kind"])
        summary["raw_names"].update(raw_names)
        summary["item_row_count"] += int(context["item_rows"])
        summary["addon_row_count"] += int(context["addon_rows"])
        summary["historical_merged_source_names"].update(merged_sources)

    cluster_summary_rows: List[Dict[str, Any]] = []
    for summary in summary_index.values():
        cluster_summary_rows.append(
            {
                "parent_cluster_name": summary["parent_cluster_name"],
                "parent_cluster_type": summary["parent_cluster_type"],
                "child_cluster_name": summary["child_cluster_name"],
                "menu_item_id": summary["menu_item_id"],
                "variant_id": summary["variant_id"],
                "mapping_count": summary["mapping_count"],
                "verified_mapping_count": summary["verified_mapping_count"],
                "source_item_count": len(summary["source_item_ids"]),
                "source_item_ids": _sorted_join(summary["source_item_ids"]),
                "source_item_kinds": _sorted_join(summary["source_item_kinds"]),
                "item_row_count": summary["item_row_count"],
                "addon_row_count": summary["addon_row_count"],
                "raw_name_count": len(summary["raw_names"]),
                "raw_names": _sorted_join(summary["raw_names"]),
                "historical_merged_source_count": len(summary["historical_merged_source_names"]),
                "historical_merged_source_names": _sorted_join(summary["historical_merged_source_names"]),
            }
        )

    membership_rows.sort(
        key=lambda row: (
            row["parent_cluster_name"].lower(),
            row["child_cluster_name"].lower(),
            row["source_item_id"],
        )
    )
    cluster_summary_rows.sort(
        key=lambda row: (
            row["parent_cluster_name"].lower(),
            row["child_cluster_name"].lower(),
        )
    )
    return membership_rows, cluster_summary_rows


def _write_csv(path: Path, rows: List[Dict[str, Any]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return

    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def _write_cluster_markdown(
    path: Path,
    cluster_summary_rows: List[Dict[str, Any]],
    membership_rows: List[Dict[str, Any]],
    generated_at: str,
) -> None:
    members_by_cluster: Dict[Tuple[str, str], List[Dict[str, Any]]] = defaultdict(list)
    for row in membership_rows:
        members_by_cluster[(row["menu_item_id"], row["variant_id"])].append(row)

    lines = [
        "# Current Cluster Review",
        "",
        f"Generated at: {generated_at}",
        "",
        "Each section shows the current parent cluster, child cluster, source item IDs, and raw names observed on order rows/addons.",
        "",
    ]

    for summary in cluster_summary_rows:
        key = (summary["menu_item_id"], summary["variant_id"])
        lines.append(
            f"## {summary['parent_cluster_name']} [{summary['parent_cluster_type']}] :: {summary['child_cluster_name']}"
        )
        lines.append(f"- menu_item_id: {summary['menu_item_id']}")
        lines.append(f"- variant_id: {summary['variant_id'] or 'NULL'}")
        lines.append(
            f"- mappings: {summary['mapping_count']} total, {summary['verified_mapping_count']} verified"
        )
        lines.append(
            f"- rows: {summary['item_row_count']} item rows, {summary['addon_row_count']} addon rows"
        )
        lines.append(f"- source item ids: {summary['source_item_ids'] or '(none)'}")
        lines.append(f"- raw names: {summary['raw_names'] or '(none)'}")
        if summary["historical_merged_source_names"]:
            lines.append(
                f"- historical merged source clusters: {summary['historical_merged_source_names']}"
            )
        else:
            lines.append("- historical merged source clusters: (none)")
        lines.append("")
        for member in members_by_cluster.get(key, []):
            lines.append(
                f"  - source_item_id={member['source_item_id']} "
                f"[{member['source_item_kind']}] "
                f"item_rows={member['item_row_count']} "
                f"addon_rows={member['addon_row_count']} "
                f"raw_names={member['raw_names'] or '(none)'}"
            )
        lines.append("")

    path.write_text("\n".join(lines), encoding="utf-8")


def _write_merge_history_markdown(
    path: Path,
    history_rows: List[Dict[str, Any]],
    generated_at: str,
) -> None:
    lines = [
        "# Merge History Review",
        "",
        f"Generated at: {generated_at}",
        "",
        "Each section shows a historical merge event, the source and target names, the affected source item IDs, and raw names currently linked to those IDs.",
        "",
    ]

    for row in history_rows:
        lines.append(
            f"## Merge {row['merge_id']} :: {row['source_name']} -> {row['target_name_at_event']}"
        )
        lines.append(f"- merged_at: {row['merged_at']}")
        lines.append(f"- payload_kind: {row['payload_kind']}")
        lines.append(f"- source_type: {row['source_type']}")
        lines.append(
            f"- current_final_target: {row['current_target_name']} [{row['current_target_type']}]"
        )
        lines.append(f"- variant_assignments: {row['variant_assignments'] or '(none recorded)'}")
        lines.append(f"- affected source item ids: {row['affected_source_item_ids'] or '(none recorded)'}")
        lines.append(f"- raw names: {row['raw_names'] or '(none found)'}")
        lines.append(
            f"- counts: mappings={row['mapping_rows_count']} "
            f"order_items={row['order_item_rows_count']} "
            f"addons={row['order_item_addons_count']}"
        )
        lines.append("")

    path.write_text("\n".join(lines), encoding="utf-8")


def export_cluster_review(output_dir: Path) -> Dict[str, Path]:
    conn, message = get_db_connection()
    if conn is None:
        raise RuntimeError(f"Could not connect to database: {message}")

    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S %Z")
    output_dir.mkdir(parents=True, exist_ok=True)

    try:
        menu_lookup = _load_menu_lookup(conn)
        variant_lookup = _load_variant_lookup(conn)
        source_contexts = _load_source_contexts(conn)
        history_rows, lineage_by_final_target = _load_merge_history_rows(
            conn,
            menu_lookup,
            variant_lookup,
            source_contexts,
        )
        membership_rows, cluster_summary_rows = _load_current_cluster_rows(
            conn,
            menu_lookup,
            variant_lookup,
            source_contexts,
            lineage_by_final_target,
        )
    finally:
        conn.close()

    cluster_csv = output_dir / "current_clusters.csv"
    cluster_members_csv = output_dir / "current_cluster_members.csv"
    cluster_md = output_dir / "current_clusters.md"
    merge_csv = output_dir / "merge_history.csv"
    merge_md = output_dir / "merge_history.md"

    _write_csv(cluster_csv, cluster_summary_rows)
    _write_csv(cluster_members_csv, membership_rows)
    _write_csv(merge_csv, history_rows)
    _write_cluster_markdown(cluster_md, cluster_summary_rows, membership_rows, generated_at)
    _write_merge_history_markdown(merge_md, history_rows, generated_at)

    return {
        "cluster_summary_csv": cluster_csv,
        "cluster_members_csv": cluster_members_csv,
        "cluster_summary_md": cluster_md,
        "merge_history_csv": merge_csv,
        "merge_history_md": merge_md,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Export cluster review lists from analytics.db")
    parser.add_argument(
        "--output-dir",
        default="tmp/cluster_review",
        help=f"Directory for generated review files (default: tmp/cluster_review, DB: {DB_PATH})",
    )
    args = parser.parse_args()

    outputs = export_cluster_review(Path(args.output_dir))
    for label, path in outputs.items():
        print(f"{label}: {path}")


if __name__ == "__main__":
    main()
