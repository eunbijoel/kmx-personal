from __future__ import annotations

import json
from typing import Any

from manufacturing_agent.llm import PROMPT_VERSION, check_ollama_available, generate_json
from manufacturing_agent.schemas import (
    DataProfile,
    MappingCandidate,
    MappingProposal,
    RuleHit,
    utc_now,
)


async def propose_mappings(
    profile: DataProfile,
    rule_hits: list[RuleHit],
    *,
    allow_llm: bool = True,
) -> tuple[MappingProposal, dict]:
    started = utc_now()
    # Group retrieve hits by the field they were queried for (avoids cross-field leakage).
    by_field: dict[str, list[RuleHit]] = {f.field_name: [] for f in profile.fields}
    for hit in rule_hits:
        key = hit.source_field
        if key and key in by_field:
            by_field[key].append(hit)
        else:
            # Legacy hits without source_field: attach only on strong lexical match
            for field_name in by_field:
                fl = field_name.lower()
                if fl == hit.matched_on.lower() or fl in hit.matched_on.lower() or hit.matched_on.lower() in fl:
                    by_field[field_name].append(hit)
    for field_name, hits in by_field.items():
        by_field[field_name] = sorted(hits, key=lambda h: h.score, reverse=True)[:3]

    rule_candidates: list[MappingCandidate] = []
    uncertain: list[str] = []
    for field in profile.fields:
        hits = by_field.get(field.field_name) or []
        if not hits:
            uncertain.append(field.field_name)
            rule_candidates.append(
                MappingCandidate(
                    source_field=field.field_name,
                    decision="abstain",
                    confidence=0.0,
                    rationale="No rulebook concept matched this field.",
                    source="rule",
                )
            )
            continue
        top = hits[0]
        alts = [
            {
                "concept_id": h.concept_id,
                "concept_name": h.name,
                "score": h.score,
                "aas_submodel": h.aas_submodel,
                "aas_property": h.aas_property,
            }
            for h in hits[1:3]
        ]
        decision = "map" if top.score >= 0.8 else "needs_review"
        if field.missing_ratio > 0.5:
            decision = "needs_review"
            uncertain.append(field.field_name)
        # Unit unclear heuristic: field name lacks unit suffix and concept has unit
        unit_assumption = None
        unit_markers = ("_c", "_w", "_pct", "_ms", "mm_s", "_kpa", "kpa", "temp", "pressure", "vib")
        if top.unit and not any(suf in field.field_name.lower() for suf in unit_markers):
            if decision == "map":
                decision = "needs_review"
            unit_assumption = f"assumed unit {top.unit} from rulebook concept"
            if field.field_name not in uncertain:
                uncertain.append(field.field_name)
        rule_candidates.append(
            MappingCandidate(
                source_field=field.field_name,
                concept_id=top.concept_id,
                concept_name=top.name,
                aas_submodel=top.aas_submodel,
                aas_property=top.aas_property,
                confidence=float(top.score),
                evidence_ids=[top.concept_id],
                alternatives=alts,
                rationale=f"Matched rulebook concept via '{top.matched_on}'.",
                decision=decision,
                unit_assumption=unit_assumption,
                source="rule",
            )
        )

    llm_available = False
    llm_error = None
    used_fallback = True
    model_name = None
    candidates = rule_candidates

    if allow_llm:
        status = await check_ollama_available()
        llm_available = bool(status.get("available") and status.get("model_loaded"))
        if not llm_available:
            llm_error = status.get("error") or "Ollama unavailable"
        else:
            try:
                llm_payload = await _llm_refine(profile, rule_hits, rule_candidates)
                candidates = _merge_llm(rule_candidates, llm_payload)
                used_fallback = False
                model_name = str(status.get("model") or "")
            except Exception as exc:  # noqa: BLE001
                llm_error = str(exc).strip() or f"{exc.__class__.__name__}: LLM refine failed"
                candidates = rule_candidates
                used_fallback = True
                model_name = None

    proposal = MappingProposal(
        candidates=candidates,
        uncertain_fields=sorted(set(uncertain + [c.source_field for c in candidates if c.decision != "map"])),
        model_name=model_name,
        prompt_version=PROMPT_VERSION,
        llm_available=llm_available,
        llm_error=llm_error,
        used_rule_fallback=used_fallback,
    )
    finished = utc_now()
    trace = {
        "tool": "semantic_mapping",
        "status": "ok" if llm_error is None or used_fallback else "error",
        "started_at": started,
        "finished_at": finished,
        "detail": {
            "llm_available": llm_available,
            "llm_error": llm_error,
            "used_rule_fallback": used_fallback,
            "candidate_count": len(candidates),
        },
    }
    return proposal, trace


