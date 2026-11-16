from __future__ import annotations

from manufacturing_agent.rulebook.adapter import RulebookAdapter
from manufacturing_agent.schemas import (
    DataProfile,
    MappingProposal,
    ValidationIssue,
    ValidationReport,
    ValidationStatus,
    utc_now,
)


def validate_mapping(
    profile: DataProfile,
    proposal: MappingProposal,
    adapter: RulebookAdapter,
) -> tuple[ValidationReport, dict]:
    started = utc_now()
    issues: list[ValidationIssue] = []

    for candidate in proposal.candidates:
        field = next((f for f in profile.fields if f.field_name == candidate.source_field), None)
        if candidate.decision == "map":
            if not candidate.concept_id:
                issues.append(
                    ValidationIssue(
                        field=candidate.source_field,
                        code="missing_concept",
                        message="Mapped decision without concept_id.",
                        status=ValidationStatus.FAIL,
                    )
                )
                continue
            concept = adapter.get_concept(candidate.concept_id)
            if concept is None:
                issues.append(
                    ValidationIssue(
                        field=candidate.source_field,
                        code="concept_not_in_rulebook",
                        message=f"Concept {candidate.concept_id} is not in the active rulebook.",
                        status=ValidationStatus.FAIL,
                        evidence_ids=candidate.evidence_ids,
                    )
                )
                continue
            # type check
            expected = str(concept.get("value_type") or "").lower()
            if field and expected in {"number", "integer"} and field.inferred_type not in {"number", "integer", "unknown"}:
                issues.append(
                    ValidationIssue(
                        field=candidate.source_field,
                        code="type_mismatch",
                        message=f"Field type {field.inferred_type} does not match concept type {expected}.",
                        status=ValidationStatus.FAIL,
                        evidence_ids=[candidate.concept_id],
                    )
                )
            # unit uncertainty
            if candidate.unit_assumption:
                issues.append(
                    ValidationIssue(
                        field=candidate.source_field,
                        code="unit_unclear",
                        message=candidate.unit_assumption,
                        status=ValidationStatus.UNVERIFIED,
                        evidence_ids=[candidate.concept_id],
                    )
                )
            # range check on samples
            constraints = concept.get("constraints") or {}
            if field and expected in {"number", "integer"}:
                for sample in field.sample_values:
                    try:
                        number = float(sample)
                    except (TypeError, ValueError):
                        continue
                    min_v = constraints.get("min")
                    max_v = constraints.get("max")
                    if min_v is not None and number < float(min_v):
                        issues.append(
                            ValidationIssue(
                                field=candidate.source_field,
                                code="range_violation",
                                message=f"Sample {number} < min {min_v}",
                                status=ValidationStatus.FAIL,
                                evidence_ids=[candidate.concept_id],
                            )
                        )
                    if max_v is not None and number > float(max_v):
                        issues.append(
                            ValidationIssue(
                                field=candidate.source_field,
                                code="range_violation",
                                message=f"Sample {number} > max {max_v}",
                                status=ValidationStatus.FAIL,
                                evidence_ids=[candidate.concept_id],
                            )
                        )
        elif candidate.decision == "needs_review":
            issues.append(
                ValidationIssue(
                    field=candidate.source_field,
                    code="needs_review",
                    message=candidate.rationale or "Mapping requires human review.",
                    status=ValidationStatus.UNVERIFIED,
                    evidence_ids=candidate.evidence_ids,
                )
            )
        else:
            issues.append(
                ValidationIssue(
                    field=candidate.source_field,
                    code="abstain",
                    message=candidate.rationale or "No mapping proposed.",
                    status=ValidationStatus.UNVERIFIED,
                )
            )

    fail = any(i.status == ValidationStatus.FAIL for i in issues)
    report = ValidationReport(ok=not fail, issues=issues)
    finished = utc_now()
    trace = {
        "tool": "validation",
        "status": "ok",
        "started_at": started,
        "finished_at": finished,
        "detail": {
            "ok": report.ok,
            "fail_count": sum(1 for i in issues if i.status == ValidationStatus.FAIL),
            "unverified_count": sum(1 for i in issues if i.status == ValidationStatus.UNVERIFIED),
        },
    }
    return report, trace
