from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Any


DEFAULT_LEXICAL_EXAMPLES = {
    "noun": ["小猫", "柳树", "小河", "石桥", "叶子", "太阳", "男孩", "书包", "爪子", "水流", "桥边"],
    "verb": ["发芽", "走来", "看见", "伸出", "碰", "漂走", "坐", "看着", "升起来", "经过", "笑", "说", "抬起", "回答"],
    "adjective": ["金色", "安静", "轻轻", "慢慢"],
}

MAX_SPAN_TOKENS = 6
MIN_FUZZY_SCORE = 0.68
CHARACTER_RAG_RETRIEVAL = "offset_gap_span_text|nfkc_quote_dash_casefold_whitespace_cjk_inner_space_normalized|lexical_exact|lexical_contains|char_ngram"
MATCH_NORMALIZATION_SCHEMA_VERSION = "annopilot.match_normalization.v4"

PUNCTUATION_TRANSLATION = str.maketrans(
    {
        "‘": "'",
        "’": "'",
        "‚": "'",
        "‛": "'",
        "＇": "'",
        "“": '"',
        "”": '"',
        "„": '"',
        "‟": '"',
        "－": "-",
        "−": "-",
        "﹣": "-",
        "–": "-",
        "—": "-",
        "―": "-",
        "／": "/",
    }
)


def match_normalization_config() -> dict[str, Any]:
    return {
        "schema_version": MATCH_NORMALIZATION_SCHEMA_VERSION,
        "steps": [
            "strip",
            "unicode_nfkc",
            "normalize_quotes_dashes_slashes",
            "space_alnum_hyphen_slash_connectors",
            "collapse_apostrophe_spacing",
            "collapse_whitespace",
            "casefold",
            "remove_cjk_inner_whitespace",
        ],
        "preserves_source_text": True,
    }


@dataclass(frozen=True)
class CandidateSpan:
    tag_id: str
    start_token_index: int
    end_token_index: int
    start_char: int
    end_char: int
    text: str
    confidence: float
    source: str
    evidence_text: str
    match_key: str
    evidence_match_key: str


def build_examples(tags: list[dict[str, Any]], annotations: list[dict[str, Any]]) -> dict[str, list[str]]:
    examples: dict[str, list[str]] = {tag["id"]: [] for tag in tags}
    for tag in tags:
        tag_examples = tag.get("examples") or DEFAULT_LEXICAL_EXAMPLES.get(tag["id"], [])
        examples[tag["id"]].extend(str(example) for example in tag_examples)
    for annotation in annotations:
        text = str(annotation["text"]).strip()
        if text:
            examples.setdefault(annotation["tag_id"], []).append(text)
    return {tag_id: _dedupe(values) for tag_id, values in examples.items() if values}


def build_negative_examples(tags: list[dict[str, Any]], rejected_suggestions: list[dict[str, Any]]) -> dict[str, list[str]]:
    examples: dict[str, list[str]] = {tag["id"]: [] for tag in tags}
    for suggestion in rejected_suggestions:
        text = str(suggestion["text"]).strip()
        if text:
            examples.setdefault(suggestion["tag_id"], []).append(text)
    return {tag_id: _dedupe(values) for tag_id, values in examples.items() if values}


def build_match_keys_by_tag(examples_by_tag: dict[str, list[str]]) -> dict[str, list[str]]:
    match_keys_by_tag: dict[str, list[str]] = {}
    for tag_id, examples in examples_by_tag.items():
        match_keys = _dedupe_match_keys(examples)
        if match_keys:
            match_keys_by_tag[tag_id] = match_keys
    return match_keys_by_tag


def generate_candidate_spans(
    tokens: list[dict[str, Any]],
    examples_by_tag: dict[str, list[str]],
    blocked_ranges: list[tuple[int, int]],
    limit: int,
    min_confidence: float = 0.0,
    negative_examples_by_tag: dict[str, list[str]] | None = None,
) -> list[CandidateSpan]:
    raw_candidates: list[CandidateSpan] = []
    negative_examples_by_tag = negative_examples_by_tag or {}
    for start in range(len(tokens)):
        for end in range(start, min(len(tokens), start + MAX_SPAN_TOKENS)):
            if _overlaps(start, end, blocked_ranges):
                continue
            span_text = _span_text(tokens[start : end + 1]).strip()
            if not _is_candidate_text(span_text):
                continue
            match = _best_match(span_text, examples_by_tag)
            if match is None:
                continue
            tag_id, confidence, source, evidence_text, match_key, evidence_match_key = match
            if _is_negative_example(span_text, tag_id, negative_examples_by_tag):
                continue
            if confidence < min_confidence:
                continue
            raw_candidates.append(
                CandidateSpan(
                    tag_id=tag_id,
                    start_token_index=int(tokens[start]["token_index"]),
                    end_token_index=int(tokens[end]["token_index"]),
                    start_char=int(tokens[start]["start_char"]),
                    end_char=int(tokens[end]["end_char"]),
                    text=span_text,
                    confidence=confidence,
                    source=source,
                    evidence_text=evidence_text,
                    match_key=match_key,
                    evidence_match_key=evidence_match_key,
                )
            )

    selected: list[CandidateSpan] = []
    for candidate in sorted(raw_candidates, key=lambda item: (-item.confidence, -len(item.text), item.start_token_index)):
        if _overlaps(candidate.start_token_index, candidate.end_token_index, [(item.start_token_index, item.end_token_index) for item in selected]):
            continue
        selected.append(candidate)
        if len(selected) >= limit:
            break
    return sorted(selected, key=lambda item: (item.start_token_index, item.end_token_index))


