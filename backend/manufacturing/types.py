from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any


class QualityFlag(str, Enum):
    OK = "OK"
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"
    INVALID = "INVALID"


@dataclass
class ValidationIssue:
    field: str
    message: str
    severity: QualityFlag
    rule: str = "common"

    def to_dict(self) -> dict[str, Any]:
        return {
            "field": self.field,
            "message": self.message,
            "severity": self.severity.value,
            "rule": self.rule,
        }


@dataclass
class RecordResult:
    index: int
    original: dict[str, Any]
    normalized: dict[str, Any]
    quality_flag: QualityFlag
    issues: list[ValidationIssue] = field(default_factory=list)
    derived: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "original": self.original,
            "normalized": self.normalized,
            "quality_flag": self.quality_flag.value,
            "issues": [issue.to_dict() for issue in self.issues],
            "derived": self.derived,
        }


def worst_flag(issues: list[ValidationIssue]) -> QualityFlag:
    if any(i.severity == QualityFlag.INVALID for i in issues):
        return QualityFlag.INVALID
    if any(i.severity == QualityFlag.CRITICAL for i in issues):
        return QualityFlag.CRITICAL
    if any(i.severity == QualityFlag.WARNING for i in issues):
        return QualityFlag.WARNING
    return QualityFlag.OK


def dataclass_to_dict(obj: Any) -> dict[str, Any]:
    return asdict(obj)
