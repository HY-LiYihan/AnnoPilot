from __future__ import annotations

import json

from backend.app.engagement_candidates import (
    build_engagement_generation_prompt,
    parse_engagement_candidate,
    score_candidate_consistency,
)


def _tokens(text: str) -> list[dict[str, object]]:
    return [
        {"token_index": 0, "text": "🙂", "start_char": 0, "end_char": 1},
        {"token_index": 1, "text": "可能", "start_char": 1, "end_char": 3},
        {"token_index": 2, "text": "may", "start_char": 4, "end_char": 7},
        {"token_index": 3, "text": ".", "start_char": 7, "end_char": 8},
    ]


def _candidate(text: str = "🙂可能 may.", *, start: int = 1, end: int = 3, label: str = "engagement_entertain") -> str:
    return json.dumps(
        {
            "text": text,
            "spans": [{"start": start, "end": end, "text": text[start:end], "label": label, "confidence": 0.93}],
            "explanation": "The cue opens the proposition to another voice.",
        },
        ensure_ascii=False,
    )


def test_bilingual_candidate_uses_python_code_point_offsets() -> None:
    candidate, issues = parse_engagement_candidate(
        _candidate(),
        source_text="🙂可能 may.",
        label_to_id={"engagement_entertain": "engagement_entertain"},
        tokens=_tokens("🙂可能 may."),
    )

    assert candidate["spans"][0]["text"] == "可能"
    assert issues == []
    assert candidate["spans"][0]["start"] == 1


def test_verifier_rejects_text_label_boundary_and_overlap_errors() -> None:
    raw = json.dumps(
        {
            "text": "wrong",
            "spans": [
                {"start": 1, "end": 4, "text": "可能 ", "label": "unknown", "confidence": 0.8},
                {"start": 2, "end": 7, "text": "能 may", "label": "engagement_entertain", "confidence": 0.8},
            ],
            "explanation": "bad candidate",
        }
    )
    _, issues = parse_engagement_candidate(
        raw,
        source_text="🙂可能 may.",
        label_to_id={"engagement_entertain": "engagement_entertain"},
        tokens=_tokens("🙂可能 may."),
    )

    codes = {issue["code"] for issue in issues}
    assert {"text_mismatch", "invalid_label", "offset_not_token_boundary", "overlapping_spans"} <= codes


def test_consistency_requires_all_candidates_and_routes_same_config_runs() -> None:
    passed = {"verifier_status": "passed", "spans": [{"start": 1, "end": 3, "text": "可能", "label": "engagement_entertain"}]}
    invalid = {"verifier_status": "failed", "spans": []}

    high = score_candidate_consistency([passed, dict(passed), dict(passed)], requested_count=3)
    low = score_candidate_consistency([passed, dict(passed), invalid], requested_count=3)

    assert high.route == "high"
    assert high.auto_accept_eligible is True
    assert low.route == "low"
    assert low.auto_accept_eligible is False
    assert low.invalid_candidate_count == 1


def test_prompt_is_bilingual_and_carries_taxonomy_contract() -> None:
    prompt = build_engagement_generation_prompt(
        "It may change.",
        [
            {
                "id": "engagement_entertain",
                "name": "Entertain",
                "description": "Opens dialogic space.",
                "taxonomy": {"system": "engagement", "family": "entertain"},
                "examples": ["may"],
            }
        ],
    )

    assert "appraisal Engagement" in prompt
    assert "Python code-point offsets" in prompt
    assert "engagement_entertain" in prompt
    assert "may" in prompt


def test_non_integer_offsets_are_rejected_instead_of_truncated() -> None:
    raw = json.dumps(
        {
            "text": "🙂可能 may.",
            "spans": [{"start": 1.9, "end": 3.1, "text": "可能", "label": "engagement_entertain", "confidence": 0.9}],
            "explanation": "The cue opens dialogic space.",
        },
        ensure_ascii=False,
    )
    _, issues = parse_engagement_candidate(
        raw,
        source_text="🙂可能 may.",
        label_to_id={"engagement_entertain": "engagement_entertain"},
        tokens=_tokens("🙂可能 may."),
    )

    assert "invalid_offset" in {issue["code"] for issue in issues}
