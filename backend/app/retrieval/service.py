from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .base import RankedCandidate, RetrievalCandidate
from .bm25 import BM25Retriever
from .dense import DenseRetriever, DenseRetrievalError
from .rrf import reciprocal_rank_fusion


@dataclass(frozen=True)
class RetrievalResult:
    candidates: list[RankedCandidate]
    metadata: dict[str, Any]


class RetrievalService:
    def __init__(self, *, mode: str = "bm25", bm25_top_k: int = 30, dense_top_k: int = 30, rrf_k: int = 60, dense: DenseRetriever | None = None):
        self.mode = mode if mode in {"bm25", "hybrid"} else "bm25"
        self.bm25_top_k = max(0, int(bm25_top_k))
        self.dense_top_k = max(0, int(dense_top_k))
        self.rrf_k = max(1, int(rrf_k))
        self.bm25 = BM25Retriever()
        self.dense = dense

    def search(self, query: str, candidates: list[RetrievalCandidate]) -> RetrievalResult:
        bm25 = self.bm25.search(query, candidates, self.bm25_top_k)
        metadata: dict[str, Any] = {
            "retrieval_mode": self.mode,
            "retrieval_strategy": "bm25_top30" if self.mode == "bm25" else "bm25_top30+dense_top30+rrf",
            "bm25_top_k": self.bm25_top_k,
            "dense_top_k": 0 if self.mode == "bm25" else self.dense_top_k,
            "rrf_k": None if self.mode == "bm25" else self.rrf_k,
            "dense_enabled": bool(self.mode == "hybrid" and self.dense is not None),
            "fallback_used": bool(self.mode == "hybrid" and self.dense is None),
            "dense_failed": False,
        }
        if self.mode != "hybrid" or self.dense is None:
            result = bm25[:60]
        else:
            try:
                dense = self.dense.search(query, candidates, self.dense_top_k)
                result = reciprocal_rank_fusion(bm25, dense, k=self.rrf_k, limit=60)
            except DenseRetrievalError:
                metadata["dense_failed"] = True
                metadata["fallback_used"] = True
                result = bm25[:60]
        metadata["candidate_count"] = len(result)
        return RetrievalResult(result, metadata)
