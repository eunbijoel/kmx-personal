from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, Depends, File, Form, UploadFile
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from db.database import get_db
from db.models import DID, VerifiableCredential
from manufacturing_agent.auth import resolve_review_auth
from manufacturing_agent.hitl import apply_review, mark_needs_reapproval_if_hash_changed
from manufacturing_agent.llm import check_ollama_available, configured_ollama_model
from manufacturing_agent.rulebook.factory import get_rulebook_adapter
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
    rulebook_source: str | None = None


class ReviewBody(BaseModel):
    action: Literal["approve", "modify", "reject"]
    reason: str = ""
    actor: str = Field(..., description="did:kmx:... of reviewer/approver")
    vc_id: str | None = None
    modified_candidates: list[MappingCandidate] | None = None


class SourceHashBody(BaseModel):
    raw_hash: str = Field(..., min_length=1, description="SHA-256 hex of current source payload")


class BootstrapIdentityBody(BaseModel):
    subject_hint: str = "workbench-approver"
    roles: list[str] = Field(default_factory=lambda: ["approver", "reviewer"])


def _sha256_text(value: str) -> str:
    import hashlib

    return hashlib.sha256(value.encode("utf-8")).hexdigest()


@router.get("/llm-status")
async def llm_status() -> JSONResponse:
    status = await check_ollama_available()
    return JSONResponse(status)


@router.get("/rulebook-status")
async def rulebook_status(source: str | None = None) -> JSONResponse:
    adapter = get_rulebook_adapter(source)
    meta = adapter.metadata()
    return JSONResponse(
        {
            **meta,
            "concept_count": len(adapter.list_concepts()),
            "configured_default_model": configured_ollama_model(),
        }
    )


@router.post("/bootstrap-identity")
async def bootstrap_identity(
    body: BootstrapIdentityBody,
    db: AsyncSession = Depends(get_db),
) -> JSONResponse:
    """Create a DID + workbench role VC for local HITL demos."""
    roles = [r.strip().lower() for r in body.roles if r.strip()]
    if not roles:
        return JSONResponse({"detail": "roles required"}, status_code=400)
    issuer_did = f"did:kmx:issuer:{uuid.uuid4().hex[:8]}"
    subject_did = f"did:kmx:{body.subject_hint}:{uuid.uuid4().hex[:12]}"
    for did_value, controller, entity_type in (
        (issuer_did, "kmx-workbench-issuer", "organization"),
        (subject_did, body.subject_hint, "human"),
    ):
        db.add(
            DID(
                did=did_value,
                controller=controller,
                public_key="ed25519-public-placeholder",
                private_key_enc="encrypted-private-placeholder",
                entity_type=entity_type,
            )
        )
    claims = {"workbench_roles": roles, "scope": "agent-workbench"}
    import json
    from datetime import datetime, timezone

    vc_payload = {
        "@context": ["https://www.w3.org/2018/credentials/v1"],
        "type": ["VerifiableCredential", "KmxWorkbenchCredential"],
        "issuer": issuer_did,
        "credentialSubject": {"id": subject_did, **claims},
    }
    signature = _sha256_text(json.dumps(vc_payload, sort_keys=True) + issuer_did)
    vc_id = f"vc:kmx:{uuid.uuid4().hex[:12]}"
    db.add(
        VerifiableCredential(
            vc_id=vc_id,
            issuer_did=issuer_did,
            subject_did=subject_did,
            vc_type="KmxWorkbenchCredential",
            claims=claims,
            signature=signature,
            issued_at=datetime.now(timezone.utc).replace(tzinfo=None),
        )
    )
    await db.flush()
    return JSONResponse(
        {
            "issuer_did": issuer_did,
            "actor": subject_did,
            "vc_id": vc_id,
            "roles": roles,
            "claims": claims,
        }
    )


@router.post("/run-upload")
async def run_upload(
    file: UploadFile = File(...),
    equipment_description: str = Form(""),
    process_description: str = Form(""),
    allow_llm: bool = Form(True),
    rulebook_source: str | None = Form(None),
) -> JSONResponse:
    raw = await file.read()
    try:
        run = await run_workbench_pipeline(
            raw=raw,
            filename=file.filename or "upload.json",
            equipment_description=equipment_description,
            process_description=process_description,
            allow_llm=allow_llm,
            rulebook_source=rulebook_source,
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
            rulebook_source=payload.rulebook_source,
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
async def review_run(
    run_id: str,
    body: ReviewBody,
    db: AsyncSession = Depends(get_db),
) -> JSONResponse:
    try:
        auth = await resolve_review_auth(
            db,
            actor=body.actor,
            action=body.action,
            vc_id=body.vc_id,
        )
        run = load_run(run_id)
        updated = apply_review(
            run,
            ReviewActionRequest(
                action=body.action,
                reason=body.reason,
                actor=auth.actor_did,
                vc_id=auth.vc_id,
                modified_candidates=body.modified_candidates,
            ),
            auth=auth,
        )
        return JSONResponse(updated.model_dump())
    except FileNotFoundError:
        return JSONResponse({"detail": "run not found"}, status_code=404)
    except PermissionError as exc:
        return JSONResponse({"detail": str(exc)}, status_code=403)
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
    rulebook_source: str | None = Form(None),
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
        rulebook_source=rulebook_source,
    )
    return JSONResponse(run.model_dump())