async def _llm_refine(
    profile: DataProfile,
    rule_hits: list[RuleHit],
    rule_candidates: list[MappingCandidate],
) -> dict[str, Any]:
    # Keep prompt compact for local models (e.g. gemma3:12b).
    prompt = {
        "task": "semantic_mapping",
        "instructions": [
            "Return JSON with key candidates: array of objects.",
            "Each object keys: source_field, concept_id, concept_name, aas_submodel, aas_property, confidence, evidence_ids, decision, rationale, unit_assumption.",
            "decision must be one of map|needs_review|abstain.",
            "Only use concept_id values present in retrieved_rules. Do not invent concepts.",
            "If unsure, use needs_review or abstain.",
        ],
        "equipment_description": profile.equipment_description,
        "process_description": profile.process_description,
        "fields": [
            {
                "field_name": f.field_name,
                "inferred_type": f.inferred_type,
                "sample_values": f.sample_values[:3],
            }
            for f in profile.fields
        ],
        "retrieved_rules": [
            {
                "concept_id": h.concept_id,
                "name": h.name,
                "score": h.score,
                "matched_on": h.matched_on,
                "source_field": h.source_field,
                "aas_submodel": h.aas_submodel,
                "aas_property": h.aas_property,
                "unit": h.unit,
            }
            for h in rule_hits[:24]
        ],
        "rule_baseline": [
            {
                "source_field": c.source_field,
                "concept_id": c.concept_id,
                "decision": c.decision,
                "confidence": c.confidence,
                "evidence_ids": c.evidence_ids,
            }
            for c in rule_candidates
        ],
    }
    return await generate_json(json.dumps(prompt, ensure_ascii=False), timeout=180.0)


def _merge_llm(
    baseline: list[MappingCandidate],
    llm_payload: dict[str, Any],
) -> list[MappingCandidate]:
    rows = llm_payload.get("candidates")
    if not isinstance(rows, list):
        raise RuntimeError("LLM payload missing candidates array")
    by_field = {c.source_field: c for c in baseline}
    allowed_ids = {
        evid
        for c in baseline
        for evid in c.evidence_ids
    } | {c.concept_id for c in baseline if c.concept_id}

    out: list[MappingCandidate] = []
    seen: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        source_field = str(row.get("source_field") or "")
        if not source_field or source_field in seen:
            continue
        seen.add(source_field)
        base = by_field.get(source_field)
        concept_id = row.get("concept_id")
        evidence_ids = [str(x) for x in (row.get("evidence_ids") or [])]
        # Reject invented concepts
        if concept_id and str(concept_id) not in allowed_ids and not evidence_ids:
            decision = "abstain"
            rationale = "Rejected LLM concept not present in retrieved rulebook evidence."
            concept_id = None
        else:
            decision = str(row.get("decision") or "needs_review")
            if decision not in {"map", "needs_review", "abstain"}:
                decision = "needs_review"
            rationale = str(row.get("rationale") or (base.rationale if base else ""))
        out.append(
            MappingCandidate(
                source_field=source_field,
                concept_id=str(concept_id) if concept_id else (base.concept_id if base else None),
                concept_name=row.get("concept_name") or (base.concept_name if base else None),
                aas_submodel=row.get("aas_submodel") or (base.aas_submodel if base else None),
                aas_property=row.get("aas_property") or (base.aas_property if base else None),
                confidence=float(row.get("confidence") or (base.confidence if base else 0.0)),
                evidence_ids=evidence_ids or (base.evidence_ids if base else []),
                alternatives=base.alternatives if base else [],
                rationale=rationale,
                decision=decision,  # type: ignore[arg-type]
                unit_assumption=row.get("unit_assumption") or (base.unit_assumption if base else None),
                source="hybrid" if base else "llm",
            )
        )
    # ensure all baseline fields remain
    for field, base in by_field.items():
        if field not in seen:
            out.append(base)
    return out
