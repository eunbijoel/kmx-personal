from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parents[2]
FIELD_DICTIONARY_PATH = ROOT_DIR / "data" / "control_plane" / "field_dictionary.json"


@lru_cache(maxsize=1)
def load_field_dictionary() -> dict[str, Any]:
    if not FIELD_DICTIONARY_PATH.exists():
        return {}
    with FIELD_DICTIONARY_PATH.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def build_alias_index(field_dictionary: dict[str, Any] | None = None) -> dict[str, str]:
    dictionary = field_dictionary or load_field_dictionary()
    alias_index: dict[str, str] = {}

    for group in dictionary.get("normalization_rules", {}).get("alias_groups", []):
        canonical = group.get("canonical_field")
        if not canonical:
            continue
        alias_index[_key(canonical)] = str(canonical)
        for alias in group.get("aliases", []):
            alias_index[_key(alias)] = str(canonical)

    for field_def in dictionary.get("fields", []):
        canonical = field_def.get("field_id")
        if not canonical:
            continue
        alias_index[_key(canonical)] = str(canonical)
        for alias in field_def.get("aliases", []):
            alias_index[_key(alias)] = str(canonical)
    return alias_index


def build_field_defs(field_dictionary: dict[str, Any] | None = None) -> dict[str, dict[str, Any]]:
    dictionary = field_dictionary or load_field_dictionary()
    return {
        str(item["field_id"]): item
        for item in dictionary.get("fields", [])
        if item.get("field_id")
    }


def normalize_record(record: dict[str, Any], alias_index: dict[str, str] | None = None) -> dict[str, Any]:
    index = alias_index or build_alias_index()
    normalized: dict[str, Any] = {}
    for key, value in record.items():
        canonical = index.get(_key(key), str(key))
        if canonical in normalized and normalized[canonical] is not None and value in (None, ""):
            continue
        normalized[canonical] = value
    return normalized


def _key(value: Any) -> str:
    return str(value).strip().lower()
