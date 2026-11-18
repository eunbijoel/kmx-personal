from __future__ import annotations

import os

from manufacturing_agent.rulebook.adapter import RulebookAdapter
from manufacturing_agent.rulebook.field_dictionary_adapter import FieldDictionaryRulebookAdapter
from manufacturing_agent.rulebook.json_adapter import JsonRulebookAdapter

# field_dictionary = KMX platform vocabulary (not KSA-certified).
# mock = synthetic JSON fixture for isolated MVP demos.
DEFAULT_RULEBOOK_SOURCE = "field_dictionary"


def get_rulebook_adapter(source: str | None = None) -> RulebookAdapter:
    chosen = (source or os.getenv("KMX_RULEBOOK_SOURCE") or DEFAULT_RULEBOOK_SOURCE).strip().lower()
    if chosen in {"mock", "json", "mock_json"}:
        return JsonRulebookAdapter()
    if chosen in {"field_dictionary", "standard", "kmx", "control_plane"}:
        return FieldDictionaryRulebookAdapter()
    raise ValueError(
        f"Unknown KMX_RULEBOOK_SOURCE={chosen!r}; use 'field_dictionary' or 'mock'"
    )
