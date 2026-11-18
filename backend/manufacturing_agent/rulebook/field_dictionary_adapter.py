from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from manufacturing_agent.rulebook.adapter import RulebookAdapter

ROOT_DIR = Path(__file__).resolve().parents[3]
DEFAULT_FIELD_DICTIONARY = ROOT_DIR / "data" / "control_plane" / "field_dictionary.json"

DISCLAIMER = (
    "KMX control-plane field dictionary content source. "
    "This is platform vocabulary for development — NOT an official KSA/ISO/IDTA certified standard."
)


class FieldDictionaryRulebookAdapter(RulebookAdapter):
    """RulebookAdapter backed by data/control_plane/field_dictionary.json (+ alias groups)."""

    def __init__(self, path: Path | None = None):
        self.path = path or DEFAULT_FIELD_DICTIONARY
        self._payload = _load_dictionary(self.path)
        self._concepts = _build_concepts(self._payload)

    def metadata(self) -> dict[str, Any]:
        return {
            "rulebook_id": "kmx-field-dictionary",
            "version": str(self._payload.get("version") or "0.0.0"),
            "title": "KMX Field Dictionary Content Source",
            "disclaimer": DISCLAIMER,
            "source": "kmx-control-plane/field_dictionary.json",
            "path": str(self.path),
            "official_standard": False,
            "ksa_certified": False,
            "content_kind": "platform_vocabulary",
        }

    def list_concepts(self) -> list[dict[str, Any]]:
        return [dict(c) for c in self._concepts]

    def get_concept(self, concept_id: str) -> dict[str, Any] | None:
        for concept in self._concepts:
            if concept.get("concept_id") == concept_id:
                return dict(concept)
        return None

    def search(self, query: str, *, limit: int = 5) -> list[dict[str, Any]]:
        q = (query or "").strip().lower()
        if not q:
            return []
        scored: list[tuple[float, dict[str, Any], str]] = []
        for concept in self._concepts:
            aliases = [str(concept.get("name") or "")] + [str(a) for a in concept.get("aliases") or []]
            best = 0.0
            matched_on = ""
            for alias in aliases:
                alias_l = alias.lower()
                if not alias_l:
                    continue
                if q == alias_l:
                    score = 1.0
                elif q in alias_l or alias_l in q:
                    score = 0.82
                elif _token_overlap(q, alias_l) > 0:
                    score = 0.55 + 0.2 * _token_overlap(q, alias_l)
                else:
                    continue
                if score > best:
                    best = score
                    matched_on = alias
            blob = " ".join(
                [
                    str(concept.get("description") or ""),
                    " ".join(str(t) for t in concept.get("tags") or []),
                ]
            ).lower()
            if best < 0.55 and q in blob:
                best = 0.45
                matched_on = "description/tags"
            if best > 0:
                scored.append((best, concept, matched_on))
        scored.sort(key=lambda item: item[0], reverse=True)
        meta = self.metadata()
        out: list[dict[str, Any]] = []
        for score, concept, matched_on in scored[:limit]:
            row = dict(concept)
            row["score"] = round(score, 4)
            row["matched_on"] = matched_on
            row["rulebook_id"] = meta["rulebook_id"]
            row["rulebook_version"] = meta["version"]
            row["disclaimer"] = meta["disclaimer"]
            out.append(row)
        return out


def _build_concepts(payload: dict[str, Any]) -> list[dict[str, Any]]:
    concepts: list[dict[str, Any]] = []
    seen: set[str] = set()

    for field in payload.get("fields") or []:
        field_id = str(field.get("field_id") or "").strip()
        if not field_id or field_id in seen:
            continue
        seen.add(field_id)
        semantic = field.get("semantic_concept") or {}
        value_range = field.get("value_range") or {}
        concepts.append(
            {
                "concept_id": f"KMX-FD-{field_id}",
                "name": field_id,
                "aliases": [field_id, *[str(a) for a in (field.get("aliases") or [])]],
                "description": str(field.get("description") or ""),
                "value_type": str(field.get("type") or "unknown"),
                "unit": field.get("unit"),
                "required": False,
                "constraints": {
                    k: value_range[k] for k in ("min", "max") if k in value_range
                },
                "aas_submodel": semantic.get("aas_submodel"),
                "aas_property": semantic.get("aas_property"),
                "tags": list(field.get("tags") or []),
            }
        )

    # Alias groups may introduce canonical names not present as fields (e.g. pressure_kpa).
    groups = ((payload.get("normalization_rules") or {}).get("alias_groups")) or []
    for group in groups:
        canonical = str(group.get("canonical_field") or "").strip()
        if not canonical or canonical in seen:
            # Merge extra aliases into existing concept when present
            if canonical in seen:
                concept = next(c for c in concepts if c["name"] == canonical)
                for alias in group.get("aliases") or []:
                    alias_s = str(alias)
                    if alias_s not in concept["aliases"]:
                        concept["aliases"].append(alias_s)
                if group.get("target_unit") and not concept.get("unit"):
                    concept["unit"] = group.get("target_unit")
            continue
        seen.add(canonical)
        concepts.append(
            {
                "concept_id": f"KMX-FD-{canonical}",
                "name": canonical,
                "aliases": [canonical, *[str(a) for a in (group.get("aliases") or [])]],
                "description": f"Canonical field from normalization alias group: {canonical}",
                "value_type": "number" if group.get("target_unit") else "unknown",
                "unit": group.get("target_unit"),
                "required": False,
                "constraints": {},
                "aas_submodel": None,
                "aas_property": None,
                "tags": ["alias_group"],
            }
        )
    return concepts


@lru_cache(maxsize=4)
def _load_dictionary(path_str: str | Path) -> dict[str, Any]:
    path = Path(path_str)
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _token_overlap(a: str, b: str) -> float:
    ta = {t for t in a.replace("_", " ").replace("-", " ").split() if t}
    tb = {t for t in b.replace("_", " ").replace("-", " ").split() if t}
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)
