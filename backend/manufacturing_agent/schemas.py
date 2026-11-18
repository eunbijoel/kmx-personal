from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ReviewStatus(str, Enum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    MODIFIED = "MODIFIED"
    REJECTED = "REJECTED"
    NEEDS_REAPPROVAL = "NEEDS_REAPPROVAL"


class ValidationStatus(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    UNVERIFIED = "UNVERIFIED"


class FieldProfile(BaseModel):
    field_name: str
    inferred_type: str
    sample_values: list[Any] = Field(default_factory=list)
    missing_ratio: float = 0.0
    null_count: int = 0
    distinct_count: int = 0


class DataProfile(BaseModel):
    filename: str
    record_count: int
    fields: list[FieldProfile]
    equipment_description: str = ""
    process_description: str = ""
    raw_hash: str
    sample_records: list[dict[str, Any]] = Field(default_factory=list)


class RuleHit(BaseModel):
    concept_id: str
    name: str
    score: float
    matched_on: str
    source_field: str | None = None
    aas_submodel: str | None = None
    aas_property: str | None = None
    unit: str | None = None
    value_type: str | None = None
    description: str = ""
    rulebook_id: str
    rulebook_version: str
    disclaimer: str = ""


class MappingCandidate(BaseModel):
    source_field: str
    concept_id: str | None = None
    concept_name: str | None = None
    aas_submodel: str | None = None
    aas_property: str | None = None
    confidence: float = 0.0
    evidence_ids: list[str] = Field(default_factory=list)
    alternatives: list[dict[str, Any]] = Field(default_factory=list)
    rationale: str = ""
    decision: Literal["map", "needs_review", "abstain"] = "needs_review"
    unit_assumption: str | None = None
    source: Literal["rule", "llm", "hybrid", "fallback"] = "rule"


class MappingProposal(BaseModel):
    candidates: list[MappingCandidate]
    uncertain_fields: list[str] = Field(default_factory=list)
    model_name: str | None = None
    prompt_version: str = "mapping-v1"
    llm_available: bool = False
    llm_error: str | None = None
    used_rule_fallback: bool = False


class ValidationIssue(BaseModel):
    field: str
    code: str
    message: str
    status: ValidationStatus
    evidence_ids: list[str] = Field(default_factory=list)


class ValidationReport(BaseModel):
    ok: bool
    issues: list[ValidationIssue] = Field(default_factory=list)
    checked_at: str = Field(default_factory=utc_now)


class ToolTrace(BaseModel):
    tool: str
    status: str
    started_at: str
    finished_at: str
    detail: dict[str, Any] = Field(default_factory=dict)


class EvidenceBundle(BaseModel):
    run_id: str
    created_at: str = Field(default_factory=utc_now)
    raw_hash: str
    rulebook_id: str
    rulebook_version: str
    rulebook_disclaimer: str
    retrieved_evidence_ids: list[str] = Field(default_factory=list)
    model_name: str | None = None
    prompt_version: str | None = None
    tool_traces: list[ToolTrace] = Field(default_factory=list)
    validation: ValidationReport | None = None
    review_status: ReviewStatus = ReviewStatus.PENDING
    review_history: list[dict[str, Any]] = Field(default_factory=list)


class WorkbenchRun(BaseModel):
    run_id: str
    status: str
    created_at: str = Field(default_factory=utc_now)
    updated_at: str = Field(default_factory=utc_now)
    profile: DataProfile
    retrieved_rules: list[RuleHit] = Field(default_factory=list)
    mapping: MappingProposal
    validation: ValidationReport
    evidence: EvidenceBundle
    review_status: ReviewStatus = ReviewStatus.PENDING
    approved_artifact: dict[str, Any] | None = None
    notes: list[str] = Field(default_factory=list)


class ReviewActionRequest(BaseModel):
    action: Literal["approve", "modify", "reject"]
    reason: str = ""
    modified_candidates: list[MappingCandidate] | None = None
    actor: str = Field(..., description="did:kmx:... of the reviewer/approver")
    vc_id: str | None = None
