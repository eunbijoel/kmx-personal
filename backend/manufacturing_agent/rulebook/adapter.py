from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class RulebookAdapter(ABC):
    """Adapter interface for rulebook backends.

    Current MVP uses a local JSON mock. Future KSA-provided materials can
    implement this same interface without changing the workflow.
    """

    @abstractmethod
    def metadata(self) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def search(self, query: str, *, limit: int = 5) -> list[dict[str, Any]]:
        raise NotImplementedError

    @abstractmethod
    def get_concept(self, concept_id: str) -> dict[str, Any] | None:
        raise NotImplementedError

    @abstractmethod
    def list_concepts(self) -> list[dict[str, Any]]:
        raise NotImplementedError
