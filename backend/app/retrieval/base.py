from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(frozen=True)
class RetrievalCandidate:
    id: str
    text: str
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RankedCandidate:
    candidate: RetrievalCandidate
    score: float
    rank: int
    source: str


class Retriever(Protocol):
    def search(self, query: str, candidates: list[RetrievalCandidate], top_k: int) -> list[RankedCandidate]: ...
