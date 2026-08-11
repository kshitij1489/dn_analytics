"""Explicit All Stores reducers.

Every combined field is declared. There is deliberately no recursive
"sum every numeric field" helper: that would corrupt IDs, rates, averages,
probabilities, timestamps, pagination totals, and flags (plan §8.3).

Rules
-----
* `Sum`      — true counts and money add up.
* `Ratio`    — recomputed from combined numerator/denominator, never averaged.
* `Min`/`Max`— envelope-style bounds.
* `First`    — echoed request parameters that every store shares.
* `AllTrue` / `AnyTrue` — flags combined with an explicit boolean rule.
* `Ignore`   — dropped because it has no cross-store meaning.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

from src.core.profiles import RestaurantProfile

Pairs = Sequence[Tuple[RestaurantProfile, Any]]


# --------------------------------------------------------------------------- rules


@dataclass(frozen=True)
class Sum:
    field: Optional[str] = None


@dataclass(frozen=True)
class Ratio:
    """Recompute `scale * numerator / denominator` from combined atoms."""

    numerator: str
    denominator: str
    scale: float = 1.0
    digits: Optional[int] = 2


@dataclass(frozen=True)
class Min:
    field: Optional[str] = None


@dataclass(frozen=True)
class Max:
    field: Optional[str] = None


@dataclass(frozen=True)
class First:
    field: Optional[str] = None


@dataclass(frozen=True)
class AllTrue:
    field: Optional[str] = None


@dataclass(frozen=True)
class AnyTrue:
    field: Optional[str] = None


@dataclass(frozen=True)
class Ignore:
    pass


Rule = Any


# ----------------------------------------------------------------------- helpers


def as_number(value: Any) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, Decimal):
        return float(value)
    return None


def _sum_field(rows: Iterable[Dict[str, Any]], field: str) -> Optional[float]:
    total = 0.0
    seen = False
    for row in rows:
        number = as_number(row.get(field))
        if number is None:
            continue
        total += number
        seen = True
    return total if seen else None


def _preserve_int(values: Iterable[Any], total: Optional[float]) -> Any:
    """Keep integer counts integral instead of turning them into floats."""
    if total is None:
        return None
    numbers = [value for value in values if isinstance(value, int) and not isinstance(value, bool)]
    if numbers and float(total).is_integer():
        return int(total)
    return total


def round_to(value: Optional[float], digits: Optional[int]) -> Optional[float]:
    if value is None or digits is None:
        return value
    return float(round(value, digits))


def sort_key(value: Any) -> Tuple[int, float, str]:
    """Type-stable ordering key: missing values, then numbers, then text.

    NULL sorts below everything so a federated page orders missing values the
    way the per-store SQL already did — SQLite treats NULL as smallest, putting
    it first ascending and last descending.
    """
    if value is None:
        return (-1, 0.0, "")
    number = as_number(value)
    if number is not None:
        return (0, number, "")
    return (1, 0.0, str(value))


def _extreme(values: Sequence[Any], *, largest: bool) -> Any:
    """Smallest/largest present value, comparable across numbers and text.

    Dates and other text are ordered by `sort_key` instead of being dropped:
    `Max()` on a business-date field must return the furthest-ahead date, not
    `None`.
    """
    if not values:
        return None
    chosen = (max if largest else min)(values, key=sort_key)
    number = as_number(chosen)
    if number is None:
        return chosen
    return _preserve_int(values, number)


# ------------------------------------------------------------------ dict reducer


def reduce_mapping(
    mappings: Sequence[Dict[str, Any]],
    spec: Dict[str, Rule],
    *,
    default: Rule = Ignore(),
) -> Dict[str, Any]:
    """Combine dicts using declared rules; unspecified fields are discarded."""
    rows = [dict(mapping or {}) for mapping in mappings]
    if not rows:
        return {}

    keys: List[str] = []
    for row in rows:
        for key in row:
            if key not in keys:
                keys.append(key)
    for key in spec:
        if key not in keys and not isinstance(spec[key], Ignore):
            keys.append(key)

    combined: Dict[str, Any] = {}
    deferred: List[Tuple[str, Ratio]] = []

    for key in keys:
        rule = spec.get(key, default)
        values = [row.get(key) for row in rows if key in row]
        if isinstance(rule, Ignore):
            continue
        if isinstance(rule, Sum):
            source = rule.field or key
            combined[key] = _preserve_int(
                [row.get(source) for row in rows], _sum_field(rows, source)
            )
        elif isinstance(rule, Ratio):
            deferred.append((key, rule))
        elif isinstance(rule, (Min, Max)):
            source = getattr(rule, "field", None) or key
            present = [
                row.get(source)
                for row in rows
                if source in row and row.get(source) is not None
            ]
            combined[key] = _extreme(present, largest=isinstance(rule, Max))
        elif isinstance(rule, AllTrue):
            combined[key] = all(bool(value) for value in values) if values else False
        elif isinstance(rule, AnyTrue):
            combined[key] = any(bool(value) for value in values) if values else False
        else:  # First
            source = getattr(rule, "field", None) or key
            combined[key] = next(
                (row.get(source) for row in rows if row.get(source) is not None), None
            )

    for key, rule in deferred:
        numerator = combined.get(rule.numerator)
        if numerator is None:
            numerator = _sum_field(rows, rule.numerator)
        denominator = combined.get(rule.denominator)
        if denominator is None:
            denominator = _sum_field(rows, rule.denominator)
        numerator_value = as_number(numerator) or 0.0
        denominator_value = as_number(denominator) or 0.0
        combined[key] = (
            round_to(rule.scale * numerator_value / denominator_value, rule.digits)
            if denominator_value
            else 0.0
        )
    return combined


# ------------------------------------------------------------------ row reducers


def attribute_rows(pairs: Pairs, rows_of: Callable[[Any], Sequence[Dict[str, Any]]] = lambda value: value) -> List[Dict[str, Any]]:
    """Tag every row with its restaurant before any cross-store combination."""
    attributed: List[Dict[str, Any]] = []
    for profile, value in pairs:
        for row in rows_of(value) or []:
            enriched = dict(row)
            # `orders.restaurant_id` is a local FK, not the central restaurant
            # ID. Keep the local value instead of silently overwriting it.
            local_value = enriched.get("restaurant_id")
            if local_value is not None and str(local_value) != profile.restaurant_id:
                enriched.setdefault("local_restaurant_id", local_value)
            enriched["restaurant_id"] = profile.restaurant_id
            enriched["restaurant_name"] = profile.display_name
            attributed.append(enriched)
    return attributed


def profile_row_key(profile_id: str, row: Dict[str, Any], key_fields: Sequence[str]) -> str:
    """`<restaurant_id>:<local key>` — local IDs are not globally unique.

    `restaurants.restaurant_id` is itself a local key that attribution moved to
    `local_restaurant_id`; prefer that so two of a store's rows never share a key.
    """
    parts = []
    for field in key_fields:
        value = row.get(f"local_{field}", row.get(field))
        if value is not None:
            parts.append(str(value))
    return f"{profile_id}:{'|'.join(parts)}" if parts else profile_id


def union_rows(
    pairs: Pairs,
    *,
    key_fields: Sequence[str],
    sort_by: Optional[str] = None,
    descending: bool = True,
    limit: Optional[int] = None,
    rows_of: Callable[[Any], Sequence[Dict[str, Any]]] = lambda value: value,
) -> List[Dict[str, Any]]:
    """Sorted union of per-store rows, each carrying its restaurant identity."""
    rows = attribute_rows(pairs, rows_of)
    for row in rows:
        row["row_key"] = profile_row_key(str(row["restaurant_id"]), row, key_fields)
    if sort_by:
        sort_attributed_rows(rows, sort_by, descending)
    if limit is not None:
        return rows[:limit]
    return rows


def sort_attributed_rows(rows: List[Dict[str, Any]], sort_by: str, descending: bool) -> None:
    """Stable k-way merge order with a fully deterministic local-key tie.

    Stable sorts are applied least-significant first: local row key, restaurant
    ID, then the requested value. Restaurant and local-key ties stay ascending
    even when the requested field is descending.
    """
    rows.sort(key=lambda row: str(row.get("row_key")))
    rows.sort(key=lambda row: str(row.get("restaurant_id")))
    rows.sort(key=lambda row: sort_key(row.get(sort_by)), reverse=descending)


def group_rows(
    pairs: Pairs,
    *,
    group_by: Sequence[str],
    spec: Dict[str, Rule],
    sort_by: Optional[str] = None,
    descending: bool = True,
    limit: Optional[int] = None,
    contributor_fields: Sequence[str] = (),
    rows_of: Callable[[Any], Sequence[Dict[str, Any]]] = lambda value: value,
    default: Rule = Ignore(),
) -> List[Dict[str, Any]]:
    """Group rows across stores by durable dimensions, then apply declared rules.

    `contributor_fields` keeps the per-store identity of each grouped row so a
    combined menu/customer row can still be drilled down to `{restaurant_id,
    menu_item_id, variant_id}` (plan §7.3).
    """
    buckets: Dict[Tuple[Any, ...], List[Dict[str, Any]]] = {}
    order: List[Tuple[Any, ...]] = []
    for profile, value in pairs:
        for row in rows_of(value) or []:
            key = tuple(row.get(field) for field in group_by)
            if key not in buckets:
                buckets[key] = []
                order.append(key)
            buckets[key].append({**row, "__restaurant__": profile})

    combined_rows: List[Dict[str, Any]] = []
    for key in order:
        members = buckets[key]
        stripped = [{k: v for k, v in row.items() if k != "__restaurant__"} for row in members]
        combined = reduce_mapping(stripped, spec, default=default)
        for index, field_name in enumerate(group_by):
            combined[field_name] = key[index]
        if contributor_fields:
            combined["contributors"] = [
                {
                    "restaurant_id": row["__restaurant__"].restaurant_id,
                    "restaurant_name": row["__restaurant__"].display_name,
                    **{field: row.get(field) for field in contributor_fields},
                }
                for row in members
            ]
            combined["restaurant_ids"] = sorted(
                {row["__restaurant__"].restaurant_id for row in members}
            )
        combined_rows.append(combined)

    if sort_by:
        combined_rows.sort(
            key=lambda row: (sort_key(row.get(sort_by)), str(row.get(group_by[0]))),
            reverse=descending,
        )
    if limit is not None:
        return combined_rows[:limit]
    return combined_rows


def menu_identity_payload(
    conn,
    rows: Sequence[Dict[str, Any]],
    *,
    item_field: Optional[str],
    variant_field: Optional[str] = None,
    variant_only: bool = False,
) -> Dict[str, Any]:
    """Attach dormant global identity plus the profile's coverage gate."""
    try:
        from src.core.global_menu_identity import annotate_global_identity_rows
        from src.core.global_menu_schema import resolve_global_menu_capability

        status = resolve_global_menu_capability(conn, allow_federated_read=True)
        if variant_only:
            annotated = []
            from src.core.global_menu_identity import global_ids_for_local

            for raw in rows:
                row = dict(raw)
                _item_id, variant_id = global_ids_for_local(
                    conn, "", str(row.get(variant_field or "") or "")
                )
                row["global_variant_id"] = variant_id
                row["identity_unlinked"] = bool(status.active and not variant_id)
                if variant_id:
                    variant = conn.execute(
                        """
                        SELECT canonical_name, unit, value FROM global_variants
                        WHERE global_variant_id=? AND lifecycle_state='active'
                        """,
                        (variant_id,),
                    ).fetchone()
                    if variant:
                        row.update(
                            {
                                "canonical_variant_name": variant[0],
                                "canonical_variant_unit": variant[1],
                                "canonical_variant_value": variant[2],
                            }
                        )
                annotated.append(row)
        else:
            annotated = annotate_global_identity_rows(
                conn,
                rows,
                item_field=str(item_field),
                variant_field=variant_field,
                allow_federated_read=True,
            )
        return {"rows": annotated, "identity": status.to_dict()}
    except Exception:
        # A profile not yet upgraded/activated remains byte-compatible with the
        # legacy name-based reducer. It is not falsely reported as global-ready.
        return {
            "rows": [dict(row) for row in rows],
            "identity": {
                "active": False,
                "aggregation_ready": False,
                "coverage_linked": 0,
                "coverage_total": 0,
                "quarantine_count": 0,
            },
        }


