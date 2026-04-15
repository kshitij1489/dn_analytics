"""
Export cluster review for one menu_items.type (e.g. Combo, Drinks, Service, Extra, Dessert, Ice Cream).

Default output directory is menu_cleansing_exercise/<type_slug>_cluster_review (e.g.
menu_cleansing_exercise/extra_cluster_review/). Markdown uses a numbered hierarchy:
  1) Parent cluster name, Type
    a) Variant (child cluster) name
      1) Raw menu item name(s) from order rows

A trailing section maps hierarchy refs (e.g. 1-a-1) to database ids for tracing.
"""

from __future__ import annotations

import argparse
import re
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import export_cluster_review as ecr  # noqa: E402

from src.core.db.connection import DB_PATH, get_db_connection  # noqa: E402


def _type_stem(cluster_type: str) -> str:
    """Lowercase slug for paths and filenames (e.g. Combo -> combo, Ice Cream -> ice_cream)."""
    return cluster_type.strip().lower().replace(" ", "_")


def _resolve_cluster_type_arg(raw: str) -> str:
    """
    Map CLI-friendly spellings to the exact menu_items.type / parent_cluster_type string.

    Ice Cream is the only multi-word type in this database; callers may pass ice_cream / Ice_Cream.
    """
    s = raw.strip()
    if not s:
        return s
    key = re.sub(r"[\s_]+", "_", s.lower())
    if key in ("ice_cream", "icecream"):
        return "Ice Cream"
    return s


def _default_output_dir(cluster_type: str) -> str:
    return f"{ecr.MENU_STATE_EVALUATION_ROOT}/{_type_stem(cluster_type)}_cluster_review"


def _variant_letter(index: int) -> str:
    """0 -> a, 1 -> b, ... 25 -> z, 26 -> aa (same spirit as Excel columns)."""
    s = ""
    n = index
    while True:
        s = chr(ord("a") + (n % 26)) + s
        n = n // 26 - 1
        if n < 0:
            break
    return s


def _build_mapping_line_details(
    conn: Any, combo_membership_rows: List[Dict[str, Any]]
) -> Dict[Tuple[str, str, str], Dict[str, Any]]:
    """Key: (source_item_id, menu_item_id, variant_id or '')."""
    out: Dict[Tuple[str, str, str], Dict[str, Any]] = {}
    seen: Set[Tuple[str, str, str]] = set()
    for row in combo_membership_rows:
        variant_id = row["variant_id"] or ""
        key = (row["source_item_id"], row["menu_item_id"], variant_id)
        if key in seen:
            continue
        seen.add(key)
        vid: Optional[str] = None if not variant_id else variant_id
        items, addons, rel_i, rel_a = ecr.fetch_order_lines_for_mapping_review(
            conn, row["source_item_id"], row["menu_item_id"], vid
        )
        out[key] = {
            "order_items": items,
            "order_item_addons": addons,
            "relaxed_items": rel_i,
            "relaxed_addons": rel_a,
        }
    return out


def _md_escape_cell(text: Any) -> str:
    s = "" if text is None else str(text)
    return s.replace("|", "\\|")


def _compact_id_csv(ids: List[str], *, max_shown: int = 20) -> str:
    """Keep the ID index table readable when many order lines exist."""
    if not ids:
        return "(none)"
    if len(ids) <= max_shown:
        return ",".join(ids)
    head = ",".join(ids[:max_shown])
    return f"{head},… (+{len(ids) - max_shown} more; full list above)"


def _split_raw_names(raw_names: str) -> List[str]:
    if not raw_names or raw_names == "(none)":
        return []
    parts = [p.strip() for p in raw_names.split(ecr.LIST_SEPARATOR) if p.strip()]
    return parts if parts else [raw_names.strip()]


def _load_category_merge_rows(
    history_rows: List[Dict[str, Any]], category_menu_item_ids: Set[str]
) -> List[Dict[str, Any]]:
    """Merge events that involve at least one exported cluster menu_item_id in this category."""
    out: List[Dict[str, Any]] = []
    for row in history_rows:
        if row["current_target_id"] in category_menu_item_ids:
            out.append(row)
            continue
        if row["source_id"] in category_menu_item_ids:
            out.append(row)
            continue
        if row["target_id_at_event"] in category_menu_item_ids:
            out.append(row)
    # De-dupe while preserving order
    seen: Set[int] = set()
    deduped: List[Dict[str, Any]] = []
    for row in out:
        mid = row["merge_id"]
        if mid in seen:
            continue
        seen.add(mid)
        deduped.append(row)
    return deduped


