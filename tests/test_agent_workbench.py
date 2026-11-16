from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from fastapi.testclient import TestClient

from manufacturing_agent.hitl import apply_review
from manufacturing_agent.rulebook.json_adapter import JsonRulebookAdapter
from manufacturing_agent.schemas import MappingCandidate, ReviewActionRequest, ReviewStatus
from manufacturing_agent.store import save_run
from manufacturing_agent.workflow import export_approved_json, run_workbench_pipeline
from main import app


client = TestClient(app)
SAMPLES = ROOT / "data" / "agent_workbench" / "samples"


@pytest.fixture(autouse=True)
def _isolate_store(tmp_path, monkeypatch):
    import manufacturing_agent.store as store

    monkeypatch.setattr(store, "STORE_DIR", tmp_path / "runs")
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "runs" / "workbench.sqlite3")


def test_rulebook_search_and_disclaimer():
    adapter = JsonRulebookAdapter()
    meta = adapter.metadata()
    assert meta["official_standard"] is False
    assert "TEST" in meta["disclaimer"] or "test" in meta["disclaimer"].lower()
    hits = adapter.search("temperature_c")
    assert hits
    assert hits[0]["concept_id"] == "RB-TEMP-001"


def test_csv_json_profiling_and_mapping_with_evidence():
    raw = (SAMPLES / "workbench_bms.json").read_bytes()
    run = asyncio.run(
        run_workbench_pipeline(
            raw=raw,
            filename="workbench_bms.json",
            equipment_description="EV battery pack",
            process_description="telemetry",
            allow_llm=False,
        )
    )
    assert run.profile.record_count == 2
    assert run.profile.fields
    assert run.retrieved_rules
    mapped = [c for c in run.mapping.candidates if c.decision == "map"]
    assert mapped
    assert all(c.evidence_ids for c in mapped)
    assert run.evidence.rulebook_id
    assert run.mapping.used_rule_fallback is True


def test_invented_concept_fails_validation():
    raw = (SAMPLES / "workbench_bms.json").read_bytes()
    run = asyncio.run(
        run_workbench_pipeline(raw=raw, filename="workbench_bms.json", allow_llm=False)
    )
    # Inject fake mapped concept
    run.mapping.candidates[0].decision = "map"
    run.mapping.candidates[0].concept_id = "RB-DOES-NOT-EXIST"
    from manufacturing_agent.rulebook.json_adapter import JsonRulebookAdapter
    from manufacturing_agent.tools.validate import validate_mapping

    report, _ = validate_mapping(run.profile, run.mapping, JsonRulebookAdapter())
    assert report.ok is False
    assert any(i.code == "concept_not_in_rulebook" for i in report.issues)


def test_ambiguous_unit_needs_review():
    raw = (SAMPLES / "workbench_ambiguous.csv").read_bytes()
    run = asyncio.run(
        run_workbench_pipeline(raw=raw, filename="workbench_ambiguous.csv", allow_llm=False)
    )
    # mystery_signal should abstain/needs_review; temp_raw may need review for unit
    decisions = {c.source_field: c.decision for c in run.mapping.candidates}
    assert decisions.get("mystery_signal") in {"abstain", "needs_review"}
    assert "mystery_signal" in run.mapping.uncertain_fields or decisions["mystery_signal"] != "map"


def test_llm_failure_is_safe(monkeypatch):
    async def _fail_check():
        return {"available": False, "model_loaded": False, "model": "x", "error": "connection refused"}

    import manufacturing_agent.tools.map_semantic as mapper

    monkeypatch.setattr(mapper, "check_ollama_available", _fail_check)
    raw = (SAMPLES / "workbench_bms.json").read_bytes()
    run = asyncio.run(
        run_workbench_pipeline(raw=raw, filename="workbench_bms.json", allow_llm=True)
    )
    assert run.mapping.llm_available is False
    assert run.mapping.llm_error
    assert run.mapping.used_rule_fallback is True
    assert run.status == "awaiting_review"


def test_hitl_approve_reject_modify_and_export():
    raw = (SAMPLES / "workbench_bms.json").read_bytes()
    run = asyncio.run(
        run_workbench_pipeline(raw=raw, filename="workbench_bms.json", allow_llm=False)
    )
    # Ensure validation ok for approve path: drop FAIL by keeping rule results
    assert run.validation.ok is True

    rejected = apply_review(run, ReviewActionRequest(action="reject", reason="not ready"))
    assert rejected.review_status == ReviewStatus.REJECTED

    run2 = asyncio.run(
        run_workbench_pipeline(raw=raw, filename="workbench_bms.json", allow_llm=False)
    )
    approved = apply_review(run2, ReviewActionRequest(action="approve", reason="looks good"))
    assert approved.review_status == ReviewStatus.APPROVED
    artifact = export_approved_json(approved)
    assert artifact["artifact_type"] == "kmx.manufacturing_agent.mapping"
    assert artifact["rulebook"]["official_standard"] is False

    # modify after approval requires reapproval
    modified = [
        MappingCandidate.model_validate({**c.model_dump(), "rationale": c.rationale + " edited"})
        for c in approved.mapping.candidates
    ]
    changed = apply_review(
        approved,
        ReviewActionRequest(action="modify", reason="tweak", modified_candidates=modified),
    )
    assert changed.review_status == ReviewStatus.NEEDS_REAPPROVAL
    assert changed.approved_artifact is None


def test_api_and_regression_health():
    assert client.get("/health").status_code == 200
    page = client.get("/agent-workbench")
    assert page.status_code == 200
    assert "Agent Workbench" in page.text

    with (SAMPLES / "workbench_bms.json").open("rb") as handle:
        response = client.post(
            "/ui/api/agent-workbench/run-upload",
            files={"file": ("workbench_bms.json", handle, "application/json")},
            data={
                "equipment_description": "pack bench",
                "process_description": "telemetry",
                "allow_llm": "false",
            },
        )
    assert response.status_code == 200
    run = response.json()
    run_id = run["run_id"]

    denied = client.get(f"/ui/api/agent-workbench/runs/{run_id}/export")
    assert denied.status_code == 400

    approved = client.post(
        f"/ui/api/agent-workbench/runs/{run_id}/review",
        json={"action": "approve", "reason": "ok"},
    )
    assert approved.status_code == 200
    exported = client.get(f"/ui/api/agent-workbench/runs/{run_id}/export")
    assert exported.status_code == 200
    assert exported.json()["run_id"] == run_id

    # existing UI APIs still respond
    assert client.get("/datasets").status_code == 200
    assert client.get("/ui/api/catalog").status_code == 200
