from __future__ import annotations

import json

from backend.app.assistance_generation import (
    OpenAICompatibleAssistanceGenerator,
    build_assistance_prompt,
    parse_and_verify_assistance_candidate,
    select_assistance_examples,
)
from backend.app.settings import LlmSettings


TEXT = "Alice visits Paris."
TOKENS = [
    {"token_index": 0, "text": "Alice", "start_char": 0, "end_char": 5},
    {"token_index": 1, "text": "visits", "start_char": 6, "end_char": 12},
    {"token_index": 2, "text": "Paris", "start_char": 13, "end_char": 18},
    {"token_index": 3, "text": ".", "start_char": 18, "end_char": 19},
]
LABELS = {"person": "person", "location": "location"}


def _raw(spans: list[dict[str, object]], *, text: str = TEXT) -> str:
    return json.dumps({"text": text, "spans": spans})


def test_parse_accepts_empty_and_valid_spans() -> None:
    empty, empty_issues = parse_and_verify_assistance_candidate(_raw([]), TEXT, LABELS, TOKENS)
    valid, valid_issues = parse_and_verify_assistance_candidate(
        _raw([{"start": 0, "end": 5, "text": "Alice", "label": "person", "confidence": 0.91}]), TEXT, LABELS, TOKENS
    )

    assert empty["spans"] == []
    assert empty_issues == []
    assert valid_issues == []
    assert valid["spans"][0]["label"] == "person"


def test_parse_reports_offset_text_label_and_overlap_errors() -> None:
    _, issues = parse_and_verify_assistance_candidate(
        _raw(
            [
                {"start": 0, "end": 7, "text": "Alice v", "label": "unknown", "confidence": 0.8},
                {"start": 0, "end": 5, "text": "wrong", "label": "person", "confidence": 0.8},
                {"start": 13, "end": 18, "text": "Paris", "label": "location", "confidence": 0.8},
                {"start": 13, "end": 18, "text": "Paris", "label": "location", "confidence": 0.8},
            ]
        ),
        TEXT,
        LABELS,
        TOKENS,
    )

    codes = {issue["code"] for issue in issues}
    assert {"offset_not_token_boundary", "span_text_mismatch", "invalid_label", "duplicate_span", "overlapping_spans"} <= codes


def test_select_examples_keeps_small_pool_and_selects_similar_plus_recent_corrections() -> None:
    small = select_assistance_examples("Alice", {"person": ["Alice", "Bob"]}, {"person": [{"text": "Carol", "created_at": "2026-01-01"}]})
    assert small["person"] == ["Alice", "Bob", {"text": "Carol", "created_at": "2026-01-01"}]

    examples = [{"text": f"Alice target {index:02d}"} for index in range(24)]
    corrections = [{"text": f"Correction {index}", "corrected_at": f"2026-01-{index + 1:02d}"} for index in range(6)]
    selected = select_assistance_examples("Alice target", {"person": examples}, {"person": corrections})["person"]

    assert len(selected) == 12
    assert {item["text"] for item in corrections[-4:]} <= {item["text"] for item in selected}
    assert selected == select_assistance_examples("Alice target", {"person": examples}, {"person": corrections})["person"]


def test_generator_includes_retry_context_and_returns_provider_json(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def read(self) -> bytes:
            return json.dumps({"choices": [{"message": {"content": _raw([])}}]}).encode()

    def fake_urlopen(request, timeout):
        captured["payload"] = json.loads(request.data.decode())
        captured["authorization"] = request.get_header("Authorization")
        return Response()

    monkeypatch.setattr("backend.app.assistance_generation.urllib.request.urlopen", fake_urlopen)
    generator = OpenAICompatibleAssistanceGenerator(LlmSettings("https://api.example.test/v1", "secret-key", "gpt5.5"))
    result = generator.generate(
        TEXT,
        [{"id": "person", "name": "Person"}],
        {"person": ["Alice"]},
        [],
        validation_issues=[{"code": "offset_not_token_boundary", "message": "Use token edges."}],
    )

    prompt = captured["payload"]["messages"][1]["content"]
    assert result == {"text": TEXT, "spans": []}
    assert captured["authorization"] == "Bearer secret-key"
    assert "offset_not_token_boundary" in prompt
    assert "Alice" in prompt
    assert "secret-key" not in prompt
    assert "response_format" in captured["payload"]


def test_prompt_only_lists_supplied_labels() -> None:
    prompt = build_assistance_prompt(TEXT, [{"id": "person", "name": "Person"}], {}, [])
    assert '"id": "person"' in prompt
    assert "location" not in prompt
