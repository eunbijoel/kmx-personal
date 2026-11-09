from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal

from metadata.extractor import extract_metadata
from manufacturing.aas_builder import build_aas_from_preprocess_record
from manufacturing.aas_validate import validate_aas_documents
from manufacturing.preprocess import PreprocessResult, preprocess_bytes, preprocess_records
from manufacturing.store import register_catalog_item, save_draft

RobotMode = Literal["auto", "always", "never"]


@dataclass
class PipelineResult:
    draft_id: str
    status: str
    title: str
    dataset_id: str | None
    preprocess: dict[str, Any]
    metadata: dict[str, Any]
    aas: dict[str, Any]
    aas_validation: dict[str, Any]
    catalog_item: dict[str, Any] | None = None
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "draft_id": self.draft_id,
            "status": self.status,
            "title": self.title,
            "dataset_id": self.dataset_id,
            "preprocess": self.preprocess,
            "metadata": self.metadata,
            "aas": self.aas,
            "aas_validation": self.aas_validation,
            "catalog_item": self.catalog_item,
            "created_at": self.created_at,
            "notes": self.notes,
        }


def run_pipeline(
    records: list[dict[str, Any]],
    *,
    filename: str = "upload.json",
    dataset_id: str | None = None,
    title: str | None = None,
    robot_mode: RobotMode = "auto",
    register: bool = False,
    confirm_register: bool = False,
) -> PipelineResult:
    """Run manufacturing pipeline up to AAS draft, optionally register to catalog.

    Registration requires explicit confirm_register=True (user confirmation step).
    """
    preprocess = preprocess_records(records, robot_mode=robot_mode)
    return _finalize(
        preprocess,
        filename=filename,
        dataset_id=dataset_id,
        title=title,
        register=register,
        confirm_register=confirm_register,
    )


def run_pipeline_from_bytes(
    raw: bytes,
    *,
    filename: str = "upload.json",
    dataset_id: str | None = None,
    title: str | None = None,
    robot_mode: RobotMode = "auto",
    register: bool = False,
    confirm_register: bool = False,
) -> PipelineResult:
    preprocess = preprocess_bytes(raw, filename=filename, robot_mode=robot_mode)
    return _finalize(
        preprocess,
        filename=filename,
        dataset_id=dataset_id,
        title=title,
        register=register,
        confirm_register=confirm_register,
    )


def _finalize(
    preprocess: PreprocessResult,
    *,
    filename: str,
    dataset_id: str | None,
    title: str | None,
    register: bool,
    confirm_register: bool,
) -> PipelineResult:
    if not preprocess.records:
        raise ValueError("no records to process")

    primary = preprocess.records[0]
    metadata = extract_metadata(primary.normalized)
    metadata["record_count_estimate"] = len(preprocess.records)
    metadata["quality_counts"] = preprocess.quality_counts
    metadata["robot_rules_applied"] = preprocess.robot_rules_applied

    aas_build = build_aas_from_preprocess_record(primary, dataset_id=dataset_id)
    validation = validate_aas_documents(aas_build.shell, aas_build.submodels)

    draft_id = f"draft-{uuid.uuid4().hex[:12]}"
    resolved_title = title or dataset_id or filename or draft_id
    notes = [
        "AAS documents are stored under data/aas_store/<draft_id>/",
        "Catalog metadata is separate from AAS JSON (catalog_registry.json)",
        "EDC/DSP negotiation is out of scope for this pipeline",
    ]
    if not validation["ok"]:
        notes.append("AAS structural validation reported errors; draft saved but registration blocked")

    result = PipelineResult(
        draft_id=draft_id,
        status="draft",
        title=resolved_title,
        dataset_id=dataset_id,
        preprocess=preprocess.to_dict(),
        metadata=metadata,
        aas=aas_build.to_dict(),
        aas_validation=validation,
        notes=notes,
    )

    if register:
        if not confirm_register:
            result.status = "awaiting_confirmation"
            result.notes.append("register=true ignored without confirm_register=true")
        elif not validation["ok"]:
            result.status = "validation_failed"
        else:
            catalog_item = {
                "draft_id": draft_id,
                "asset_id": aas_build.asset_id,
                "title": resolved_title,
                "description": f"Manufacturing dataset from {filename}",
                "dataset_id": dataset_id,
                "keywords": metadata.get("semantic_tags") or [],
                "fields": metadata.get("fields") or [],
                "quality_counts": preprocess.quality_counts,
                "aas_shell_path": f"data/aas_store/{draft_id}/shell.json",
                "aas_submodels_dir": f"data/aas_store/{draft_id}/submodels",
                "registered_at": result.created_at,
                "status": "REGISTERED",
            }
            register_catalog_item(catalog_item)
            result.catalog_item = catalog_item
            result.status = "registered"

    save_draft(draft_id, result.to_dict())
    return result
