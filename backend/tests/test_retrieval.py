from __future__ import annotations

import json

import pytest

from backend.app.retrieval.base import RetrievalCandidate
from backend.app.retrieval.bm25 import BM25Retriever, tokenize_for_bm25
from backend.app.retrieval.dense import DenseRetriever, DenseRetrievalError
from backend.app.retrieval.service import RetrievalService


def _candidates() -> list[RetrievalCandidate]:
    return [
        RetrievalCandidate("zh", "苹果 公司 发布 新 产品"),
        RetrievalCandidate("en", "Apple released a new product"),
        RetrievalCandidate("other", "天气 今天 很 好"),
    ]


def test_bm25_supports_chinese_english_and_top_k() -> None:
    assert "苹果" in tokenize_for_bm25("苹果公司")
    assert "apple" in tokenize_for_bm25("Apple")
    hits = BM25Retriever().search("Apple product", _candidates(), top_k=1)
    assert len(hits) == 1
    assert hits[0].candidate.id == "en"


def test_bm25_returns_at_most_thirty() -> None:
    candidates = [RetrievalCandidate(str(index), f"shared token {index}") for index in range(80)]
    assert len(BM25Retriever().search("shared token", candidates, top_k=30)) == 30


def test_dense_provider_posts_openai_compatible_embeddings(monkeypatch: pytest.MonkeyPatch) -> None:
    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def read(self) -> bytes:
            return json.dumps({"data": [
                {"index": 0, "embedding": [1.0, 0.0]},
                {"index": 1, "embedding": [1.0, 0.0]},
                {"index": 2, "embedding": [0.0, 1.0]},
                {"index": 3, "embedding": [0.0, 1.0]},
            ]}).encode()

    captured = {}

    def fake_urlopen(request, timeout):
        captured["payload"] = json.loads(request.data.decode())
        captured["timeout"] = timeout
        return Response()

    monkeypatch.setattr("backend.app.retrieval.dense.urllib.request.urlopen", fake_urlopen)
    hits = DenseRetriever("http://embedding.test/v1", "", "Qwen3-Embedding-0.6B").search("query", _candidates(), 2)
    assert captured["payload"]["model"] == "Qwen3-Embedding-0.6B"
    assert captured["payload"]["input"][0] == "query"
    assert hits[0].candidate.id == "zh"


def test_dense_provider_batches_requests_to_service_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    batch_lengths: list[int] = []

    class Response:
        def __init__(self, count: int):
            self.count = count

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def read(self) -> bytes:
            return json.dumps({"data": [{"index": index, "embedding": [1.0, 0.0]} for index in range(self.count)]}).encode()

    def fake_urlopen(request, timeout):
        count = len(json.loads(request.data.decode())["input"])
        batch_lengths.append(count)
        return Response(count)

    monkeypatch.setattr("backend.app.retrieval.dense.urllib.request.urlopen", fake_urlopen)
    candidates = [RetrievalCandidate(str(index), f"candidate {index}") for index in range(70)]
    hits = DenseRetriever("http://embedding.test/v1", "", "test", batch_size=32).search("query", candidates, 30)
    assert batch_lengths == [32, 32, 7]
    assert len(hits) == 30


def test_hybrid_falls_back_to_bm25_when_dense_fails() -> None:
    class BrokenDense:
        def search(self, *_args, **_kwargs):
            raise DenseRetrievalError("offline")

    result = RetrievalService(mode="hybrid", dense=BrokenDense()).search("Apple product", _candidates())
    assert result.metadata["fallback_used"] is True
    assert result.metadata["dense_failed"] is True
    assert result.candidates[0].candidate.id == "en"


def test_bm25_mode_never_calls_dense() -> None:
    class FailingDense:
        def search(self, *_args, **_kwargs):
            raise AssertionError("dense must not be called in bm25 mode")

    result = RetrievalService(mode="bm25", dense=FailingDense()).search("Apple", _candidates())
    assert result.metadata["dense_enabled"] is False
    assert result.metadata["dense_top_k"] == 0


def test_hybrid_rrf_candidate_pool_is_bounded() -> None:
    class Dense:
        def search(self, _query, candidates, top_k):
            from backend.app.retrieval.base import RankedCandidate

            return [RankedCandidate(candidate, 1.0, index, "dense") for index, candidate in enumerate(candidates[:top_k], 1)]

    candidates = [RetrievalCandidate(str(index), f"token {index}") for index in range(100)]
    result = RetrievalService(mode="hybrid", dense=Dense()).search("token", candidates)
    assert len(result.candidates) <= 60
