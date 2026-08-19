from __future__ import annotations

import json
import urllib.request
from typing import Any

from .base import RankedCandidate, RetrievalCandidate


class DenseRetrievalError(RuntimeError):
    pass


class DenseRetriever:
    """OpenAI-compatible embeddings provider; the embedding model stays outside API."""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        timeout_seconds: float = 5.0,
        batch_size: int = 32,
    ):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout_seconds = timeout_seconds
        self.batch_size = max(1, int(batch_size))

    def _embed(self, texts: list[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for start in range(0, len(texts), self.batch_size):
            batch = texts[start : start + self.batch_size]
            request = urllib.request.Request(
                f"{self.base_url}/embeddings",
                data=json.dumps({"model": self.model, "input": batch}, ensure_ascii=False).encode("utf-8"),
                headers={"Content-Type": "application/json", **({"Authorization": f"Bearer {self.api_key}"} if self.api_key else {})},
                method="POST",
            )
            try:
                with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                    payload = json.loads(response.read().decode("utf-8"))
            except Exception as exc:
                raise DenseRetrievalError(f"Dense retrieval request failed: {exc}") from exc
            data = payload.get("data") if isinstance(payload, dict) else None
            if not isinstance(data, list) or len(data) != len(batch):
                raise DenseRetrievalError("Dense retrieval response has invalid data length.")
            try:
                vectors.extend(list(map(float, item["embedding"])) for item in sorted(data, key=lambda item: int(item.get("index", 0))))
            except (KeyError, TypeError, ValueError) as exc:
                raise DenseRetrievalError("Dense retrieval response has invalid embeddings.") from exc
        return vectors

    def search(self, query: str, candidates: list[RetrievalCandidate], top_k: int = 30) -> list[RankedCandidate]:
        if not candidates or not query:
            return []
        vectors = self._embed([query, *[candidate.text for candidate in candidates]])
        query_vector = vectors[0]
        scored = [(self._cosine(query_vector, vector), candidate) for candidate, vector in zip(candidates, vectors[1:])]
        scored.sort(key=lambda item: (-item[0], item[1].id))
        return [RankedCandidate(candidate, score, index, "dense") for index, (score, candidate) in enumerate(scored[: max(0, top_k)], 1)]

    @staticmethod
    def _cosine(left: list[float], right: list[float]) -> float:
        numerator = sum(a * b for a, b in zip(left, right))
        left_norm = sum(a * a for a in left) ** 0.5
        right_norm = sum(b * b for b in right) ** 0.5
        return numerator / (left_norm * right_norm) if left_norm and right_norm else 0.0