def _best_match(text: str, examples_by_tag: dict[str, list[str]]) -> tuple[str, float, str, str, str, str] | None:
    best: tuple[str, float, str, str, str, str] | None = None
    for tag_id, examples in examples_by_tag.items():
        for example in examples:
            score, source, match_key, evidence_match_key = _score(text, example)
            if score <= 0:
                continue
            if best is None or score > best[1]:
                best = (tag_id, score, source, example, match_key, evidence_match_key)
    return best


def _score(text: str, example: str) -> tuple[float, str, str, str]:
    normalized_text = _normalize_match_text(text)
    normalized_example = _normalize_match_text(example)
    if not normalized_text or not normalized_example:
        return 0, "", normalized_text, normalized_example
    if normalized_text == normalized_example:
        return 0.98, "lexical_exact", normalized_text, normalized_example
    if len(normalized_text) > 1 and len(normalized_example) > 1 and (
        normalized_text in normalized_example or normalized_example in normalized_text
    ):
        length_ratio = min(len(normalized_text), len(normalized_example)) / max(len(normalized_text), len(normalized_example))
        return round(0.74 + 0.12 * length_ratio, 4), "lexical_contains", normalized_text, normalized_example
    similarity = _char_ngram_jaccard(normalized_text, normalized_example)
    if similarity >= MIN_FUZZY_SCORE:
        return round(0.55 + 0.25 * similarity, 4), "char_ngram", normalized_text, normalized_example
    return 0, "", normalized_text, normalized_example


def _is_negative_example(text: str, tag_id: str, negative_examples_by_tag: dict[str, list[str]]) -> bool:
    normalized = _normalize_match_text(text)
    return bool(normalized) and normalized in {
        _normalize_match_text(example) for example in negative_examples_by_tag.get(tag_id, [])
    }


def _normalize_match_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", str(value).strip()).translate(PUNCTUATION_TRANSLATION)
    normalized = re.sub(r"(?<=[A-Za-z0-9])[-/](?=[A-Za-z0-9])", " ", normalized)
    normalized = re.sub(r"\s*'\s*", "'", normalized)
    collapsed = " ".join(normalized.split()).casefold()
    return _remove_cjk_inner_whitespace(collapsed)


def _remove_cjk_inner_whitespace(text: str) -> str:
    normalized: list[str] = []
    for index, char in enumerate(text):
        if (
            char == " "
            and index > 0
            and index + 1 < len(text)
            and _is_cjk(text[index - 1])
            and _is_cjk(text[index + 1])
        ):
            continue
        normalized.append(char)
    return "".join(normalized)


def _char_ngram_jaccard(left: str, right: str) -> float:
    left_grams = _char_ngrams(left)
    right_grams = _char_ngrams(right)
    if not left_grams or not right_grams:
        return 0
    return len(left_grams & right_grams) / len(left_grams | right_grams)


def _char_ngrams(text: str) -> set[str]:
    if len(text) < 2:
        return {text}
    return {text[index : index + 2] for index in range(len(text) - 1)}


def _span_text(tokens: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    previous_end: int | None = None
    for token in tokens:
        token_text = str(token["text"])
        token_start = int(token["start_char"])
        if previous_end is not None and token_start > previous_end:
            parts.append(" ")
        parts.append(token_text)
        previous_end = int(token["end_char"])
    return "".join(parts)


def _is_candidate_text(text: str) -> bool:
    return bool(text) and any(char.isalnum() or _is_cjk(char) for char in text)


def _overlaps(start: int, end: int, ranges: list[tuple[int, int]]) -> bool:
    return any(range_start <= end and range_end >= start for range_start, range_end in ranges)


def _is_cjk(char: str) -> bool:
    codepoint = ord(char)
    return 0x3400 <= codepoint <= 0x4DBF or 0x4E00 <= codepoint <= 0x9FFF or 0xF900 <= codepoint <= 0xFAFF


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    deduped: list[str] = []
    for value in values:
        text = str(value).strip()
        normalized = _normalize_match_text(text)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        deduped.append(text)
    return deduped


def _dedupe_match_keys(values: list[str]) -> list[str]:
    seen: set[str] = set()
    deduped: list[str] = []
    for value in values:
        normalized = _normalize_match_text(value)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        deduped.append(normalized)
    return deduped
