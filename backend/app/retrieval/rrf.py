from __future__ import annotations

from collections import defaultdict

from .base import RankedCandidate


def reciprocal_rank_fusion(*ranked_lists: list[RankedCandidate], k: int = 60, limit: int = 60) -> list[RankedCandidate]:
    scores: defaultdict[str, float] = defaultdict(float)
    records: dict[str, RankedCandidate] = {}
    sources: defaultdict[str, list[str]] = defaultdict(list)
    for ranked in ranked_lists:
        for item in ranked:
            scores[item.candidate.id] += 1 / (k + item.rank)
            records[item.candidate.id] = item
            sources[item.candidate.id].append(item.source)
    ordered = sorted(scores, key=lambda item: (-scores[item], item))[: max(0, limit)]
    return [RankedCandidate(records[item].candidate, scores[item], index, "+".join(sorted(set(sources[item])))) for index, item in enumerate(ordered, 1)]