def _append_line_list_markdown(
    lines: List[str],
    title: str,
    rows: List[Dict[str, Any]],
    *,
    kind: str,
) -> None:
    n = len(rows)
    lines.append(f"  - **{title}** ({n}):")
    if not rows:
        lines.append("    - _(none)_")
        return
    for i, r in enumerate(rows, start=1):
        if kind == "item":
            pet = r.get("petpooja_itemid")
            pet_s = f"petpooja_itemid={pet}" if pet is not None else "petpooja_itemid=(null)"
            line = (
                f"    {i}. `order_item_id`={r.get('order_item_id')} · `order_id`={r.get('order_id')} · "
                f"qty={r.get('quantity')} · total_price={r.get('total_price')} · {pet_s} · "
                f"name_raw={_md_escape_cell(r.get('name_raw'))}"
            )
        else:
            line = (
                f"    {i}. `order_item_addon_id`={r.get('order_item_addon_id')} · "
                f"`order_item_id`={r.get('order_item_id')} · qty={r.get('quantity')} · "
                f"price={r.get('price')} · petpooja_addonid={_md_escape_cell(r.get('petpooja_addonid'))} · "
                f"name_raw={_md_escape_cell(r.get('name_raw'))}"
            )
        lines.append(line)


def _write_typed_cluster_markdown(
    path: Path,
    cluster_type: str,
    category_summary_rows: List[Dict[str, Any]],
    category_membership_rows: List[Dict[str, Any]],
    category_merge_rows: List[Dict[str, Any]],
    mapping_line_details: Dict[Tuple[str, str, str], Dict[str, Any]],
    generated_at: str,
) -> None:
    members_by_cluster: Dict[Tuple[str, str], List[Dict[str, Any]]] = defaultdict(list)
    for row in category_membership_rows:
        members_by_cluster[(row["menu_item_id"], row["variant_id"])].append(row)

    parents: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for s in category_summary_rows:
        parents[s["menu_item_id"]].append(s)

    lines = [
        f"# {cluster_type} cluster review",
        "",
        f"Generated at: {generated_at}",
        "",
        "Hierarchy legend (refs use the same numbers/letters):",
        "",
        "- **1)** Parent cluster name, type",
        "  - **a)** Variant (child cluster) name",
        "    - **1)** Raw name(s) on order rows for that `menu_item_variants` mapping",
        "",
        "Cross-reference key: **`1-a-1`** = parent **1)**, variant **a)**, raw row **1)**.",
        "",
        f"_Category filter: `menu_items.type` / `parent_cluster_type` = **{cluster_type}**._",
        "",
        "---",
        "",
    ]

    leaf_index_rows: List[Dict[str, Any]] = []

    p_num = 0
    for menu_item_id in sorted(parents.keys(), key=lambda mid: parents[mid][0]["parent_cluster_name"].lower()):
        p_num += 1
        summaries = sorted(
            parents[menu_item_id],
            key=lambda s: s["child_cluster_name"].lower(),
        )
        first = summaries[0]
        lines.append(f"## {p_num}) {first['parent_cluster_name']}, {first['parent_cluster_type']}")
        lines.append("")

        for v_idx, variant_summary in enumerate(summaries):
            v_letter = _variant_letter(v_idx)
            key = (variant_summary["menu_item_id"], variant_summary["variant_id"])
            members = members_by_cluster.get(key, [])
            members_sorted = sorted(
                members,
                key=lambda m: (m["raw_names"].lower(), m["source_item_id"]),
            )

            lines.append(f"### {v_letter}) {variant_summary['child_cluster_name']}")
            lines.append("")
            lines.append(
                f"- Stats: {variant_summary['mapping_count']} mappings "
                f"({variant_summary['verified_mapping_count']} verified); "
                f"{variant_summary['item_row_count']} item rows, "
                f"{variant_summary['addon_row_count']} addon rows."
            )
            lines.append("")

            for raw_idx, member in enumerate(members_sorted, start=1):
                ref = f"{p_num}-{v_letter}-{raw_idx}"
                names = _split_raw_names(member["raw_names"])
                if len(names) == 1:
                    lines.append(f"- **{raw_idx})** {names[0]}")
                elif len(names) > 1:
                    # One numbered entry; join distinct raw labels (e.g. item + addon names).
                    lines.append(f"- **{raw_idx})** {' · '.join(names)}")
                else:
                    lines.append(f"- **{raw_idx})**")
                lines.append(
                    f"  - Source kind: {member['source_item_kind']}; "
                    f"{member['item_row_count']} item rows, "
                    f"{member['addon_row_count']} addon rows."
                )
                detail_key = (
                    member["source_item_id"],
                    member["menu_item_id"],
                    member["variant_id"] or "",
                )
                details = mapping_line_details.get(
                    detail_key,
                    {
                        "order_items": [],
                        "order_item_addons": [],
                        "relaxed_items": False,
                        "relaxed_addons": False,
                    },
                )
                item_lines = details["order_items"]
                addon_lines = details["order_item_addons"]
                _append_line_list_markdown(lines, "order_items rows", item_lines, kind="item")
                _append_line_list_markdown(
                    lines, "order_item_addons rows", addon_lines, kind="addon"
                )
                if details.get("relaxed_items"):
                    lines.append(
                        "  - _`order_items` listed by PetPooja/internal id + variant; "
                        "`menu_item_id` on rows may differ from this mapping's catalog id._"
                    )
                if details.get("relaxed_addons"):
                    lines.append(
                        "  - _`order_item_addons` listed the same way (often parent line catalog, "
                        "e.g. Ice Cream vs Dessert)._"
                    )
                if member["item_row_count"] and not item_lines:
                    lines.append(
                        "  - _(No `order_items` rows matched strict or source+variant filters; "
                        "counts may come from a broader source-id fallback.)_"
                    )
                if member["addon_row_count"] and not addon_lines:
                    lines.append(
                        "  - _(No `order_item_addons` rows matched strict or source+variant filters; "
                        "counts may come from a broader source-id fallback.)_"
                    )
                lines.append("")

                oid_list = [str(r["order_item_id"]) for r in item_lines]
                aid_list = [str(r["order_item_addon_id"]) for r in addon_lines]
                leaf_index_rows.append(
                    {
                        "hierarchy_ref": ref,
                        "menu_item_id": member["menu_item_id"],
                        "variant_id": member["variant_id"] or "",
                        "child_cluster_name": member["child_cluster_name"],
                        "source_item_id": member["source_item_id"],
                        "source_item_kind": member["source_item_kind"],
                        "raw_names": member["raw_names"],
                        "mapping_is_verified": member["mapping_is_verified"],
                        "item_row_count": member["item_row_count"],
                        "addon_row_count": member["addon_row_count"],
                        "order_item_ids": _compact_id_csv(oid_list),
                        "order_item_addon_ids": _compact_id_csv(aid_list),
                    }
                )

            lines.append("")

    lines.extend(
        [
            "---",
            "",
            "## ID index (trace hierarchy → database)",
            "",
            "Use **hierarchy_ref** to match sections above (`1-a-1` = parent **1)**, variant **a)**, raw row **1)**).",
            "",
            "When there are many lines, **order_item_ids** in this table may be truncated; the numbered **order_items rows** list under each mapping is complete.",
            "",
        ]
    )

    if leaf_index_rows:
        fieldnames = list(leaf_index_rows[0].keys())
        lines.append("| " + " | ".join(fieldnames) + " |")
        lines.append("| " + " | ".join("---" for _ in fieldnames) + " |")
        for row in leaf_index_rows:
            cells = []
            for k in fieldnames:
                v = row[k]
                if isinstance(v, bool):
                    cells.append("✅" if v else "❌")
                else:
                    cells.append(str(v).replace("|", "\\|"))
            lines.append("| " + " | ".join(cells) + " |")
    else:
        lines.append(f"_No {cluster_type.lower()} mappings found._")

    merge_heading = f"## Merge history ({cluster_type}-related)"
    lines.extend(["", "---", "", merge_heading, ""])

    if category_merge_rows:
        lines.append(
            "Events whose **source**, **target at event**, or **resolved current target** "
            f"is one of the `{cluster_type}` `menu_item_id` values in this export."
        )
        lines.append("")
        lines.append(
            "| merge_id | merged_at | source_id | source_name | target_id_at_event | "
            "target_name_at_event | current_target_id | current_target_name |"
        )
        lines.append("| --- | --- | --- | --- | --- | --- | --- | --- |")
        for row in category_merge_rows:
            lines.append(
                "| "
                + " | ".join(
                    [
                        str(row["merge_id"]),
                        str(row["merged_at"]),
                        str(row["source_id"]),
                        str(row["source_name"]).replace("|", "\\|"),
                        str(row["target_id_at_event"]),
                        str(row["target_name_at_event"]).replace("|", "\\|"),
                        str(row["current_target_id"]),
                        str(row["current_target_name"]).replace("|", "\\|"),
                    ]
                )
                + " |"
            )
    else:
        lines.append(f"_No merge events matched these {cluster_type.lower()} clusters._")

    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def export_typed_cluster_review(cluster_type: str, output_dir: Path) -> Dict[str, Path]:
    """
    Export cluster review for rows where parent_cluster_type == cluster_type
    (matches menu_items.type on the canonical parent).
    """
    conn, message = get_db_connection()
    if conn is None:
        raise RuntimeError(f"Could not connect to database: {message}")

    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S %Z")
    output_dir.mkdir(parents=True, exist_ok=True)

    try:
        menu_lookup = ecr._load_menu_lookup(conn)
        variant_lookup = ecr._load_variant_lookup(conn)
        source_contexts = ecr._load_source_contexts(conn)
        history_rows, lineage_by_final_target = ecr._load_merge_history_rows(
            conn,
            menu_lookup,
            variant_lookup,
            source_contexts,
        )
        membership_rows, cluster_summary_rows = ecr._load_current_cluster_rows(
            conn,
            menu_lookup,
            variant_lookup,
            source_contexts,
            lineage_by_final_target,
        )
        category_membership = [
            r for r in membership_rows if r.get("parent_cluster_type") == cluster_type
        ]
        mapping_line_details = _build_mapping_line_details(conn, category_membership)
    finally:
        conn.close()

    category_summary = [r for r in cluster_summary_rows if r.get("parent_cluster_type") == cluster_type]
    category_menu_ids = {r["menu_item_id"] for r in category_summary}
    category_merge = _load_category_merge_rows(history_rows, category_menu_ids)

    stem = _type_stem(cluster_type)
    clusters_md = output_dir / f"{stem}_clusters.md"
    summary_csv = output_dir / f"{stem}_cluster_summary.csv"
    members_csv = output_dir / f"{stem}_cluster_members.csv"
    merge_csv = output_dir / f"{stem}_merge_history.csv"

    _write_typed_cluster_markdown(
        clusters_md,
        cluster_type,
        category_summary,
        category_membership,
        category_merge,
        mapping_line_details,
        generated_at,
    )
    ecr._write_csv(summary_csv, category_summary)
    ecr._write_csv(members_csv, category_membership)
    ecr._write_csv(merge_csv, category_merge)

    # Stable return keys for Combo (existing callers / muscle memory).
    if cluster_type == "Combo":
        return {
            "combo_cluster_md": clusters_md,
            "combo_cluster_summary_csv": summary_csv,
            "combo_cluster_members_csv": members_csv,
            "combo_merge_history_csv": merge_csv,
        }
    return {
        f"{stem}_cluster_md": clusters_md,
        f"{stem}_cluster_summary_csv": summary_csv,
        f"{stem}_cluster_members_csv": members_csv,
        f"{stem}_merge_history_csv": merge_csv,
    }


