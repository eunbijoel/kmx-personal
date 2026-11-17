from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, File, Form, UploadFile
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from manufacturing_agent.hitl import apply_review, mark_needs_reapproval_if_hash_changed
from manufacturing_agent.schemas import MappingCandidate, ReviewActionRequest
from manufacturing_agent.store import list_runs, load_run
from manufacturing_agent.workflow import export_approved_json, run_workbench_pipeline

router = APIRouter(prefix="/ui/api/agent-workbench", tags=["agent-workbench"])

ROOT_DIR = Path(__file__).resolve().parents[2]
SAMPLES_DIR = ROOT_DIR / "data" / "agent_workbench" / "samples"


class RunJsonRequest(BaseModel):
    records: list[dict[str, Any]] = Field(default_factory=list)
    filename: str = "api.json"
    equipment_description: str = ""
    process_description: str = ""
    allow_llm: bool = True


class ReviewBody(BaseModel):
    action: Literal["approve", "modify", "reject"]
    reason: str = ""
    actor: str = "user"
    modified_candidates: list[MappingCandidate] | None = None


class SourceHashBody(BaseModel):
    raw_hash: str = Field(..., min_length=1, description="SHA-256 hex of current source payload")


@router.post("/run-upload")
async def run_upload(
    file: UploadFile = File(...),
    equipment_description: str = Form(""),
    process_description: str = Form(""),
    allow_llm: bool = Form(True),
) -> JSONResponse:
    raw = await file.read()
    try:
        run = await run_workbench_pipeline(
            raw=raw,
            filename=file.filename or "upload.json",
            equipment_description=equipment_description,
            process_description=process_description,
            allow_llm=allow_llm,
        )
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"detail": str(exc)}, status_code=400)
    return JSONResponse(run.model_dump())


@router.post("/run")
async def run_json(payload: RunJsonRequest) -> JSONResponse:
    try:
        run = await run_workbench_pipeline(
            records=payload.records,
            filename=payload.filename,
            equipment_description=payload.equipment_description,
            process_description=payload.process_description,
            allow_llm=payload.allow_llm,
        )
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"detail": str(exc)}, status_code=400)
    return JSONResponse(run.model_dump())


@router.get("/runs")
async def runs() -> JSONResponse:
    items = list_runs()
    return JSONResponse({"count": len(items), "items": items})


@router.get("/runs/{run_id}")
async def get_run(run_id: str) -> JSONResponse:
    try:
        return JSONResponse(load_run(run_id).model_dump())
    except FileNotFoundError:
        return JSONResponse({"detail": "run not found"}, status_code=404)


@router.post("/runs/{run_id}/review")
async def review_run(run_id: str, body: ReviewBody) -> JSONResponse:
    try:
        run = load_run(run_id)
        updated = apply_review(
            run,
            ReviewActionRequest(
                action=body.action,
                reason=body.reason,
                actor=body.actor,
                modified_candidates=body.modified_candidates,
            ),
        )
        return JSONResponse(updated.model_dump())
    except FileNotFoundError:
        return JSONResponse({"detail": "run not found"}, status_code=404)
    except ValueError as exc:
        return JSONResponse({"detail": str(exc)}, status_code=400)


@router.get("/runs/{run_id}/export")
async def export_run(run_id: str) -> JSONResponse:
    try:
        run = load_run(run_id)
        artifact = export_approved_json(run)
        return JSONResponse(artifact)
    except FileNotFoundError:
        return JSONResponse({"detail": "run not found"}, status_code=404)
    except ValueError as exc:
        return JSONResponse({"detail": str(exc)}, status_code=400)


@router.post("/runs/{run_id}/check-source-hash")
async def check_source_hash(run_id: str, body: SourceHashBody) -> JSONResponse:
    """Invalidate APPROVED status when the source payload hash no longer matches."""
    try:
        run = load_run(run_id)
        updated = mark_needs_reapproval_if_hash_changed(run, body.raw_hash)
        return JSONResponse(
            {
                "run_id": updated.run_id,
                "review_status": updated.review_status.value,
                "status": updated.status,
                "raw_hash": updated.profile.raw_hash,
                "provided_hash": body.raw_hash,
                "hash_match": body.raw_hash == updated.profile.raw_hash,
                "approved_artifact": updated.approved_artifact is not None,
            }
        )
    except FileNotFoundError:
        return JSONResponse({"detail": "run not found"}, status_code=404)


@router.get("/samples")
async def list_samples() -> JSONResponse:
    items = []
    if SAMPLES_DIR.exists():
        for path in sorted(SAMPLES_DIR.glob("*")):
            if path.suffix.lower() in {".json", ".csv"}:
                items.append({"name": path.name, "path": str(path.relative_to(ROOT_DIR))})
    return JSONResponse({"items": items})


@router.post("/run-sample")
async def run_sample(
    sample: str = Form("workbench_bms.json"),
    equipment_description: str = Form("EV battery pack test bench"),
    process_description: str = Form("Charge/discharge telemetry capture"),
    allow_llm: bool = Form(False),
) -> JSONResponse:
    path = SAMPLES_DIR / sample
    if not path.exists():
        return JSONResponse({"detail": f"sample not found: {sample}"}, status_code=404)
    raw = path.read_bytes()
    run = await run_workbench_pipeline(
        raw=raw,
        filename=path.name,
        equipment_description=equipment_description,
        process_description=process_description,
        allow_llm=allow_llm,
    )
    return JSONResponse(run.model_dump())
