from __future__ import annotations

from manufacturing_agent.rulebook.adapter import RulebookAdapter
from manufacturing_agent.schemas import DataProfile, RuleHit, utc_now


def retrieve_rules_for_profile(
    profile: DataProfile,
    adapter: RulebookAdapter,
    *,
    limit_per_field: int = 3,
) -> tuple[list[RuleHit], dict]:
    started = utc_now()
    hits: list[RuleHit] = []
    seen: set[str] = set()
    for field in profile.fields:
        queries = [field.field_name]
        # include equipment/process context lightly
        if profile.equipment_description:
            queries.append(f"{field.field_name} {profile.equipment_description}")
        for query in queries:
            for row in adapter.search(query, limit=limit_per_field):
                concept_id = str(row.get("concept_id"))
                key = f"{field.field_name}:{concept_id}"
                if key in seen:
                    continue
                seen.add(key)
                hits.append(
                    RuleHit(
                        concept_id=concept_id,
                        name=str(row.get("name") or ""),
                        score=float(row.get("score") or 0.0),
                        matched_on=str(row.get("matched_on") or field.field_name),
                        source_field=field.field_name,
                        aas_submodel=row.get("aas_submodel"),
                        aas_property=row.get("aas_property"),
                        unit=row.get("unit"),
                        value_type=row.get("value_type"),
                        description=str(row.get("description") or ""),
                        rulebook_id=str(row.get("rulebook_id") or ""),
                        rulebook_version=str(row.get("rulebook_version") or ""),
                        disclaimer=str(row.get("disclaimer") or ""),
                    )
                )
    finished = utc_now()
    trace = {
        "tool": "rulebook_retrieve",
        "status": "ok",
        "started_at": started,
        "finished_at": finished,
        "detail": {"hit_count": len(hits), "fields": len(profile.fields)},
    }
    return hits, trace
