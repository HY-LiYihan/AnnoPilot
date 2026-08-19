from __future__ import annotations

import math
import re
import unicodedata
from collections import Counter

from .base import RankedCandidate, RetrievalCandidate


_TOKEN_RE = re.compile(r"[A-Za-z0-9]+|[\u4e00-\u9fff]|[^\W_\s]", re.UNICODE)


def tokenize_for_bm25(value: str) -> list[str]:
    """Tokenize mixed Chinese/English text without a heavyweight dependency."""
    normalized = unicodedata.normalize("NFKC", str(value or "")).casefold()
    tokens = _TOKEN_RE.findall(normalized)
    # Character bigrams give Chinese short phrases useful lexical signal.
    cjk = [char for char in normalized if "\u4e00" <= char <= "\u9fff"]
    tokens.extend(f"{cjk[index]}{cjk[index + 1]}" for index in range(len(cjk) - 1))
    return tokens


class BM25Retriever:
    def __init__(self, *, k1: float = 1.5, b: float = 0.75):
        self.k1 = k1
        self.b = b

    def search(self, query: str, candidates: list[RetrievalCandidate], top_k: int = 30) -> list[RankedCandidate]:
        if not candidates or not query:
            return []
        query_terms = tokenize_for_bm25(query)
        documents = [tokenize_for_bm25(item.text) for item in candidates]
        avgdl = sum(len(doc) for doc in documents) / max(1, len(documents))
        document_frequency: Counter[str] = Counter()
        for document in documents:
            document_frequency.update(set(document))
        scores: list[tuple[float, RetrievalCandidate]] = []
        total = len(documents)
        for candidate, document in zip(candidates, documents):
            counts = Counter(document)
            score = 0.0
            for term in query_terms:
                frequency = counts.get(term, 0)
                if not frequency:
                    continue
                df = document_frequency[term]
                idf = math.log(1 + (total - df + 0.5) / (df + 0.5))
                normalization = 1 - self.b + self.b * len(document) / max(avgdl, 1e-9)
                score += idf * frequency * (self.k1 + 1) / (frequency + self.k1 * normalization)
            if score > 0:
                scores.append((score, candidate))
        scores.sort(key=lambda item: (-item[0], item[1].id))
        return [RankedCandidate(candidate, score, index, "bm25") for index, (score, candidate) in enumerate(scores[: max(0, top_k)], 1)]
