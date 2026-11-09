from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from manufacturing.aas_builder import build_aas_from_record
from manufacturing.aas_validate import validate_aas_documents
from manufacturing.pipeline import run_pipeline_from_bytes
from manufacturing.store import search_catalog


FIXTURES = Path(__file__).resolve().parent / "fixtures"


def test_aas_structure_and_validation():
    record = {
        "soc_pct": 70.0,
        "soh_pct": 95.0,
        "temperature_c": 30.0,
        "vin": "VIN123",
        "pack_id": "PACK1",
    }
    built = build_aas_from_record(record, dataset_id="auto-ev-bms-v1")
    assert built.shell["modelType"] == "AssetAdministrationShell"
    assert built.shell["submodels"][0]["type"] == "ModelReference"
    assert "_meta" not in built.shell
    for submodel in built.submodels:
        assert "_meta" not in submodel
    validation = validate_aas_documents(built.shell, built.submodels)
    assert validation["ok"] is True


def test_pipeline_preview_and_confirmed_register(tmp_path, monkeypatch):
    import manufacturing.store as store

    monkeypatch.setattr(store, "AAS_STORE_DIR", tmp_path / "aas_store")
    monkeypatch.setattr(store, "CATALOG_REGISTRY_PATH", tmp_path / "aas_store" / "catalog_registry.json")

    raw = (FIXTURES / "sample_bms.json").read_bytes()
    preview = run_pipeline_from_bytes(
        raw,
        filename="sample_bms.json",
        dataset_id="auto-ev-bms-v1",
        title="BMS sample",
        robot_mode="never",
        register=False,
    )
    assert preview.status == "draft"
    assert preview.aas_validation["ok"] is True
    assert preview.metadata["fields"]

    confirmed = run_pipeline_from_bytes(
        raw,
        filename="sample_bms.json",
        dataset_id="auto-ev-bms-v1",
        title="BMS sample registered",
        robot_mode="never",
        register=True,
        confirm_register=True,
    )
    assert confirmed.status == "registered"
    assert confirmed.catalog_item is not None
    items = search_catalog("BMS")
    assert any(item["draft_id"] == confirmed.draft_id for item in items)


def test_register_without_confirm_stays_unregistered(tmp_path, monkeypatch):
    import manufacturing.store as store

    monkeypatch.setattr(store, "AAS_STORE_DIR", tmp_path / "aas_store")
    monkeypatch.setattr(store, "CATALOG_REGISTRY_PATH", tmp_path / "aas_store" / "catalog_registry.json")

    raw = (FIXTURES / "sample_robot.json").read_bytes()
    result = run_pipeline_from_bytes(
        raw,
        filename="sample_robot.json",
        robot_mode="auto",
        register=True,
        confirm_register=False,
    )
    assert result.status == "awaiting_confirmation"
    assert search_catalog() == []
