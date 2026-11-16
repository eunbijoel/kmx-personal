from __future__ import annotations

import uuid
from typing import Any

from manufacturing_agent.hitl import build_approved_artifact
from manufacturing_agent.profiling import profile_records, profile_upload
from manufacturing_agent.rulebook.json_adapter import JsonRulebookAdapter
from manufacturing_agent.schemas import (
    EvidenceBundle,
    ReviewStatus,
    ToolTrace,
    WorkbenchRun,
    utc_now,
)
from manufacturing_agent.store import save_run
from manufacturing_agent.tools.map_semantic import propose_mappings
from manufacturing_agent.tools.retrieve import retrieve_rules_for_profile
from manufacturing_agent.tools.validate import validate_mapping


async def run_workbench_pipeline(
    *,
    records: list[dict[str, Any]] | None = None,
    raw: bytes | None = None,
    filename: str = "upload.json",
    equipment_description: str = "",
    process_description: str = "",
    allow_llm: bool = True,
) -> WorkbenchRun:
    """Fixed-step agent workflow (no arbitrary shell/file tools)."""
    adapter = JsonRulebookAdapter()
    meta = adapter.metadata()
    run_id = f"agent-{uuid.uuid4().hex[:12]}"
    notes = [
        "Rulebook is a TEST mock and is not an official KSA/ISO/IDTA standard.",
        "LLM output is advisory and must pass schema + rulebook validation.",
    ]

    if raw is not None:
        profile, records = profile_upload(
            raw,
            filename=filename,
            equipment_description=equipment_description,
            process_description=process_description,
        )
    else:
        if not records:
            raise ValueError("records or raw bytes required")
        profile = profile_records(
            records,
            filename=filename,
            equipment_description=equipment_description,
            process_description=process_description,
        )

    retrieved, retrieve_trace = retrieve_rules_for_profile(profile, adapter)
    mapping, map_trace = await propose_mappings(profile, retrieved, allow_llm=allow_llm)
    validation, validate_trace = validate_mapping(profile, mapping, adapter)

    traces = [
        ToolTrace.model_validate(retrieve_trace),
        ToolTrace.model_validate(map_trace),
        ToolTrace.model_validate(validate_trace),
    ]
    evidence = EvidenceBundle(
        run_id=run_id,
        raw_hash=profile.raw_hash,
        rulebook_id=str(meta["rulebook_id"]),
        rulebook_version=str(meta["version"]),
        rulebook_disclaimer=str(meta["disclaimer"]),
        retrieved_evidence_ids=sorted({h.concept_id for h in retrieved}),
        model_name=mapping.model_name,
        prompt_version=mapping.prompt_version,
        tool_traces=traces,
        validation=validation,
        review_status=ReviewStatus.PENDING,
    )
    if mapping.llm_error:
        notes.append(f"LLM unavailable or failed: {mapping.llm_error}. Rule-based fallback used.")

    run = WorkbenchRun(
        run_id=run_id,
        status="awaiting_review",
        created_at=utc_now(),
        updated_at=utc_now(),
        profile=profile,
        retrieved_rules=retrieved,
        mapping=mapping,
        validation=validation,
        evidence=evidence,
        review_status=ReviewStatus.PENDING,
        notes=notes,
    )
    save_run(run)
    return run


def export_approved_json(run: WorkbenchRun) -> dict[str, Any]:
    if run.review_status != ReviewStatus.APPROVED or not run.approved_artifact:
        raise ValueError("Run is not approved; export blocked")
    return copy_artifact(run)


def copy_artifact(run: WorkbenchRun) -> dict[str, Any]:
    return run.approved_artifact or build_approved_artifact(run)
