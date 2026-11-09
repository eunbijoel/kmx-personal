from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

from manufacturing.types import QualityFlag, RecordResult

ROOT_DIR = Path(__file__).resolve().parents[2]
AAS_MAPPING_PATH = ROOT_DIR / "data" / "control_plane" / "mappings" / "aas_mapping.json"
FIELD_DICTIONARY_PATH = ROOT_DIR / "data" / "control_plane" / "field_dictionary.json"


@dataclass
class AASBuildResult:
    shell: dict[str, Any]
    submodels: list[dict[str, Any]]
    mapping_used: list[dict[str, str]]
    unmapped_fields: list[str]
    asset_id: str
    dataset_id: str | None
    meta: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "asset_id": self.asset_id,
            "dataset_id": self.dataset_id,
            "shell": self.shell,
            "submodels": self.submodels,
            "mapping_used": self.mapping_used,
            "unmapped_fields": self.unmapped_fields,
            "meta": self.meta,
        }


@lru_cache(maxsize=1)
def _load_aas_mappings() -> dict[str, list[dict[str, str]]]:
    if not AAS_MAPPING_PATH.exists():
        return {}
    with AAS_MAPPING_PATH.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    out: dict[str, list[dict[str, str]]] = {}
    for item in payload.get("mappings", []):
        dataset_id = item.get("dataset_id")
        if dataset_id:
            out[str(dataset_id)] = list(item.get("field_map", []))
    return out


@lru_cache(maxsize=1)
def _semantic_from_dictionary() -> dict[str, dict[str, str]]:
    if not FIELD_DICTIONARY_PATH.exists():
        return {}
    with FIELD_DICTIONARY_PATH.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    out: dict[str, dict[str, str]] = {}
    for field_def in payload.get("fields", []):
        field_id = field_def.get("field_id")
        concept = field_def.get("semantic_concept") or {}
        if field_id and concept.get("aas_submodel") and concept.get("aas_property"):
            out[str(field_id)] = {
                "aas_submodel": str(concept["aas_submodel"]),
                "aas_property": str(concept["aas_property"]),
            }
    return out


def resolve_field_map(record: dict[str, Any], dataset_id: str | None = None) -> list[dict[str, str]]:
    mappings = _load_aas_mappings()
    if dataset_id and dataset_id in mappings:
        return mappings[dataset_id]

    # Auto-pick mapping with most overlapping fields.
    best: list[dict[str, str]] = []
    best_hits = -1
    for field_map in mappings.values():
        hits = sum(1 for item in field_map if item.get("field") in record)
        if hits > best_hits:
            best_hits = hits
            best = field_map
    if best_hits > 0:
        return best

    # Fallback to field_dictionary semantic_concept.
    fallback: list[dict[str, str]] = []
    semantic = _semantic_from_dictionary()
    for field_name in record.keys():
        concept = semantic.get(field_name)
        if concept:
            fallback.append(
                {
                    "field": field_name,
                    "aas_submodel": concept["aas_submodel"],
                    "aas_property": concept["aas_property"],
                }
            )
    return fallback


def build_aas_from_record(
    record: dict[str, Any],
    *,
    dataset_id: str | None = None,
    asset_id: str | None = None,
    quality_flag: QualityFlag | str | None = None,
    derived: dict[str, Any] | None = None,
) -> AASBuildResult:
    asset = asset_id or f"urn:kmx:asset:{uuid.uuid4().hex[:12]}"
    field_map = resolve_field_map(record, dataset_id=dataset_id)
    grouped: dict[str, list[dict[str, Any]]] = {}
    used: list[dict[str, str]] = []
    mapped_fields: set[str] = set()

    for item in field_map:
        field_name = item.get("field")
        submodel = item.get("aas_submodel")
        prop = item.get("aas_property")
        if not field_name or not submodel or not prop:
            continue
        if field_name not in record:
            continue
        grouped.setdefault(submodel, []).append(
            _property(prop, record.get(field_name))
        )
        used.append({"field": field_name, "aas_submodel": submodel, "aas_property": prop})
        mapped_fields.add(field_name)

    if quality_flag is not None:
        grouped.setdefault("QualityAndDiagnostics", []).append(
            _property("QualityFlag", str(quality_flag.value if isinstance(quality_flag, QualityFlag) else quality_flag))
        )
    if derived:
        for key, value in derived.items():
            if isinstance(value, (str, int, float, bool)) or value is None:
                grouped.setdefault("QualityAndDiagnostics", []).append(_property(_id_short(key), value))

    submodels: list[dict[str, Any]] = []
    submodel_ids: list[str] = []
    for id_short, elements in grouped.items():
        sm_id = f"{asset}/submodel/{id_short}"
        submodel_ids.append(sm_id)
        submodels.append(
            {
                "modelType": "Submodel",
                "id": sm_id,
                "idShort": id_short,
                "kind": "Instance",
                "semanticId": {
                    "type": "ExternalReference",
                    "keys": [
                        {
                            "type": "GlobalReference",
                            "value": f"https://kmx.local/aas/submodel/{id_short}/1/0",
                        }
                    ],
                },
                "submodelElements": elements,
            }
        )

    shell = {
        "modelType": "AssetAdministrationShell",
        "id": f"{asset}/shell",
        "idShort": _id_short(asset.split(":")[-1]),
        "assetInformation": {
            "assetKind": "Instance",
            "globalAssetId": asset,
        },
        "submodels": [
            {
                "type": "ModelReference",
                "keys": [{"type": "Submodel", "value": sm_id}],
            }
            for sm_id in submodel_ids
        ],
    }

    unmapped = [key for key in record.keys() if key not in mapped_fields]
    return AASBuildResult(
        shell=shell,
        submodels=submodels,
        mapping_used=used,
        unmapped_fields=unmapped,
        asset_id=asset,
        dataset_id=dataset_id,
        meta={
            "generator": "kmx.manufacturing.aas_builder",
            "aas_spec": "IDTA-01001/01002 AAS v3 JSON metamodel (structural subset)",
            "record_field_count": len(record),
            "mapped_field_count": len(mapped_fields),
        },
    )


def build_aas_from_preprocess_record(
    row: RecordResult,
    *,
    dataset_id: str | None = None,
    asset_id: str | None = None,
) -> AASBuildResult:
    return build_aas_from_record(
        row.normalized,
        dataset_id=dataset_id,
        asset_id=asset_id,
        quality_flag=row.quality_flag,
        derived=row.derived,
    )


def _property(id_short: str, value: Any) -> dict[str, Any]:
    return {
        "modelType": "Property",
        "idShort": _id_short(id_short),
        "valueType": _value_type(value),
        "value": "" if value is None else str(value),
    }


def _value_type(value: Any) -> str:
    if isinstance(value, bool):
        return "xs:boolean"
    if isinstance(value, int) and not isinstance(value, bool):
        return "xs:integer"
    if isinstance(value, float):
        return "xs:double"
    return "xs:string"


def _id_short(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_]", "", value.replace("-", "_"))
    if not cleaned:
        return "Value"
    if cleaned[0].isdigit():
        cleaned = f"F_{cleaned}"
    return cleaned[:128]
