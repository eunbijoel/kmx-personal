from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from manufacturing_agent.rulebook.adapter import RulebookAdapter

ROOT_DIR = Path(__file__).resolve().parents[3]
DEFAULT_RULEBOOK = (
    ROOT_DIR / "data" / "agent_workbench" / "rulebooks" / "mock_manufacturing_rulebook_v1.json"
)


class JsonRulebookAdapter(RulebookAdapter):
    def __init__(self, path: Path | None = None):
        self.path = path or DEFAULT_RULEBOOK
        self._payload = _load_rulebook(self.path)

    def metadata(self) -> dict[str, Any]:
        return {
            "rulebook_id": self._payload.get("rulebook_id"),
            "version": self._payload.get("version"),
            "title": self._payload.get("title"),
            "disclaimer": self._payload.get("disclaimer"),
            "source": self._payload.get("source"),
            "path": str(self.path),
            "official_standard": False,
        }

    def list_concepts(self) -> list[dict[str, Any]]:
        return list(self._payload.get("concepts") or [])

    def get_concept(self, concept_id: str) -> dict[str, Any] | None:
        for concept in self.list_concepts():
            if concept.get("concept_id") == concept_id:
                return concept
        return None

    def search(self, query: str, *, limit: int = 5) -> list[dict[str, Any]]:
        q = (query or "").strip().lower()
        if not q:
            return []
        scored: list[tuple[float, dict[str, Any], str]] = []
        for concept in self.list_concepts():
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
            # soft match on description/tags
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
        out: list[dict[str, Any]] = []
        meta = self.metadata()
        for score, concept, matched_on in scored[:limit]:
            row = dict(concept)
            row["score"] = round(score, 4)
            row["matched_on"] = matched_on
            row["rulebook_id"] = meta["rulebook_id"]
            row["rulebook_version"] = meta["version"]
            row["disclaimer"] = meta["disclaimer"]
            out.append(row)
        return out


@lru_cache(maxsize=4)
def _load_rulebook(path_str: str | Path) -> dict[str, Any]:
    path = Path(path_str)
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _token_overlap(a: str, b: str) -> float:
    ta = {t for t in a.replace("_", " ").replace("-", " ").split() if t}
    tb = {t for t in b.replace("_", " ").replace("-", " ").split() if t}
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)
