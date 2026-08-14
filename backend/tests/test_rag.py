from backend.app.rag import generate_candidate_spans, match_normalization_config


def test_cjk_inner_whitespace_does_not_break_lexical_exact_match() -> None:
    tokens = [
        {"token_index": 0, "text": "清", "start_char": 0, "end_char": 1},
        {"token_index": 1, "text": "楚", "start_char": 2, "end_char": 3},
        {"token_index": 2, "text": "显", "start_char": 4, "end_char": 5},
        {"token_index": 3, "text": "示", "start_char": 6, "end_char": 7},
    ]

    candidates = generate_candidate_spans(
        tokens,
        {"engagement_proclaim_endorse": ["清楚显示"]},
        blocked_ranges=[],
        limit=4,
        min_confidence=0.98,
    )

    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate.text == "清 楚 显 示"
    assert candidate.start_char == 0
    assert candidate.end_char == 7
    assert candidate.source == "lexical_exact"
    assert candidate.match_key == "清楚显示"
    assert candidate.evidence_match_key == "清楚显示"


def test_english_multi_word_cue_keeps_required_space() -> None:
    tokens = [
        {"token_index": 0, "text": "Of", "start_char": 0, "end_char": 2},
        {"token_index": 1, "text": "course", "start_char": 3, "end_char": 9},
        {"token_index": 2, "text": ",", "start_char": 9, "end_char": 10},
    ]

    candidates = generate_candidate_spans(
        tokens,
        {"engagement_proclaim_concur": ["of course"]},
        blocked_ranges=[],
        limit=4,
        min_confidence=0.98,
    )

    assert len(candidates) == 1
    assert candidates[0].text == "Of course"
    assert candidates[0].match_key == "of course"


def test_fullwidth_numeric_punctuation_normalizes_for_lexical_match() -> None:
    tokens = [
        {"token_index": 0, "text": "3.5％", "start_char": 10, "end_char": 14},
    ]

    candidates = generate_candidate_spans(
        tokens,
        {"metric": ["3.5%"]},
        blocked_ranges=[],
        limit=4,
        min_confidence=0.98,
    )

    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate.text == "3.5％"
    assert candidate.start_char == 10
    assert candidate.end_char == 14
    assert candidate.source == "lexical_exact"
    assert candidate.match_key == "3.5%"
    assert candidate.evidence_match_key == "3.5%"


def test_match_normalization_documents_cjk_inner_whitespace_step() -> None:
    assert match_normalization_config() == {
        "schema_version": "annopilot.match_normalization.v3",
        "steps": ["strip", "unicode_nfkc", "collapse_whitespace", "casefold", "remove_cjk_inner_whitespace"],
        "preserves_source_text": True,
    }
