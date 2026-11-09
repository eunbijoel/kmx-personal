from __future__ import annotations

from typing import Any

from manufacturing.normalize import build_field_defs
from manufacturing.types import QualityFlag, ValidationIssue


def evaluate_common_quality(
    record: dict[str, Any],
    *,
    field_defs: dict[str, dict[str, Any]] | None = None,
) -> list[ValidationIssue]:
    """Apply field_dictionary value_range / type checks (common manufacturing rules)."""
    defs = field_defs if field_defs is not None else build_field_defs()
    issues: list[ValidationIssue] = []

    for field, value in record.items():
        if value is None or value == "":
            issues.append(
                ValidationIssue(
                    field=field,
                    message=f"missing value for '{field}'",
                    severity=QualityFlag.WARNING,
                    rule="common.missing",
                )
            )
            continue

        field_def = defs.get(field)
        if not field_def:
            continue

        expected_type = str(field_def.get("type") or "").lower()
        if expected_type in {"number", "float", "integer", "int"}:
            numeric = _as_number(value)
            if numeric is None:
                issues.append(
                    ValidationIssue(
                        field=field,
                        message=f"expected numeric value, got {type(value).__name__}",
                        severity=QualityFlag.INVALID,
                        rule="common.type",
                    )
                )
                continue
            if expected_type in {"integer", "int"} and not float(numeric).is_integer():
                issues.append(
                    ValidationIssue(
                        field=field,
                        message=f"expected integer, got {numeric}",
                        severity=QualityFlag.WARNING,
                        rule="common.type",
                    )
                )
            value_range = field_def.get("value_range") or {}
            min_v = value_range.get("min")
            max_v = value_range.get("max")
            if min_v is not None and numeric < float(min_v):
                issues.append(
                    ValidationIssue(
                        field=field,
                        message=f"{numeric} below min {min_v}",
                        severity=QualityFlag.CRITICAL,
                        rule="common.range",
                    )
                )
            if max_v is not None and numeric > float(max_v):
                issues.append(
                    ValidationIssue(
                        field=field,
                        message=f"{numeric} above max {max_v}",
                        severity=QualityFlag.CRITICAL,
                        rule="common.range",
                    )
                )
        elif expected_type in {"string", "str"} and not isinstance(value, str):
            issues.append(
                ValidationIssue(
                    field=field,
                    message=f"expected string, got {type(value).__name__}",
                    severity=QualityFlag.WARNING,
                    rule="common.type",
                )
            )

    return issues


def summarize_dataset_quality(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Lightweight dataset-level checks (missing ratio, simple IQR outliers)."""
    if not records:
        return {"record_count": 0, "missing_ratio": {}, "outlier_fields": []}

    keys = sorted({key for row in records for key in row.keys()})
    missing_ratio: dict[str, float] = {}
    outlier_fields: list[dict[str, Any]] = []

    for key in keys:
        values = [row.get(key) for row in records]
        missing = sum(1 for value in values if value is None or value == "")
        missing_ratio[key] = round(missing / len(records), 4)

        numeric_values = [_as_number(value) for value in values]
        numeric_values = [value for value in numeric_values if value is not None]
        if len(numeric_values) < 4:
            continue
        q1, q3 = _quartiles(sorted(numeric_values))
        iqr = q3 - q1
        if iqr <= 0:
            continue
        low = q1 - 1.5 * iqr
        high = q3 + 1.5 * iqr
        outliers = [value for value in numeric_values if value < low or value > high]
        if outliers:
            outlier_fields.append(
                {
                    "field": key,
                    "outlier_count": len(outliers),
                    "bounds": {"low": low, "high": high},
                    "rule": "common.iqr",
                }
            )

    return {
        "record_count": len(records),
        "missing_ratio": missing_ratio,
        "outlier_fields": outlier_fields,
    }


def _as_number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            return None
    return None


def _quartiles(sorted_values: list[float]) -> tuple[float, float]:
    n = len(sorted_values)

    def _at(p: float) -> float:
        idx = p * (n - 1)
        lo = int(idx)
        hi = min(lo + 1, n - 1)
        frac = idx - lo
        return sorted_values[lo] * (1 - frac) + sorted_values[hi] * frac

    return _at(0.25), _at(0.75)
