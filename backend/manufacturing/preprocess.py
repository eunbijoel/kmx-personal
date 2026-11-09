from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from manufacturing.io_loader import load_records_from_bytes, load_records_from_text
from manufacturing.normalize import build_alias_index, build_field_defs, normalize_record
from manufacturing.quality import evaluate_common_quality, summarize_dataset_quality
from manufacturing.robot_rules import evaluate_robot_quality, looks_like_robot_record
from manufacturing.types import QualityFlag, RecordResult, ValidationIssue, worst_flag

RobotMode = Literal["auto", "always", "never"]


@dataclass
class PreprocessResult:
    records: list[RecordResult]
    dataset_summary: dict[str, Any]
    robot_rules_applied: bool
    field_alias_hits: dict[str, str] = field(default_factory=dict)

    @property
    def quality_counts(self) -> dict[str, int]:
        counts = {flag.value: 0 for flag in QualityFlag}
        for row in self.records:
            counts[row.quality_flag.value] += 1
        return counts

    def to_dict(self) -> dict[str, Any]:
        return {
            "record_count": len(self.records),
            "quality_counts": self.quality_counts,
            "robot_rules_applied": self.robot_rules_applied,
            "field_alias_hits": self.field_alias_hits,
            "dataset_summary": self.dataset_summary,
            "records": [row.to_dict() for row in self.records],
        }


def preprocess_records(
    records: list[dict[str, Any]],
    *,
    robot_mode: RobotMode = "auto",
) -> PreprocessResult:
    alias_index = build_alias_index()
    field_defs = build_field_defs()
    alias_hits: dict[str, str] = {}
    normalized_rows: list[dict[str, Any]] = []
    results: list[RecordResult] = []

    apply_robot = False
    if robot_mode == "always":
        apply_robot = True
    elif robot_mode == "auto" and records:
        apply_robot = looks_like_robot_record(normalize_record(records[0], alias_index))

    for idx, original in enumerate(records):
        for key in original.keys():
            canonical = alias_index.get(str(key).strip().lower())
            if canonical and canonical != key:
                alias_hits[str(key)] = canonical

        normalized = normalize_record(original, alias_index)
        issues: list[ValidationIssue] = evaluate_common_quality(normalized, field_defs=field_defs)
        derived: dict[str, Any] = {}

        if apply_robot:
            robot_issues, robot_derived = evaluate_robot_quality(normalized)
            issues.extend(robot_issues)
            derived.update(robot_derived)

        flag = worst_flag(issues)
        row = RecordResult(
            index=idx,
            original=original,
            normalized=normalized,
            quality_flag=flag,
            issues=issues,
            derived=derived,
        )
        results.append(row)
        normalized_rows.append(normalized)

    summary = summarize_dataset_quality(normalized_rows)
    return PreprocessResult(
        records=results,
        dataset_summary=summary,
        robot_rules_applied=apply_robot,
        field_alias_hits=alias_hits,
    )


def preprocess_text(
    content: str,
    *,
    filename: str = "upload.json",
    robot_mode: RobotMode = "auto",
) -> PreprocessResult:
    records = load_records_from_text(content, filename=filename)
    return preprocess_records(records, robot_mode=robot_mode)


def preprocess_bytes(
    raw: bytes,
    *,
    filename: str = "upload.json",
    robot_mode: RobotMode = "auto",
) -> PreprocessResult:
    records = load_records_from_bytes(raw, filename=filename)
    return preprocess_records(records, robot_mode=robot_mode)
