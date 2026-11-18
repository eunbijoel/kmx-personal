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

from manufacturing_agent.auth import review_auth_for_tests
from manufacturing_agent.hitl import apply_review
from manufacturing_agent.llm import resolve_ollama_model
from manufacturing_agent.rulebook.factory import get_rulebook_adapter
from manufacturing_agent.rulebook.field_dictionary_adapter import FieldDictionaryRulebookAdapter
from manufacturing_agent.rulebook.json_adapter import JsonRulebookAdapter
from manufacturing_agent.schemas import MappingCandidate, ReviewActionRequest, ReviewStatus
from manufacturing_agent.workflow import export_approved_json, run_workbench_pipeline
from main import app


client = TestClient(app)
SAMPLES = ROOT / "data" / "agent_workbench" / "samples"
AUTH = review_auth_for_tests()


@pytest.fixture(autouse=True)
def _isolate_store(tmp_path, monkeypatch):
    import manufacturing_agent.store as store

    monkeypatch.setattr(store, "STORE_DIR", tmp_path / "runs")
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "runs" / "workbench.sqlite3")
    # Keep pipeline tests on mock fixture unless a test overrides.
    monkeypatch.setenv("KMX_RULEBOOK_SOURCE", "mock")


def _bootstrap_identity(roles=None):
    response = client.post(
        "/ui/api/agent-workbench/bootstrap-identity",
        json={"subject_hint": "pytest-approver", "roles": roles or ["approver", "reviewer"]},
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_mock_rulebook_search_and_disclaimer():
    adapter = JsonRulebookAdapter()
    meta = adapter.metadata()
    assert meta["official_standard"] is False
    assert "TEST" in meta["disclaimer"] or "test" in meta["disclaimer"].lower()
    hits = adapter.search("temperature_c")
    assert hits
    assert hits[0]["concept_id"] == "RB-TEMP-001"


def test_field_dictionary_adapter_is_not_ksa_certified():
    adapter = FieldDictionaryRulebookAdapter()
    meta = adapter.metadata()
    assert meta["official_standard"] is False
    assert meta.get("ksa_certified") is False
    assert meta["rulebook_id"] == "kmx-field-dictionary"
    hits = adapter.search("temperature_c")
    assert hits
    assert hits[0]["concept_id"] == "KMX-FD-temperature_c"
    pressure = adapter.search("pressure_kpa")
    assert pressure
    assert pressure[0]["concept_id"] == "KMX-FD-pressure_kpa"


def test_factory_switches_sources(monkeypatch):
    monkeypatch.setenv("KMX_RULEBOOK_SOURCE", "field_dictionary")
    assert isinstance(get_rulebook_adapter(), FieldDictionaryRulebookAdapter)
    assert isinstance(get_rulebook_adapter("mock"), JsonRulebookAdapter)


def test_csv_json_profiling_and_mapping_with_evidence():
    raw = (SAMPLES / "workbench_bms.json").read_bytes()
    run = asyncio.run(
        run_workbench_pipeline(
            raw=raw,
            filename="workbench_bms.json",
            equipment_description="EV battery pack",
            process_description="telemetry",
            allow_llm=False,
            rulebook_source="mock",
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


def test_pipeline_uses_field_dictionary_source():
    raw = (SAMPLES / "workbench_bms.json").read_bytes()
    run = asyncio.run(
        run_workbench_pipeline(
            raw=raw,
            filename="workbench_bms.json",
            allow_llm=False,
            rulebook_source="field_dictionary",
        )
    )
    assert run.evidence.rulebook_id == "kmx-field-dictionary"
    mapped = [c for c in run.mapping.candidates if c.decision == "map"]
    assert mapped
    assert all(str(c.concept_id).startswith("KMX-FD-") for c in mapped)


def test_invented_concept_fails_validation():
    raw = (SAMPLES / "workbench_bms.json").read_bytes()
    run = asyncio.run(
        run_workbench_pipeline(raw=raw, filename="workbench_bms.json", allow_llm=False, rulebook_source="mock")
    )
    run.mapping.candidates[0].decision = "map"
    run.mapping.candidates[0].concept_id = "RB-DOES-NOT-EXIST"
    from manufacturing_agent.tools.validate import validate_mapping

    report, _ = validate_mapping(run.profile, run.mapping, JsonRulebookAdapter())
    assert report.ok is False
    assert any(i.code == "concept_not_in_rulebook" for i in report.issues)


def test_ambiguous_unit_needs_review():
    raw = (SAMPLES / "workbench_ambiguous.csv").read_bytes()
    run = asyncio.run(
        run_workbench_pipeline(
            raw=raw, filename="workbench_ambiguous.csv", allow_llm=False, rulebook_source="mock"
        )
    )
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
        run_workbench_pipeline(raw=raw, filename="workbench_bms.json", allow_llm=True, rulebook_source="mock")
    )
    assert run.mapping.llm_available is False
    assert run.mapping.llm_error
    assert run.mapping.used_rule_fallback is True
    assert run.status == "awaiting_review"


def test_llm_success_path_merges_without_inventing(monkeypatch):
    async def _ok_check():
        return {
            "available": True,
            "model_loaded": True,
            "model": "gemma3:12b",
            "preferred_model": "gemma3:12b",
            "error": None,
        }

    async def _fake_generate(prompt: str, **kwargs):
        return {
            "candidates": [
                {
                    "source_field": "temperature_c",
                    "concept_id": "RB-TEMP-001",
                    "concept_name": "ProcessTemperature",
                    "confidence": 0.91,
                    "evidence_ids": ["RB-TEMP-001"],
                    "decision": "map",
                    "rationale": "LLM confirmed temperature mapping",
                },
                {
                    "source_field": "soc_pct",
                    "concept_id": "RB-INVENTED",
                    "evidence_ids": [],
                    "decision": "map",
                    "rationale": "should be rejected",
                },
            ]
        }

    import manufacturing_agent.tools.map_semantic as mapper

    monkeypatch.setattr(mapper, "check_ollama_available", _ok_check)
    monkeypatch.setattr(mapper, "generate_json", _fake_generate)
    raw = (SAMPLES / "workbench_bms.json").read_bytes()
    run = asyncio.run(
        run_workbench_pipeline(raw=raw, filename="workbench_bms.json", allow_llm=True, rulebook_source="mock")
    )
    assert run.mapping.llm_available is True
    assert run.mapping.used_rule_fallback is False
    assert run.mapping.model_name == "gemma3:12b"
    by_field = {c.source_field: c for c in run.mapping.candidates}
    assert by_field["temperature_c"].source in {"hybrid", "llm"}
    # Invented concept without evidence must not stay mapped to RB-INVENTED
    assert by_field["soc_pct"].concept_id != "RB-INVENTED"


def test_resolve_ollama_model_prefers_installed():
    assert resolve_ollama_model(["gemma3:12b", "llama3.2"], preferred="qwen3:14b") == "gemma3:12b"
    assert resolve_ollama_model(["qwen3:14b"], preferred="qwen3:14b") == "qwen3:14b"
    assert resolve_ollama_model([], preferred="gemma3:12b") is None


def test_hitl_approve_reject_modify_and_export():
    raw = (SAMPLES / "workbench_bms.json").read_bytes()
    run = asyncio.run(
        run_workbench_pipeline(raw=raw, filename="workbench_bms.json", allow_llm=False, rulebook_source="mock")
    )
    assert run.validation.ok is True

    rejected = apply_review(
        run,
        ReviewActionRequest(action="reject", reason="not ready", actor=AUTH.actor_did),
        auth=AUTH,
    )
    assert rejected.review_status == ReviewStatus.REJECTED

    run2 = asyncio.run(
        run_workbench_pipeline(raw=raw, filename="workbench_bms.json", allow_llm=False, rulebook_source="mock")
    )
    approved = apply_review(
        run2,
        ReviewActionRequest(action="approve", reason="looks good", actor=AUTH.actor_did),
        auth=AUTH,
    )
    assert approved.review_status == ReviewStatus.APPROVED
    artifact = export_approved_json(approved)
    assert artifact["artifact_type"] == "kmx.manufacturing_agent.mapping"
    assert artifact["rulebook"]["official_standard"] is False
    assert artifact["approver_auth"]["auth_mode"] == "test_bypass"
    assert artifact["approver_auth"]["roles"] == ["approver"]

    modified = [
        MappingCandidate.model_validate({**c.model_dump(), "rationale": c.rationale + " edited"})
        for c in approved.mapping.candidates
    ]
    changed = apply_review(
        approved,
        ReviewActionRequest(
            action="modify",
            reason="tweak",
            actor=AUTH.actor_did,
            modified_candidates=modified,
        ),
        auth=AUTH,
    )
    assert changed.review_status == ReviewStatus.NEEDS_REAPPROVAL
    assert changed.approved_artifact is None


def test_reviewer_cannot_approve():
    raw = (SAMPLES / "workbench_bms.json").read_bytes()
    run = asyncio.run(
        run_workbench_pipeline(raw=raw, filename="workbench_bms.json", allow_llm=False, rulebook_source="mock")
    )
    reviewer = review_auth_for_tests(roles=("reviewer",))
    with pytest.raises(PermissionError, match="cannot perform"):
        apply_review(
            run,
            ReviewActionRequest(action="approve", reason="nope", actor=reviewer.actor_did),
            auth=reviewer,
        )


def test_api_identity_gated_review_and_regression():
    assert client.get("/health").status_code == 200
    page = client.get("/agent-workbench")
    assert page.status_code == 200
    assert "Agent Workbench" in page.text

    identity = _bootstrap_identity()
    with (SAMPLES / "workbench_bms.json").open("rb") as handle:
        response = client.post(
            "/ui/api/agent-workbench/run-upload",
            files={"file": ("workbench_bms.json", handle, "application/json")},
            data={
                "equipment_description": "pack bench",
                "process_description": "telemetry",
                "allow_llm": "false",
                "rulebook_source": "mock",
            },
        )
    assert response.status_code == 200
    run = response.json()
    run_id = run["run_id"]

    denied = client.get(f"/ui/api/agent-workbench/runs/{run_id}/export")
    assert denied.status_code == 400

    forbidden = client.post(
        f"/ui/api/agent-workbench/runs/{run_id}/review",
        json={"action": "approve", "reason": "ok", "actor": "user"},
    )
    assert forbidden.status_code == 403

    approved = client.post(
        f"/ui/api/agent-workbench/runs/{run_id}/review",
        json={
            "action": "approve",
            "reason": "ok",
            "actor": identity["actor"],
            "vc_id": identity["vc_id"],
        },
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["approved_artifact"]["approver"] == identity["actor"]
    assert approved.json()["approved_artifact"]["approver_auth"]["vc_id"] == identity["vc_id"]
    exported = client.get(f"/ui/api/agent-workbench/runs/{run_id}/export")
    assert exported.status_code == 200
    assert exported.json()["run_id"] == run_id

    assert client.get("/datasets").status_code == 200
    assert client.get("/ui/api/catalog").status_code == 200
    assert client.get("/ui/api/agent-workbench/llm-status").status_code == 200
    assert client.get("/ui/api/agent-workbench/rulebook-status").status_code == 200


def test_sensor_telemetry_mapping():
    raw = (SAMPLES / "workbench_sensors.json").read_bytes()
    run = asyncio.run(
        run_workbench_pipeline(
            raw=raw,
            filename="workbench_sensors.json",
            equipment_description="Hydraulic press line",
            process_description="Condition monitoring",
            allow_llm=False,
            rulebook_source="mock",
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


def test_sensor_mapping_with_field_dictionary():
    raw = (SAMPLES / "workbench_sensors.json").read_bytes()
    run = asyncio.run(
        run_workbench_pipeline(
            raw=raw,
            filename="workbench_sensors.json",
            allow_llm=False,
            rulebook_source="field_dictionary",
        )
    )
    by_field = {c.source_field: c for c in run.mapping.candidates}
    assert by_field["temperature_c"].concept_id == "KMX-FD-temperature_c"
    assert by_field["pressure_kpa"].concept_id == "KMX-FD-pressure_kpa"
    assert by_field["vibration_mm_s"].concept_id == "KMX-FD-vibration_mm_s"
    # equipment_id / status may abstain — dictionary may not define them
    assert by_field["equipment_id"].decision in {"abstain", "needs_review", "map"}


def test_unknown_fields_do_not_invent_concepts():
    raw = (SAMPLES / "workbench_unknown_fields.json").read_bytes()
    run = asyncio.run(
        run_workbench_pipeline(
            raw=raw, filename="workbench_unknown_fields.json", allow_llm=False, rulebook_source="mock"
        )
    )
    mapped = [c for c in run.mapping.candidates if c.decision == "map"]
    assert mapped == []
    adapter = JsonRulebookAdapter()
    for c in run.mapping.candidates:
        assert c.decision in {"abstain", "needs_review"}
        assert not c.concept_id or adapter.get_concept(c.concept_id)


def test_bad_types_fail_or_need_review():
    raw = (SAMPLES / "workbench_bad_types.csv").read_bytes()
    run = asyncio.run(
        run_workbench_pipeline(
            raw=raw, filename="workbench_bad_types.csv", allow_llm=False, rulebook_source="mock"
        )
    )
    mystery = next(c for c in run.mapping.candidates if c.source_field == "mystery_channel")
    assert mystery.decision in {"abstain", "needs_review"}
    soc = next(c for c in run.mapping.candidates if c.source_field == "soc_pct")
    temp = next(c for c in run.mapping.candidates if c.source_field == "temperature_c")
    if soc.decision == "map" or temp.decision == "map":
        assert run.validation.ok is False or any(
            i.code == "type_mismatch" for i in run.validation.issues
        )


def test_modify_revalidates_and_blocks_bad_approve():
    raw = (SAMPLES / "workbench_bms.json").read_bytes()
    run = asyncio.run(
        run_workbench_pipeline(raw=raw, filename="workbench_bms.json", allow_llm=False, rulebook_source="mock")
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
        ReviewActionRequest(
            action="modify",
            reason="inject fake",
            actor=AUTH.actor_did,
            modified_candidates=poisoned,
        ),
        auth=AUTH,
    )
    assert changed.review_status == ReviewStatus.NEEDS_REAPPROVAL
    assert changed.validation.ok is False
    assert any(i.code == "concept_not_in_rulebook" for i in changed.validation.issues)
    with pytest.raises(ValueError, match="validation"):
        apply_review(
            changed,
            ReviewActionRequest(action="approve", reason="should fail", actor=AUTH.actor_did),
            auth=AUTH,
        )


def test_hash_invalidation_via_api_and_persistence(tmp_path, monkeypatch):
    import manufacturing_agent.store as store

    monkeypatch.setattr(store, "STORE_DIR", tmp_path / "persist_runs")
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "persist_runs" / "workbench.sqlite3")

    identity = _bootstrap_identity()
    with (SAMPLES / "workbench_bms.json").open("rb") as handle:
        response = client.post(
            "/ui/api/agent-workbench/run-upload",
            files={"file": ("workbench_bms.json", handle, "application/json")},
            data={"allow_llm": "false", "rulebook_source": "mock"},
        )
    assert response.status_code == 200
    run = response.json()
    run_id = run["run_id"]
    original_hash = run["profile"]["raw_hash"]

    approved = client.post(
        f"/ui/api/agent-workbench/runs/{run_id}/review",
        json={
            "action": "approve",
            "reason": "ok",
            "actor": identity["actor"],
            "vc_id": identity["vc_id"],
        },
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["approved_artifact"]["approver"] == identity["actor"]

    from manufacturing_agent.store import load_run

    reloaded = load_run(run_id)
    assert reloaded.review_status == ReviewStatus.APPROVED
    assert reloaded.evidence.review_history

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

    with (SAMPLES / "workbench_bms.json").open("rb") as handle:
        response2 = client.post(
            "/ui/api/agent-workbench/run-upload",
            files={"file": ("workbench_bms.json", handle, "application/json")},
            data={"allow_llm": "false", "rulebook_source": "mock"},
        )
    run2 = response2.json()
    client.post(
        f"/ui/api/agent-workbench/runs/{run2['run_id']}/review",
        json={
            "action": "approve",
            "reason": "ok",
            "actor": identity["actor"],
            "vc_id": identity["vc_id"],
        },
    )
    ok_check = client.post(
        f"/ui/api/agent-workbench/runs/{run2['run_id']}/check-source-hash",
        json={"raw_hash": run2["profile"]["raw_hash"]},
    )
    assert ok_check.json()["review_status"] == ReviewStatus.APPROVED.value
    assert ok_check.json()["hash_match"] is True
    assert original_hash == run2["profile"]["raw_hash"]


def test_conflicting_mapping_candidates_need_review():
    raw = (SAMPLES / "workbench_ambiguous.csv").read_bytes()
    run = asyncio.run(
        run_workbench_pipeline(
            raw=raw, filename="workbench_ambiguous.csv", allow_llm=False, rulebook_source="mock"
        )
    )
    adapter = JsonRulebookAdapter()
    for c in run.mapping.candidates:
        if c.concept_id:
            assert adapter.get_concept(c.concept_id) is not None
        for alt in c.alternatives:
            cid = alt.get("concept_id") if isinstance(alt, dict) else None
            if cid:
                assert adapter.get_concept(cid) is not None


@pytest.mark.integration
def test_live_ollama_generate_json_path():
    """Real Ollama HTTP generate_json call (not mocked)."""
    from manufacturing_agent.llm import check_ollama_available, generate_json

    status = asyncio.run(check_ollama_available())
    if not status.get("model_loaded"):
        pytest.skip(status.get("error") or "Ollama model not available")
    assert status.get("model")
    tiny = asyncio.run(
        generate_json(
            '{"task":"ping","instructions":["Return a JSON object with boolean key ok"]}',
            timeout=120.0,
        )
    )
    assert isinstance(tiny, dict)


@pytest.mark.integration
def test_live_ollama_mapping_refine_when_fast_enough():
    """Full mapping refine against live Ollama — skips on host timeout."""
    from manufacturing_agent.llm import check_ollama_available

    status = asyncio.run(check_ollama_available())
    if not status.get("model_loaded"):
        pytest.skip(status.get("error") or "Ollama model not available")

    raw = (SAMPLES / "workbench_bms.json").read_bytes()
    run = asyncio.run(
        run_workbench_pipeline(
            raw=raw,
            filename="workbench_bms.json",
            allow_llm=True,
            rulebook_source="mock",
        )
    )
    assert run.mapping.llm_available is True
    if run.mapping.used_rule_fallback:
        err = run.mapping.llm_error or ""
        if "Timeout" in err or "timeout" in err.lower():
            pytest.skip(f"mapping refine timed out on this host: {err}")
        pytest.fail(f"LLM available but fell back: error={err!r}")
    assert run.mapping.model_name
    assert not run.mapping.llm_error
    for c in run.mapping.candidates:
        if c.concept_id:
            assert JsonRulebookAdapter().get_concept(c.concept_id) is not None
