from __future__ import annotations

import csv
import io
import json
from pathlib import Path
from typing import Any


class LoadError(ValueError):
    """Raised when uploaded manufacturing data cannot be parsed."""


def load_records_from_text(content: str, *, filename: str = "upload.json") -> list[dict[str, Any]]:
    name = filename.lower()
    text = content.strip()
    if not text:
        raise LoadError("empty file")

    if name.endswith(".csv") or ("," in text.splitlines()[0] and not text.startswith("{") and not text.startswith("[")):
        return _load_csv(text)
    return _load_json(text)


def load_records_from_bytes(raw: bytes, *, filename: str = "upload.json") -> list[dict[str, Any]]:
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise LoadError(f"unsupported encoding: {exc}") from exc
    return load_records_from_text(text, filename=filename)


def load_records_from_path(path: Path) -> list[dict[str, Any]]:
    return load_records_from_bytes(path.read_bytes(), filename=path.name)


def _load_csv(text: str) -> list[dict[str, Any]]:
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        raise LoadError("CSV has no header row")
    rows: list[dict[str, Any]] = []
    for row in reader:
        cleaned = {str(k).strip(): _coerce_scalar(v) for k, v in row.items() if k is not None}
        if any(v not in (None, "") for v in cleaned.values()):
            rows.append(cleaned)
    if not rows:
        raise LoadError("CSV contains no data rows")
    return rows


def _load_json(text: str) -> list[dict[str, Any]]:
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise LoadError(f"invalid JSON: {exc}") from exc

    if isinstance(payload, list):
        records = payload
    elif isinstance(payload, dict):
        for key in ("records", "items", "data", "rows"):
            if isinstance(payload.get(key), list):
                records = payload[key]
                break
        else:
            records = [payload]
    else:
        raise LoadError("JSON root must be object or array")

    out: list[dict[str, Any]] = []
    for idx, item in enumerate(records):
        if not isinstance(item, dict):
            raise LoadError(f"record[{idx}] is not an object")
        out.append(item)
    if not out:
        raise LoadError("JSON contains no records")
    return out


def _coerce_scalar(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (int, float, bool)):
        return value
    text = str(value).strip()
    if text == "":
        return None
    lower = text.lower()
    if lower in {"true", "false"}:
        return lower == "true"
    try:
        if "." in text or "e" in lower:
            return float(text)
        return int(text)
    except ValueError:
        return text
