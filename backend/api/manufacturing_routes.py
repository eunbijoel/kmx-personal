from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, File, Form, UploadFile
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from manufacturing.pipeline import run_pipeline, run_pipeline_from_bytes
from manufacturing.preprocess import preprocess_bytes
from manufacturing.store import list_drafts, load_draft, search_catalog

router = APIRouter(prefix="/ui/api/manufacturing", tags=["manufacturing-pipeline"])


class PreviewRequest(BaseModel):
    records: list[dict[str, Any]] = Field(default_factory=list)
    dataset_id: str | None = None
    title: str | None = None
    robot_mode: Literal["auto", "always", "never"] = "auto"


class RegisterRequest(BaseModel):
    draft_id: str
    confirm: bool = False
    title: str | None = None


@router.post("/preprocess")
async def manufacturing_preprocess(
    file: UploadFile = File(...),
    robot_mode: Literal["auto", "always", "never"] = Form("auto"),
) -> JSONResponse:
    raw = await file.read()
    result = preprocess_bytes(raw, filename=file.filename or "upload.json", robot_mode=robot_mode)
    return JSONResponse(result.to_dict())


@router.post("/pipeline/preview")
async def manufacturing_pipeline_preview(payload: PreviewRequest) -> JSONResponse:
    if not payload.records:
        return JSONResponse({"detail": "records required"}, status_code=400)
    result = run_pipeline(
        payload.records,
        filename="api-preview.json",
        dataset_id=payload.dataset_id,
        title=payload.title,
        robot_mode=payload.robot_mode,
        register=False,
    )
    return JSONResponse(result.to_dict())


@router.post("/pipeline/upload-preview")
async def manufacturing_upload_preview(
    file: UploadFile = File(...),
    dataset_id: str | None = Form(None),
    title: str | None = Form(None),
    robot_mode: Literal["auto", "always", "never"] = Form("auto"),
) -> JSONResponse:
    raw = await file.read()
    result = run_pipeline_from_bytes(
        raw,
        filename=file.filename or "upload.json",
        dataset_id=dataset_id,
        title=title,
        robot_mode=robot_mode,
        register=False,
    )
    return JSONResponse(result.to_dict())


@router.post("/pipeline/register")
async def manufacturing_pipeline_register(payload: RegisterRequest) -> JSONResponse:
    if not payload.confirm:
        return JSONResponse(
            {
                "detail": "explicit confirm=true required before catalog registration",
                "draft_id": payload.draft_id,
            },
            status_code=400,
        )
    try:
        draft = load_draft(payload.draft_id)
    except FileNotFoundError:
        return JSONResponse({"detail": "draft not found"}, status_code=404)

    # Re-run registration from stored draft AAS/metadata without re-uploading.
    from manufacturing.store import register_catalog_item, save_draft

    validation = draft.get("aas_validation") or {}
    if not validation.get("ok"):
        return JSONResponse(
            {"detail": "AAS validation failed; cannot register", "aas_validation": validation},
            status_code=400,
        )

    aas = draft.get("aas") or {}
    metadata = draft.get("metadata") or {}
    title = payload.title or draft.get("title")
    catalog_item = {
        "draft_id": payload.draft_id,
        "asset_id": aas.get("asset_id"),
        "title": title,
        "description": draft.get("notes", [""])[0] if draft.get("notes") else title,
        "dataset_id": draft.get("dataset_id"),
        "keywords": metadata.get("semantic_tags") or [],
        "fields": metadata.get("fields") or [],
        "quality_counts": (draft.get("preprocess") or {}).get("quality_counts"),
        "aas_shell_path": f"data/aas_store/{payload.draft_id}/shell.json",
        "aas_submodels_dir": f"data/aas_store/{payload.draft_id}/submodels",
        "registered_at": draft.get("created_at"),
        "status": "REGISTERED",
    }
    register_catalog_item(catalog_item)
    draft["status"] = "registered"
    draft["catalog_item"] = catalog_item
    if payload.title:
        draft["title"] = payload.title
    save_draft(payload.draft_id, draft)
    return JSONResponse(draft)


@router.get("/drafts")
async def manufacturing_list_drafts() -> JSONResponse:
    return JSONResponse({"count": len(list_drafts()), "items": list_drafts()})


@router.get("/drafts/{draft_id}")
async def manufacturing_get_draft(draft_id: str) -> JSONResponse:
    try:
        return JSONResponse(load_draft(draft_id))
    except FileNotFoundError:
        return JSONResponse({"detail": "draft not found"}, status_code=404)


@router.get("/catalog")
async def manufacturing_catalog(q: str = "") -> JSONResponse:
    items = search_catalog(q)
    return JSONResponse({"count": len(items), "items": items, "query": q})
