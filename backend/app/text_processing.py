from __future__ import annotations

from dataclasses import dataclass


SENTENCE_ENDINGS = set("。！？?!")
SENTENCE_TRAILING_CLOSERS = set("\"'”’)]}〉》」』】）")
ENGLISH_SENTENCE_ABBREVIATIONS = {
    "co",
    "corp",
    "dept",
    "dr",
    "e.g",
    "gov",
    "i.e",
    "inc",
    "jr",
    "ltd",
    "mr",
    "mrs",
    "ms",
    "no",
    "prof",
    "rep",
    "sen",
    "sr",
    "st",
    "u.k",
    "u.n",
    "u.s",
    "vs",
}
ENGLISH_NON_TERMINAL_ABBREVIATIONS = {
    "dr",
    "e.g",
    "gov",
    "i.e",
    "jr",
    "mr",
    "mrs",
    "ms",
    "no",
    "prof",
    "rep",
    "sen",
    "sr",
    "st",
    "vs",
}
ENGLISH_INITIALISM_ABBREVIATIONS = {"u.k", "u.n", "u.s"}
ENGLISH_SENTENCE_STARTERS_AFTER_ABBREVIATION = {
    "a",
    "an",
    "but",
    "he",
    "however",
    "i",
    "instead",
    "it",
    "meanwhile",
    "she",
    "that",
    "the",
    "these",
    "they",
    "this",
    "those",
    "we",
    "yet",
}


@dataclass(frozen=True)
class SentenceSpan:
    index: int
    text: str
    start: int
    end: int


@dataclass(frozen=True)
class TokenSpan:
    index: int
    text: str
    start: int
    end: int


def normalize_text(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def split_sentences(text: str) -> list[SentenceSpan]:
    normalized = normalize_text(text)
    sentences: list[SentenceSpan] = []
    start = 0
    index = 0
    i = 0

    while i < len(normalized):
        char = normalized[i]
        boundary_end: int | None = None
        next_start: int | None = None

        if char == "\n":
            boundary_end = i
            while i + 1 < len(normalized) and normalized[i + 1] == "\n":
                i += 1
            next_start = i + 1
        elif char in SENTENCE_ENDINGS:
            boundary_end = _include_trailing_closers(normalized, _include_sentence_ending_run(normalized, i + 1))
            next_start = boundary_end
        elif char == "." and _period_ends_sentence(normalized, i):
            boundary_end = _include_trailing_closers(normalized, i + 1)
            next_start = boundary_end

        if boundary_end is not None:
            span = _trimmed_span(normalized, start, boundary_end)
            if span is not None:
                trimmed_start, trimmed_end, sentence_text = span
                sentences.append(SentenceSpan(index=index, text=sentence_text, start=trimmed_start, end=trimmed_end))
                index += 1
            start = next_start if next_start is not None else i + 1
            i = start
            continue
        i += 1

    span = _trimmed_span(normalized, start, len(normalized))
    if span is not None:
        trimmed_start, trimmed_end, sentence_text = span
        sentences.append(SentenceSpan(index=index, text=sentence_text, start=trimmed_start, end=trimmed_end))

    return sentences


def tokenize_sentence(sentence: SentenceSpan) -> list[TokenSpan]:
    tokens: list[TokenSpan] = []
    text = sentence.text
    i = 0

    while i < len(text):
        char = text[i]
        if char.isspace():
            i += 1
            continue

        token_start = i
        if _is_cjk(char):
            i += 1
        elif char.isascii() and char.isdigit():
            i = _consume_numeric_token(text, i)
        elif _is_word_char(char):
            i += 1
            while i < len(text) and _is_word_char(text[i]):
                i += 1
        else:
            i += 1

        token_text = text[token_start:i]
        tokens.append(
            TokenSpan(
                index=len(tokens),
                text=token_text,
                start=sentence.start + token_start,
                end=sentence.start + i,
            )
        )

    return tokens


def _period_ends_sentence(text: str, index: int) -> bool:
    previous_char = text[index - 1] if index > 0 else ""
    next_char = text[index + 1] if index + 1 < len(text) else ""
    if previous_char.isdigit() and next_char.isdigit():
        return False
    if _period_is_english_abbreviation(text, index):
        return False
    return next_char == "" or next_char.isspace() or next_char in SENTENCE_TRAILING_CLOSERS


def _period_is_english_abbreviation(text: str, index: int) -> bool:
    abbreviation = _english_abbreviation_at_period(text, index)
    if abbreviation is None:
        return False
    key, token_end = abbreviation

    if index < token_end - 1:
        return True
    if key in ENGLISH_NON_TERMINAL_ABBREVIATIONS:
        return True

    next_word = _next_word(text, token_end)
    if not next_word:
        return False
    if next_word[0].islower() or next_word[0].isdigit():
        return True
    if key in ENGLISH_INITIALISM_ABBREVIATIONS and next_word.casefold() not in ENGLISH_SENTENCE_STARTERS_AFTER_ABBREVIATION:
        return True
    return False


def _english_abbreviation_at_period(text: str, index: int) -> tuple[str, int] | None:
    token_start = index
    while token_start > 0 and (text[token_start - 1].isalpha() or text[token_start - 1] == "."):
        token_start -= 1

    token_end = index + 1
    while token_end < len(text) and (text[token_end].isalpha() or text[token_end] == "."):
        token_end += 1

    token = text[token_start:token_end].strip(".")
    if not token:
        return None

    key = token.casefold()
    if key in ENGLISH_SENTENCE_ABBREVIATIONS:
        return key, token_end

    parts = token.split(".")
    if len(parts) > 1 and all(len(part) == 1 and part.isalpha() for part in parts) and any(part.isupper() for part in parts):
        return key, token_end
    return None


def _next_word(text: str, index: int) -> str:
    while index < len(text) and text[index].isspace():
        index += 1
    start = index
    while index < len(text) and text[index].isalpha():
        index += 1
    return text[start:index]


def _include_trailing_closers(text: str, index: int) -> int:
    while index < len(text) and text[index] in SENTENCE_TRAILING_CLOSERS:
        index += 1
    return index


def _include_sentence_ending_run(text: str, index: int) -> int:
    while index < len(text) and text[index] in SENTENCE_ENDINGS:
        index += 1
    return index


def _trimmed_span(text: str, start: int, end: int) -> tuple[int, int, str] | None:
    while start < end and text[start].isspace():
        start += 1
    while end > start and text[end - 1].isspace():
        end -= 1
    if start >= end:
        return None
    return start, end, text[start:end]


def _is_cjk(char: str) -> bool:
    codepoint = ord(char)
    return (
        0x3400 <= codepoint <= 0x4DBF
        or 0x4E00 <= codepoint <= 0x9FFF
        or 0xF900 <= codepoint <= 0xFAFF
    )


def _is_word_char(char: str) -> bool:
    return char.isascii() and (char.isalnum() or char in {"_", "'", "-"})


def _consume_numeric_token(text: str, index: int) -> int:
    i = index
    while i < len(text) and text[i].isdigit():
        i += 1

    while i + 1 < len(text) and text[i] in {".", ","} and text[i + 1].isdigit():
        i += 1
        while i < len(text) and text[i].isdigit():
            i += 1

    if i < len(text) and text[i] == "%":
        i += 1

    if i + 1 < len(text) and text[i] == "-" and text[i + 1].isascii() and text[i + 1].isalnum():
        i += 1
        while i < len(text) and _is_word_char(text[i]):
            i += 1

    return i
