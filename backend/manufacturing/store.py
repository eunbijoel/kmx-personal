from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parents[2]
AAS_STORE_DIR = ROOT_DIR / "data" / "aas_store"
CATALOG_REGISTRY_PATH = AAS_STORE_DIR / "catalog_registry.json"


def ensure_store() -> None:
    AAS_STORE_DIR.mkdir(parents=True, exist_ok=True)
    if not CATALOG_REGISTRY_PATH.exists():
        _write_json(CATALOG_REGISTRY_PATH, {"version": "1.0.0", "items": []})


def save_draft(draft_id: str, payload: dict[str, Any]) -> Path:
    ensure_store()
    draft_dir = AAS_STORE_DIR / draft_id
    draft_dir.mkdir(parents=True, exist_ok=True)
    _write_json(draft_dir / "pipeline_result.json", payload)
    aas = payload.get("aas") or {}
    if aas.get("shell"):
        _write_json(draft_dir / "shell.json", aas["shell"])
    submodels = aas.get("submodels") or []
    sm_dir = draft_dir / "submodels"
    sm_dir.mkdir(exist_ok=True)
    for submodel in submodels:
        id_short = submodel.get("idShort") or "Submodel"
        _write_json(sm_dir / f"{id_short}.json", submodel)
    return draft_dir


def load_draft(draft_id: str) -> dict[str, Any]:
    path = AAS_STORE_DIR / draft_id / "pipeline_result.json"
    if not path.exists():
        raise FileNotFoundError(draft_id)
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def list_drafts() -> list[dict[str, Any]]:
    ensure_store()
    items: list[dict[str, Any]] = []
    for path in sorted(AAS_STORE_DIR.glob("*/pipeline_result.json")):
        with path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
        items.append(
            {
                "draft_id": path.parent.name,
                "status": payload.get("status"),
                "title": payload.get("title"),
                "dataset_id": payload.get("dataset_id"),
                "created_at": payload.get("created_at"),
                "quality_counts": (payload.get("preprocess") or {}).get("quality_counts"),
            }
        )
    return items


def register_catalog_item(item: dict[str, Any]) -> dict[str, Any]:
    ensure_store()
    registry = _read_json(CATALOG_REGISTRY_PATH)
    items = registry.setdefault("items", [])
    # replace existing draft registration if present
    items = [row for row in items if row.get("draft_id") != item.get("draft_id")]
    items.insert(0, item)
    registry["items"] = items
    registry["updated_at"] = _utc_now()
    _write_json(CATALOG_REGISTRY_PATH, registry)
    return item


def search_catalog(query: str = "") -> list[dict[str, Any]]:
    ensure_store()
    registry = _read_json(CATALOG_REGISTRY_PATH)
    items = registry.get("items") or []
    q = query.strip().lower()
    if not q:
        return items
    out = []
    for item in items:
        hay = " ".join(
            [
                str(item.get("title") or ""),
                str(item.get("description") or ""),
                str(item.get("dataset_id") or ""),
                " ".join(item.get("keywords") or []),
            ]
        ).lower()
        if q in hay:
            out.append(item)
    return out


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)


def _read_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)
