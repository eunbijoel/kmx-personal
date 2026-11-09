from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from manufacturing.io_loader import LoadError, load_records_from_path
from manufacturing.preprocess import preprocess_bytes, preprocess_records
from manufacturing.types import QualityFlag


FIXTURES = Path(__file__).resolve().parent / "fixtures"


def test_load_csv_and_normalize_aliases():
    records = load_records_from_path(FIXTURES / "sample_manufacturing.csv")
    result = preprocess_records(records, robot_mode="never")
    assert len(result.records) == 2
    first = result.records[0].normalized
    assert "temperature_c" in first
    assert first["temperature_c"] == 36.5
    assert "temp" in result.field_alias_hits


def test_bms_range_quality_flags():
    raw = (FIXTURES / "sample_bms.json").read_bytes()
    result = preprocess_bytes(raw, filename="sample_bms.json", robot_mode="never")
    assert len(result.records) == 2
    assert result.records[0].quality_flag in {QualityFlag.OK, QualityFlag.WARNING}
    # soc=150 is out of 0..100 range
    assert result.records[1].quality_flag == QualityFlag.CRITICAL
    assert any(i.field == "soc_pct" for i in result.records[1].issues)


def test_robot_rules_auto_detect():
    raw = (FIXTURES / "sample_robot.json").read_bytes()
    result = preprocess_bytes(raw, filename="sample_robot.json", robot_mode="auto")
    assert result.robot_rules_applied is True
    assert result.records[0].quality_flag in {QualityFlag.OK, QualityFlag.WARNING}
    assert result.records[1].quality_flag == QualityFlag.CRITICAL
    assert "yield_rate" in result.records[0].derived


def test_invalid_json_raises():
    try:
        preprocess_bytes(b"not-json", filename="bad.json")
        assert False, "expected LoadError"
    except LoadError:
        pass
