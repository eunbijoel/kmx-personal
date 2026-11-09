from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from manufacturing.types import QualityFlag, ValidationIssue

ROBOT_SIGNAL_FIELDS = {
    "robot_id",
    "line_id",
    "station_id",
    "cycle_time_ms",
    "power_watts",
    "program_name",
    "status",
    "good_parts",
    "reject_parts",
    "joint_positions_deg",
    "pose",
}


@dataclass
class RobotThresholds:
    temp_warn_c: float = 55.0
    temp_critical_c: float = 70.0
    vibration_warn_mm_s: float = 3.0
    vibration_critical_mm_s: float = 6.0
    cycle_time_min_ms: float = 500.0
    cycle_time_max_ms: float = 30_000.0
    power_max_w: float = 2_000.0
    reject_rate_warn: float = 0.05
    reject_rate_critical: float = 0.15


def looks_like_robot_record(record: dict[str, Any]) -> bool:
    keys = set(record.keys())
    required = {"robot_id", "cycle_time_ms", "power_watts", "status"}
    return required.issubset(keys)


def evaluate_robot_quality(
    record: dict[str, Any],
    *,
    thresholds: RobotThresholds | None = None,
) -> tuple[list[ValidationIssue], dict[str, Any]]:
    """Robot-specific quality rules adapted from eunbi-catena TelemetryPreprocessor."""
    t = thresholds or RobotThresholds()
    issues: list[ValidationIssue] = []
    derived: dict[str, Any] = {}

    status_raw = str(record.get("status", "")).upper()
    status = status_raw if status_raw in {"RUNNING", "IDLE", "ERROR", "STOP", "UNKNOWN"} else "UNKNOWN"
    if status == "UNKNOWN" and record.get("status") not in (None, ""):
        issues.append(
            ValidationIssue(
                field="status",
                message=f"unknown status '{record.get('status')}' mapped to UNKNOWN",
                severity=QualityFlag.WARNING,
                rule="robot.status",
            )
        )
    derived["status_normalized"] = status

    good = _int(record.get("good_parts"), 0)
    reject = _int(record.get("reject_parts"), 0)
    total = good + reject
    yield_rate = (good / total) if total > 0 else 0.0
    derived["yield_rate"] = round(yield_rate, 4)

    temp = _float(record.get("temperature_c"))
    if temp is not None:
        if temp > t.temp_critical_c:
            issues.append(
                ValidationIssue(
                    "temperature_c",
                    f"temperature {temp}°C exceeds critical {t.temp_critical_c}°C",
                    QualityFlag.CRITICAL,
                    "robot.temp",
                )
            )
        elif temp > t.temp_warn_c:
            issues.append(
                ValidationIssue(
                    "temperature_c",
                    f"temperature {temp}°C exceeds warn {t.temp_warn_c}°C",
                    QualityFlag.WARNING,
                    "robot.temp",
                )
            )

    vib = _float(record.get("vibration_mm_s"))
    if vib is not None:
        if vib > t.vibration_critical_mm_s:
            issues.append(
                ValidationIssue(
                    "vibration_mm_s",
                    f"vibration {vib}mm/s exceeds critical {t.vibration_critical_mm_s}",
                    QualityFlag.CRITICAL,
                    "robot.vibration",
                )
            )
        elif vib > t.vibration_warn_mm_s:
            issues.append(
                ValidationIssue(
                    "vibration_mm_s",
                    f"vibration {vib}mm/s exceeds warn {t.vibration_warn_mm_s}",
                    QualityFlag.WARNING,
                    "robot.vibration",
                )
            )

    cycle = _float(record.get("cycle_time_ms"))
    if cycle is not None:
        if cycle < t.cycle_time_min_ms or cycle > t.cycle_time_max_ms:
            issues.append(
                ValidationIssue(
                    "cycle_time_ms",
                    f"cycle_time_ms {cycle} outside [{t.cycle_time_min_ms}, {t.cycle_time_max_ms}]",
                    QualityFlag.WARNING,
                    "robot.cycle_time",
                )
            )

    power = _float(record.get("power_watts"))
    if power is not None and power > t.power_max_w:
        issues.append(
            ValidationIssue(
                "power_watts",
                f"power {power}W exceeds max {t.power_max_w}W",
                QualityFlag.WARNING,
                "robot.power",
            )
        )

    if total > 0:
        rate = reject / total
        if rate > t.reject_rate_critical:
            issues.append(
                ValidationIssue(
                    "reject_parts",
                    f"reject rate {rate:.1%} exceeds critical {t.reject_rate_critical:.1%}",
                    QualityFlag.CRITICAL,
                    "robot.reject_rate",
                )
            )
        elif rate > t.reject_rate_warn:
            issues.append(
                ValidationIssue(
                    "reject_parts",
                    f"reject rate {rate:.1%} exceeds warn {t.reject_rate_warn:.1%}",
                    QualityFlag.WARNING,
                    "robot.reject_rate",
                )
            )

    alarms = record.get("alarms") or []
    if isinstance(alarms, list) and alarms:
        issues.append(
            ValidationIssue(
                "alarms",
                f"{len(alarms)} active alarms",
                QualityFlag.WARNING,
                "robot.alarms",
            )
        )

    if status == "ERROR":
        issues.append(
            ValidationIssue("status", "robot ERROR status", QualityFlag.CRITICAL, "robot.status")
        )
    elif status == "STOP":
        issues.append(
            ValidationIssue("status", "robot STOP status", QualityFlag.WARNING, "robot.status")
        )

    return issues, derived


def _float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default
