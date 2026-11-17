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


def test_sensor_telemetry_mapping():
    raw = (SAMPLES / "workbench_sensors.json").read_bytes()
    run = asyncio.run(
        run_workbench_pipeline(
            raw=raw,
            filename="workbench_sensors.json",
            equipment_description="Hydraulic press line",
            process_description="Condition monitoring",
            allow_llm=False,
        )
    )
    by_field = {c.source_field: c for c in run.mapping.candidates}
    assert by_field["temperature_c"].decision == "map"
    assert by_field["temperature_c"].concept_id == "RB-TEMP-001"
    assert by_field["pressure_kpa"].decision == "map"
    assert by_field["pressure_kpa"].concept_id == "RB-PRESS-001"
    assert by_field["vibration_mm_s"].decision == "map"
    assert by_field["vibration_mm_s"].concept_id == "RB-VIB-001"
    assert all(c.evidence_ids for c in run.mapping.candidates if c.decision == "map")
    assert run.evidence.raw_hash == run.profile.raw_hash
    assert run.evidence.rulebook_version


def test_unknown_fields_do_not_invent_concepts():
    raw = (SAMPLES / "workbench_unknown_fields.json").read_bytes()
    run = asyncio.run(
        run_workbench_pipeline(raw=raw, filename="workbench_unknown_fields.json", allow_llm=False)
    )
    mapped = [c for c in run.mapping.candidates if c.decision == "map"]
    assert mapped == []
    for c in run.mapping.candidates:
        assert c.decision in {"abstain", "needs_review"}
        assert not c.concept_id or JsonRulebookAdapter().get_concept(c.concept_id)


def test_bad_types_fail_or_need_review():
    raw = (SAMPLES / "workbench_bad_types.csv").read_bytes()
    run = asyncio.run(
        run_workbench_pipeline(raw=raw, filename="workbench_bad_types.csv", allow_llm=False)
    )
    # soc_pct / temperature_c may map but type check should fail if inferred as string
    soc = next(c for c in run.mapping.candidates if c.source_field == "soc_pct")
    temp = next(c for c in run.mapping.candidates if c.source_field == "temperature_c")
    mystery = next(c for c in run.mapping.candidates if c.source_field == "mystery_channel")
    assert mystery.decision in {"abstain", "needs_review"}
    if soc.decision == "map" or temp.decision == "map":
        assert run.validation.ok is False or any(
            i.code == "type_mismatch" for i in run.validation.issues
        )


def test_modify_revalidates_and_blocks_bad_approve():
    raw = (SAMPLES / "workbench_bms.json").read_bytes()
    run = asyncio.run(
        run_workbench_pipeline(raw=raw, filename="workbench_bms.json", allow_llm=False)
    )
    assert run.validation.ok is True
    poisoned = [
        MappingCandidate.model_validate(
            {
                **c.model_dump(),
                "decision": "map",
                "concept_id": "RB-FAKE-999",
                "evidence_ids": [],
            }
        )
        for c in run.mapping.candidates
    ]
    changed = apply_review(
        run,
        ReviewActionRequest(action="modify", reason="inject fake", actor="qa", modified_candidates=poisoned),
    )
    assert changed.review_status == ReviewStatus.NEEDS_REAPPROVAL
    assert changed.validation.ok is False
    assert any(i.code == "concept_not_in_rulebook" for i in changed.validation.issues)
    with pytest.raises(ValueError, match="validation"):
        apply_review(changed, ReviewActionRequest(action="approve", reason="should fail", actor="qa"))


def test_empty_actor_rejected():
    raw = (SAMPLES / "workbench_bms.json").read_bytes()
    run = asyncio.run(
        run_workbench_pipeline(raw=raw, filename="workbench_bms.json", allow_llm=False)
    )
    with pytest.raises(ValueError, match="actor"):
        apply_review(run, ReviewActionRequest(action="approve", reason="ok", actor="  "))


def test_hash_invalidation_via_api_and_persistence(tmp_path, monkeypatch):
    import manufacturing_agent.store as store

    monkeypatch.setattr(store, "STORE_DIR", tmp_path / "persist_runs")
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "persist_runs" / "workbench.sqlite3")

    with (SAMPLES / "workbench_bms.json").open("rb") as handle:
        response = client.post(
            "/ui/api/agent-workbench/run-upload",
            files={"file": ("workbench_bms.json", handle, "application/json")},
            data={"allow_llm": "false"},
        )
    assert response.status_code == 200
    run = response.json()
    run_id = run["run_id"]
    original_hash = run["profile"]["raw_hash"]

    approved = client.post(
        f"/ui/api/agent-workbench/runs/{run_id}/review",
        json={"action": "approve", "reason": "ok", "actor": "operator-1"},
    )
    assert approved.status_code == 200
    assert approved.json()["approved_artifact"]["approver"] == "operator-1"

    # Reload from disk (simulates process restart)
    from manufacturing_agent.store import load_run

    reloaded = load_run(run_id)
    assert reloaded.review_status == ReviewStatus.APPROVED
    assert reloaded.evidence.review_history

    # Hash mismatch → needs reapproval; export blocked
    check = client.post(
        f"/ui/api/agent-workbench/runs/{run_id}/check-source-hash",
        json={"raw_hash": "0" * 64},
    )
    assert check.status_code == 200
    body = check.json()
    assert body["review_status"] == ReviewStatus.NEEDS_REAPPROVAL.value
    assert body["hash_match"] is False
    assert body["approved_artifact"] is False
    denied = client.get(f"/ui/api/agent-workbench/runs/{run_id}/export")
    assert denied.status_code == 400

    # Matching hash leaves approval intact when still approved on a fresh run
    with (SAMPLES / "workbench_bms.json").open("rb") as handle:
        response2 = client.post(
            "/ui/api/agent-workbench/run-upload",
            files={"file": ("workbench_bms.json", handle, "application/json")},
            data={"allow_llm": "false"},
        )
    run2 = response2.json()
    client.post(
        f"/ui/api/agent-workbench/runs/{run2['run_id']}/review",
        json={"action": "approve", "reason": "ok", "actor": "operator-1"},
    )
    ok_check = client.post(
        f"/ui/api/agent-workbench/runs/{run2['run_id']}/check-source-hash",
        json={"raw_hash": run2["profile"]["raw_hash"]},
    )
    assert ok_check.json()["review_status"] == ReviewStatus.APPROVED.value
    assert ok_check.json()["hash_match"] is True
    assert original_hash == run2["profile"]["raw_hash"]


def test_conflicting_mapping_candidates_need_review():
    """Ambiguous alias collision: temp_raw has no exact alias; mystery has none."""
    raw = (SAMPLES / "workbench_ambiguous.csv").read_bytes()
    run = asyncio.run(
        run_workbench_pipeline(raw=raw, filename="workbench_ambiguous.csv", allow_llm=False)
    )
    # Alternatives may exist for near matches; mapped concepts must still be in rulebook
    adapter = JsonRulebookAdapter()
    for c in run.mapping.candidates:
        if c.concept_id:
            assert adapter.get_concept(c.concept_id) is not None
        for alt in c.alternatives:
            cid = alt.get("concept_id") if isinstance(alt, dict) else None
            if cid:
                assert adapter.get_concept(cid) is not None
