from __future__ import annotations

from dataclasses import dataclass


SENTENCE_ENDINGS = set("。！？?!")


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

        if char == "\n":
            boundary_end = i
            while i + 1 < len(normalized) and normalized[i + 1] == "\n":
                i += 1
        elif char in SENTENCE_ENDINGS:
            boundary_end = i + 1
        elif char == "." and _period_ends_sentence(normalized, i):
            boundary_end = i + 1

        if boundary_end is not None:
            span = _trimmed_span(normalized, start, boundary_end)
            if span is not None:
                trimmed_start, trimmed_end, sentence_text = span
                sentences.append(SentenceSpan(index=index, text=sentence_text, start=trimmed_start, end=trimmed_end))
                index += 1
            start = i + 1
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
    return next_char == "" or next_char.isspace() or next_char in '"\'”’)]}>'


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
