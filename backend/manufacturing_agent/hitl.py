from __future__ import annotations

import copy
from typing import Any

from manufacturing_agent.schemas import (
    MappingCandidate,
    ReviewActionRequest,
    ReviewStatus,
    WorkbenchRun,
    utc_now,
)
from manufacturing_agent.store import save_run


def apply_review(run: WorkbenchRun, request: ReviewActionRequest) -> WorkbenchRun:
    before = [c.model_dump() for c in run.mapping.candidates]
    history_entry: dict[str, Any] = {
        "at": utc_now(),
        "actor": request.actor,
        "action": request.action,
        "reason": request.reason,
    }

    if request.action == "reject":
        run.review_status = ReviewStatus.REJECTED
        run.status = "rejected"
        run.approved_artifact = None
        history_entry["diff"] = {"before": before, "after": before}
    elif request.action == "approve":
        if any(c.decision == "map" and not c.concept_id for c in run.mapping.candidates):
            raise ValueError("Cannot approve mappings that lack concept_id")
        if not run.validation.ok:
            # Allow approve only if failures are absent; unverified may remain with reason
            raise ValueError("Cannot approve while validation has FAIL issues")
        run.review_status = ReviewStatus.APPROVED
        run.status = "approved"
        run.approved_artifact = build_approved_artifact(run)
        history_entry["diff"] = {"before": before, "after": before}
    elif request.action == "modify":
        if not request.modified_candidates:
            raise ValueError("modified_candidates required for modify action")
        after_models = [MappingCandidate.model_validate(c) if not isinstance(c, MappingCandidate) else c for c in request.modified_candidates]
        after = [c.model_dump() for c in after_models]
        if after != before:
            run.mapping.candidates = after_models
            run.review_status = ReviewStatus.MODIFIED
            run.status = "modified_pending_reapproval"
            run.approved_artifact = None
            # modification invalidates previous approval
            history_entry["diff"] = {"before": before, "after": after}
            # force reapproval marker
            run.review_status = ReviewStatus.NEEDS_REAPPROVAL
        else:
            history_entry["diff"] = {"before": before, "after": after}
            run.notes.append("Modify requested but no candidate changes detected")
    else:
        raise ValueError(f"Unsupported action: {request.action}")

    run.evidence.review_status = run.review_status
    run.evidence.review_history.append(history_entry)
    run.updated_at = utc_now()
    save_run(run)
    return run


def build_approved_artifact(run: WorkbenchRun) -> dict[str, Any]:
    return {
        "artifact_type": "kmx.manufacturing_agent.mapping",
        "interface": {
            "future_aas_export": True,
            "future_dcat_export": True,
            "future_kedc_adapter": True,
            "note": "Adapters only — AAS/DCAT/K-EDC runtime integration is out of MVP scope.",
        },
        "run_id": run.run_id,
        "approved_at": utc_now(),
        "raw_hash": run.profile.raw_hash,
        "rulebook": {
            "rulebook_id": run.evidence.rulebook_id,
            "version": run.evidence.rulebook_version,
            "disclaimer": run.evidence.rulebook_disclaimer,
            "official_standard": False,
        },
        "mappings": [c.model_dump() for c in run.mapping.candidates if c.decision == "map"],
        "all_candidates": [c.model_dump() for c in run.mapping.candidates],
        "validation": run.validation.model_dump(),
        "evidence": run.evidence.model_dump(),
    }


def mark_needs_reapproval_if_hash_changed(run: WorkbenchRun, new_raw_hash: str) -> WorkbenchRun:
    if run.review_status == ReviewStatus.APPROVED and new_raw_hash != run.profile.raw_hash:
        run.review_status = ReviewStatus.NEEDS_REAPPROVAL
        run.status = "needs_reapproval"
        run.approved_artifact = None
        run.evidence.review_history.append(
            {
                "at": utc_now(),
                "actor": "system",
                "action": "invalidate_approval",
                "reason": "Source data hash changed after approval",
                "diff": {"before_hash": run.profile.raw_hash, "after_hash": new_raw_hash},
            }
        )
        run.evidence.review_status = run.review_status
        save_run(run)
    return run
