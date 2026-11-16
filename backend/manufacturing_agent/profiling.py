from __future__ import annotations

import hashlib
import json
from typing import Any

from manufacturing.io_loader import load_records_from_bytes
from manufacturing_agent.schemas import DataProfile, FieldProfile


def profile_records(
    records: list[dict[str, Any]],
    *,
    filename: str,
    equipment_description: str = "",
    process_description: str = "",
    raw_bytes: bytes | None = None,
) -> DataProfile:
    if not records:
        raise ValueError("no records to profile")

    raw = raw_bytes or json.dumps(records, ensure_ascii=False, sort_keys=True).encode("utf-8")
    raw_hash = hashlib.sha256(raw).hexdigest()

    keys: list[str] = []
    seen: set[str] = set()
    for row in records:
        for key in row.keys():
            if key not in seen:
                seen.add(key)
                keys.append(str(key))

    fields: list[FieldProfile] = []
    for key in keys:
        values = [row.get(key) for row in records]
        null_count = sum(1 for value in values if value is None or value == "")
        non_null = [value for value in values if value is not None and value != ""]
        sample_values = non_null[:5]
        distinct = {json.dumps(value, sort_keys=True, default=str) for value in non_null}
        fields.append(
            FieldProfile(
                field_name=key,
                inferred_type=_infer_type(non_null),
                sample_values=sample_values,
                missing_ratio=round(null_count / len(records), 4),
                null_count=null_count,
                distinct_count=len(distinct),
            )
        )

    return DataProfile(
        filename=filename,
        record_count=len(records),
        fields=fields,
        equipment_description=equipment_description,
        process_description=process_description,
        raw_hash=raw_hash,
        sample_records=records[:5],
    )


def profile_upload(
    raw: bytes,
    *,
    filename: str,
    equipment_description: str = "",
    process_description: str = "",
) -> tuple[DataProfile, list[dict[str, Any]]]:
    records = load_records_from_bytes(raw, filename=filename)
    profile = profile_records(
        records,
        filename=filename,
        equipment_description=equipment_description,
        process_description=process_description,
        raw_bytes=raw,
    )
    return profile, records


def _infer_type(values: list[Any]) -> str:
    if not values:
        return "unknown"
    if all(isinstance(value, bool) for value in values):
        return "boolean"
    if all(isinstance(value, int) and not isinstance(value, bool) for value in values):
        return "integer"
    if all(isinstance(value, (int, float)) and not isinstance(value, bool) for value in values):
        return "number"
    if all(isinstance(value, str) for value in values):
        return "string"
    return "mixed"
