import re
from typing import Any, Dict, Optional


_NAME_OVERRIDES = {
    "DUO_200ML_200ML": {"unit": "ML", "value": 400},
    "FAMILY": {"unit": "COUNT", "value": 1},
    "SINGLE": {"unit": "COUNT", "value": 1},
}


def _normalize_unit(unit: Any) -> Optional[str]:
    if unit is None:
        return None
    normalized = str(unit).strip().upper()
    if not normalized:
        return None
    if normalized == "KG":
        return "GMS"
    return normalized


def _normalize_value(value: Any) -> Optional[float]:
    if value in (None, ""):
        return None
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    if numeric.is_integer():
        return int(numeric)
    return numeric


def _canonicalize_variant_name(variant_name: str) -> str:
    normalized = re.sub(r"[_-]+", " ", str(variant_name or "").upper())
    return re.sub(r"\s+", " ", normalized).strip()


def _normalize_measurement(unit: str, amount: int) -> Dict[str, Any]:
    normalized_unit = _normalize_unit(unit)
    normalized_amount = amount * 1000 if normalized_unit == "GMS" and unit == "KG" else amount
    return {"unit": normalized_unit, "value": normalized_amount}


def _infer_from_name(variant_name: Optional[str]) -> Dict[str, Any]:
    if not variant_name:
        return {"unit": None, "value": None}

    raw_name = str(variant_name or "").strip().upper()
    override = _NAME_OVERRIDES.get(raw_name)
    if override:
        return dict(override)

    name = _canonicalize_variant_name(variant_name)

    multiplied = re.findall(r"(\d+)\s*X\s*(\d+)\s*(ML|GMS|KG)\b", name)
    if multiplied:
        units = {_normalize_unit(unit) for _, _, unit in multiplied}
        if len(units) == 1:
            total = 0
            for count, amount, unit in multiplied:
                base_amount = int(amount)
                if unit == "KG":
                    base_amount *= 1000
                total += int(count) * base_amount
            return {"unit": units.pop(), "value": total}

    repeated_measurements = re.findall(r"(\d+)\s*(ML|GMS|KG)\b", name)
    if len(repeated_measurements) > 1:
        units = {_normalize_unit(unit) for _, unit in repeated_measurements}
        if len(units) == 1:
            total = 0
            for amount, unit in repeated_measurements:
                base_amount = int(amount)
                if unit == "KG":
                    base_amount *= 1000
                total += base_amount
            return {"unit": units.pop(), "value": total}

    single_measurement = re.search(r"(\d+)\s*(ML|GMS|KG)\b", name)
    if single_measurement:
        amount = int(single_measurement.group(1))
        unit = single_measurement.group(2)
        return _normalize_measurement(unit, amount)

    count_measurement = re.search(r"(\d+)\s*(PIECES?|PCS|COUNT)\b", name)
    if count_measurement:
        return {"unit": "COUNT", "value": int(count_measurement.group(1))}

    return {"unit": None, "value": None}


def infer_variant_metadata(
    variant_name: Optional[str],
    snapshot_meta: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    snapshot_meta = snapshot_meta if isinstance(snapshot_meta, dict) else {}
    metadata = {
        "unit": _normalize_unit(snapshot_meta.get("unit")),
        "value": _normalize_value(snapshot_meta.get("value")),
    }

    fallback = _infer_from_name(variant_name)
    if metadata["unit"] is None:
        metadata["unit"] = fallback["unit"]
    if metadata["value"] is None:
        metadata["value"] = fallback["value"]

    return metadata