def group_menu_identity_rows(
    pairs: Pairs,
    *,
    legacy_group_by: Sequence[str],
    spec: Dict[str, Rule],
    item_id_field: Optional[str],
    variant_id_field: Optional[str] = None,
    canonical_item_name_field: Optional[str] = None,
    canonical_item_type_field: Optional[str] = None,
    canonical_variant_name_field: Optional[str] = None,
    identity_kind: str = "item",
    additional_group_by: Sequence[str] = (),
    sort_by: Optional[str] = None,
    descending: bool = True,
    limit: Optional[int] = None,
    contributor_fields: Sequence[str] = (),
    default: Rule = Ignore(),
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Use global IDs only when every included profile passes coverage gates.

    Unlinked rows are never normalized/name-grouped in global mode; their key
    includes restaurant plus local item/variant identity and they remain
    visibly marked ``identity_unlinked``.
    """
    statuses = [
        value.get("identity") or {}
        for _profile, value in pairs
        if isinstance(value, dict)
    ]
    globally_ready = bool(statuses) and len(statuses) == len(pairs) and all(
        bool(status.get("aggregation_ready")) for status in statuses
    )
    coverage = {
        "global_mode_active": any(bool(status.get("active")) for status in statuses),
        "global_aggregation_active": globally_ready,
        "linked": sum(int(status.get("coverage_linked") or 0) for status in statuses),
        "total": sum(int(status.get("coverage_total") or 0) for status in statuses),
        "quarantine_count": sum(
            int(status.get("quarantine_count") or 0) for status in statuses
        ),
    }
    if not globally_ready:
        return (
            group_rows(
                pairs,
                group_by=legacy_group_by,
                spec=spec,
                sort_by=sort_by,
                descending=descending,
                limit=limit,
                contributor_fields=contributor_fields,
                rows_of=lambda value: value.get("rows", []) if isinstance(value, dict) else value,
                default=default,
            ),
            coverage,
        )

    prepared_pairs = []
    total_source_rows = linked_source_rows = 0
    for profile, value in pairs:
        prepared = []
        for raw in value.get("rows", []):
            total_source_rows += 1
            row = dict(raw)
            global_item_id = row.get("global_menu_item_id")
            global_variant_id = row.get("global_variant_id") if (
                variant_id_field or identity_kind == "variant"
            ) else None
            local_variant_present = bool(
                variant_id_field and row.get(variant_id_field) not in (None, "")
            )
            linked = (
                bool(global_variant_id)
                if identity_kind == "variant"
                else bool(global_item_id) and (
                    not local_variant_present or bool(global_variant_id)
                )
            )
            if linked:
                linked_source_rows += 1
                identity_key = (
                    f"global-variant:{global_variant_id}"
                    if identity_kind == "variant"
                    else f"global:{global_item_id}"
                )
                if variant_id_field and identity_kind != "variant":
                    identity_key += f":{global_variant_id}"
                row["identity_unlinked"] = False
                if canonical_item_name_field and row.get("canonical_name"):
                    row[canonical_item_name_field] = row["canonical_name"]
                if canonical_item_type_field and row.get("canonical_type"):
                    row[canonical_item_type_field] = row["canonical_type"]
                if canonical_variant_name_field and row.get("canonical_variant_name"):
                    row[canonical_variant_name_field] = row["canonical_variant_name"]
            else:
                local_item = row.get(item_id_field) if item_id_field else ""
                local_variant = row.get(variant_id_field) if variant_id_field else ""
                identity_key = (
                    f"legacy:{profile.restaurant_id}:{local_item or 'missing'}:"
                    f"{local_variant or ''}"
                )
                row["identity_unlinked"] = True
            row["__global_identity_key"] = identity_key
            prepared.append(row)
        prepared_pairs.append((profile, prepared))

    identity_spec = dict(spec)
    identity_spec.update(
        {
            "global_menu_item_id": First(),
            "global_variant_id": First(),
            "identity_unlinked": AllTrue(),
        }
    )
    for field in (
        canonical_item_name_field,
        canonical_item_type_field,
        canonical_variant_name_field,
    ):
        if field:
            identity_spec[field] = First()
    rows = group_rows(
        prepared_pairs,
        group_by=("__global_identity_key", *additional_group_by),
        spec=identity_spec,
        sort_by=sort_by,
        descending=descending,
        limit=limit,
        contributor_fields=contributor_fields,
        default=default,
    )
    for row in rows:
        row.pop("__global_identity_key", None)
        row["identity_coverage_linked"] = linked_source_rows
        row["identity_coverage_total"] = total_source_rows
    coverage.update({"linked_rows": linked_source_rows, "total_rows": total_source_rows})
    return rows, coverage


def identity_aware_federated_data(data: Any, coverage: Dict[str, Any]) -> Dict[str, Any]:
    """Marker consumed by ``build_envelope`` without changing page data shape."""
    return {
        "__identity_aware_federated_data__": True,
        "data": data,
        "identity_coverage": coverage,
    }