def export_combo_cluster_review(output_dir: Path) -> Dict[str, Path]:
    """Backward-compatible wrapper: same as export_typed_cluster_review(\"Combo\", ...)."""
    return export_typed_cluster_review("Combo", output_dir)


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Export cluster review for one menu_items.type (Combo, Drinks, Service, Extra, Dessert, "
            "Ice Cream, …) from analytics.db. Default output: "
            f"{ecr.MENU_STATE_EVALUATION_ROOT}/<slug>_cluster_review/ "
            f"(e.g. {ecr.MENU_STATE_EVALUATION_ROOT}/extra_cluster_review/)."
        ),
    )
    parser.add_argument(
        "--type",
        dest="cluster_type",
        default="Combo",
        metavar="TYPE",
        help=(
            "menu_items.type / parent_cluster_type (default: Combo). Examples: --type Drinks, "
            "--type Service, --type Extra, --type Dessert, --type \"Ice Cream\" or --type ice_cream "
            f"(writes ice_cream_clusters.md under {ecr.MENU_STATE_EVALUATION_ROOT}/ice_cream_cluster_review/ by default)."
        ),
    )
    parser.add_argument(
        "--output-dir",
        default=None,
        help=(
            "Output directory "
            f"(default: {ecr.MENU_STATE_EVALUATION_ROOT}/<type>_cluster_review, e.g. "
            f"{ecr.MENU_STATE_EVALUATION_ROOT}/combo_cluster_review; DB: {DB_PATH})"
        ),
    )
    args = parser.parse_args()
    cluster_type = _resolve_cluster_type_arg(args.cluster_type)
    if not cluster_type:
        parser.error("--type must be non-empty")

    out = Path(args.output_dir) if args.output_dir else Path(_default_output_dir(cluster_type))

    outputs = export_typed_cluster_review(cluster_type, out)
    for label, path in outputs.items():
        print(f"{label}: {path}")


if __name__ == "__main__":
    main()
