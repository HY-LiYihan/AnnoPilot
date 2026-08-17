#!/usr/bin/env python3
"""Run a deterministic OpenNER human-in-the-loop assistance experiment over HTTP.

The experiment intentionally treats AnnoPilot as a black box: it imports TXT, reads
sentences/tokens, and records human decisions exclusively through the public REST API.
It never opens the runtime SQLite database.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


REPO_ROOT = Path(__file__).resolve().parents[2]
OPENNER_ROOT = REPO_ROOT / "tmp" / "openner"
LABELS = ("PER", "ORG", "LOC")
PAGE_SIZE = 200
DEFAULT_DRAFT_WAIT_SECONDS = 900.0
DEFAULT_DRAFT_POLL_INTERVAL = 0.5


class ExperimentError(RuntimeError):
    """A data or API contract mismatch that invalidates an experiment."""


@dataclass(frozen=True)
class BioSentence:
    tokens: tuple[str, ...]
    labels: tuple[str, ...]


@dataclass(frozen=True)
class GoldSpan:
    label: str
    start: int
    end: int
    text: str


@dataclass(frozen=True)
class SourceExample:
    source_index: int
    raw_text: str
    bio: BioSentence
    gold_spans: tuple[GoldSpan, ...]
    raw_start: int


def dataset_paths(language: str, root: Path = OPENNER_ROOT) -> tuple[Path, Path]:
    if language == "zh":
        return root / "standardized" / "UNER_Chinese_GSD" / "cmn" / "train.txt", root / "raw" / "openner_chinese_1000.txt"
    if language == "en":
        return root / "standardized" / "UNER_English_EWT" / "eng" / "train.txt", root / "raw" / "openner_english_1000.txt"
    raise ValueError("language must be 'zh' or 'en'.")


def parse_bio(path: Path) -> list[BioSentence]:
    """Parse a two-column BIO file, splitting sentences on blank lines."""
    sentences: list[BioSentence] = []
    tokens: list[str] = []
    labels: list[str] = []

    def finish() -> None:
        if tokens:
            sentences.append(BioSentence(tuple(tokens), tuple(labels)))
            tokens.clear()
            labels.clear()

    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw_line.strip()
        if not line:
            finish()
            continue
        parts = line.split()
        if len(parts) < 2:
            raise ExperimentError(f"Malformed BIO row at {path}:{line_number}: {raw_line!r}")
        token, label = parts[0], parts[-1]
        if token == "-DOCSTART-":
            finish()
            continue
        tokens.append(token)
        labels.append(label)
    finish()
    return sentences


def align_bio_tokens(raw_text: str, bio_tokens: Iterable[str]) -> list[tuple[int, int]]:
    """Map each BIO token to a raw Python-code-point interval.

    The source release detags by joining tokens with whitespace.  Any skipped raw
    characters therefore have to be whitespace; accepting other gaps could hide a
    corrupt or differently tokenized source file.
    """
    cursor = 0
    offsets: list[tuple[int, int]] = []
    for token in bio_tokens:
        start = raw_text.find(token, cursor)
        if start < 0:
            raise ExperimentError(f"Cannot align BIO token {token!r} after code-point offset {cursor} in {raw_text!r}")
        gap = raw_text[cursor:start]
        if gap and not gap.isspace():
            raise ExperimentError(f"Non-whitespace gap {gap!r} before BIO token {token!r} in {raw_text!r}")
        end = start + len(token)
        offsets.append((start, end))
        cursor = end
    if raw_text[cursor:] and not raw_text[cursor:].isspace():
        raise ExperimentError(f"Unmapped raw suffix {raw_text[cursor:]!r} in {raw_text!r}")
    return offsets


def bio_gold_spans(raw_text: str, bio: BioSentence) -> tuple[GoldSpan, ...]:
    if len(bio.tokens) != len(bio.labels):
        raise ExperimentError("BIO tokens and labels differ in length.")
    offsets = align_bio_tokens(raw_text, bio.tokens)
    spans: list[GoldSpan] = []
    active_label: str | None = None
    active_start: int | None = None
    active_end: int | None = None
    for label_value, (start, end) in zip(bio.labels, offsets):
        prefix, _, entity_type = label_value.partition("-")
        entity_type = entity_type.upper()
        valid = prefix in {"B", "I"} and entity_type in LABELS
        continues = valid and prefix == "I" and entity_type == active_label
        if not continues and active_label is not None:
            assert active_start is not None and active_end is not None
            spans.append(GoldSpan(active_label, active_start, active_end, raw_text[active_start:active_end]))
            active_label = active_start = active_end = None
        if valid:
            if not continues:
                active_label, active_start = entity_type, start
            active_end = end
    if active_label is not None:
        assert active_start is not None and active_end is not None
        spans.append(GoldSpan(active_label, active_start, active_end, raw_text[active_start:active_end]))
    return tuple(spans)


def load_examples(language: str, limit: int, seed: int, root: Path = OPENNER_ROOT) -> list[SourceExample]:
    bio_path, raw_path = dataset_paths(language, root)
    bio_sentences = parse_bio(bio_path)
    raw_sentences = raw_path.read_text(encoding="utf-8").splitlines()
    if len(bio_sentences) < len(raw_sentences):
        raise ExperimentError(f"BIO source has {len(bio_sentences)} sentences, raw file has {len(raw_sentences)}.")
    if limit < 1 or limit > len(raw_sentences):
        raise ExperimentError(f"limit must be between 1 and {len(raw_sentences)}.")
    indices = list(range(len(raw_sentences)))
    random.Random(seed).shuffle(indices)
    selected = sorted(indices[:limit])
    examples: list[SourceExample] = []
    offset = 0
    for position, source_index in enumerate(selected):
        raw_text = raw_sentences[source_index]
        bio = bio_sentences[source_index]
        examples.append(SourceExample(source_index, raw_text, bio, bio_gold_spans(raw_text, bio), offset))
        offset += len(raw_text) + (1 if position + 1 < len(selected) else 0)
    return examples


def token_range_for_span(tokens: list[dict[str, Any]], start: int, end: int) -> tuple[int, int]:
    matching = [token for token in tokens if int(token["start_char"]) >= start and int(token["end_char"]) <= end]
    if not matching or int(matching[0]["start_char"]) != start or int(matching[-1]["end_char"]) != end:
        raise ExperimentError(f"Span [{start}, {end}) is not an exact API-token range.")
    expected = list(range(int(matching[0]["token_index"]), int(matching[-1]["token_index"]) + 1))
    actual = [int(token["token_index"]) for token in matching]
    if actual != expected:
        raise ExperimentError(f"Span [{start}, {end}) maps to non-contiguous API tokens {actual}.")
    return actual[0], actual[-1]


def map_gold_to_api_sentences(examples: list[SourceExample], api_sentences: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """Convert source-relative gold spans to exact API token ranges.

    A raw line may be split into multiple reader sentences by the server.  Mapping is
    therefore by document code-point offsets, not by assuming one raw line is one API
    sentence.
    """
    ordered = sorted(api_sentences, key=lambda sentence: int(sentence["start_char"]))
    mapped: dict[str, list[dict[str, Any]]] = {str(sentence["id"]): [] for sentence in ordered}
    for example in examples:
        for gold in example.gold_spans:
            start, end = example.raw_start + gold.start, example.raw_start + gold.end
            owners = [sentence for sentence in ordered if int(sentence["start_char"]) <= start and end <= int(sentence["end_char"])]
            if len(owners) != 1:
                raise ExperimentError(f"Gold span {gold.text!r} [{start}, {end}) is not contained by exactly one API sentence.")
            sentence = owners[0]
            start_token_index, end_token_index = token_range_for_span(sentence["tokens"], start, end)
            mapped[str(sentence["id"])].append(
                {
                    "tag_id": gold.label,
                    "start_token_index": start_token_index,
                    "end_token_index": end_token_index,
                    "start_char": start,
                    "end_char": end,
                    "text": gold.text,
                }
            )
    return mapped


def span_keys(spans: Iterable[dict[str, Any]], typed: bool = True) -> set[tuple[Any, ...]]:
    return {
        (str(span.get("sentence_id") or ""),)
        + ((str(span.get("tag_id") or span.get("label")),) if typed else ())
        + (int(span["start_token_index"]), int(span["end_token_index"]))
        for span in spans
    }


def score_spans(predicted: Iterable[dict[str, Any]], gold: Iterable[dict[str, Any]], *, typed: bool) -> dict[str, float | int]:
    predicted_keys, gold_keys = span_keys(predicted, typed), span_keys(gold, typed)
    true_positive = len(predicted_keys & gold_keys)
    precision = true_positive / len(predicted_keys) if predicted_keys else (1.0 if not gold_keys else 0.0)
    recall = true_positive / len(gold_keys) if gold_keys else 1.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {"tp": true_positive, "predicted": len(predicted_keys), "gold": len(gold_keys), "precision": precision, "recall": recall, "f1": f1}


def scoring_spans(sentence_id: str, spans: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{**span, "sentence_id": sentence_id} for span in spans]


def span_edit_count(original: Iterable[dict[str, Any]], final: Iterable[dict[str, Any]]) -> int:
    return len(span_keys(original) ^ span_keys(final))


def decision_payload(action: str, draft_id: str, draft_version: int, final_spans: list[dict[str, Any]] | None = None, error_reasons: list[str] | None = None) -> dict[str, Any]:
    if action not in {"confirm", "correct", "skip"}:
        raise ValueError("action must be confirm, correct, or skip.")
    payload: dict[str, Any] = {"action": action, "draft_id": draft_id, "draft_version": draft_version}
    if action == "correct":
        if final_spans is None:
            raise ValueError("correct decisions require final_spans.")
        payload["final_spans"] = final_spans
        if error_reasons:
            payload["error_reasons"] = error_reasons
    return payload


def unwrap_assistance(payload: dict[str, Any]) -> dict[str, Any]:
    """Normalize small response-shape variations while keeping the contract strict."""
    for key in ("assistance", "data"):
        if isinstance(payload.get(key), dict):
            payload = payload[key]
            break
    return payload


def ready_drafts(payload: dict[str, Any]) -> list[dict[str, Any]]:
    state = unwrap_assistance(payload)
    candidates = state.get("ready_drafts", state.get("drafts"))
    if candidates is None:
        queue = state.get("queue", [])
        candidates = queue.get("items", []) if isinstance(queue, dict) else queue
    if not isinstance(candidates, list):
        raise ExperimentError("Assistance response must contain a draft list.")
    return [item for item in candidates if isinstance(item, dict) and str(item.get("status", "ready")) == "ready"]


class HttpApi:
    def __init__(self, api_base: str, opener: Callable[..., Any] = urlopen) -> None:
        self.api_base = api_base.rstrip("/")
        self.opener = opener
        self.calls = 0
        self.latency_seconds = 0.0

    def request(self, method: str, path: str, payload: dict[str, Any] | None = None, body: bytes | None = None, content_type: str | None = None) -> dict[str, Any]:
        if payload is not None and body is not None:
            raise ValueError("Use either payload or body, not both.")
        headers = {"Accept": "application/json"}
        if payload is not None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        elif content_type:
            headers["Content-Type"] = content_type
        request = Request(f"{self.api_base}{path}", data=body, headers=headers, method=method)
        started = time.perf_counter()
        self.calls += 1
        try:
            with self.opener(request, timeout=30) as response:
                raw = response.read()
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise ExperimentError(f"{method} {path} returned HTTP {exc.code}: {detail}") from exc
        except URLError as exc:
            raise ExperimentError(f"{method} {path} failed: {exc.reason}") from exc
        finally:
            self.latency_seconds += time.perf_counter() - started
        try:
            return json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ExperimentError(f"{method} {path} did not return JSON.") from exc

    def json(self, method: str, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        return self.request(method, path, payload=payload)

    def multipart_txt(self, path: str, filename: str, text: str) -> dict[str, Any]:
        boundary = f"----AnnoPilot{uuid.uuid4().hex}"
        body = (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
            "Content-Type: text/plain; charset=utf-8\r\n\r\n"
        ).encode("utf-8") + text.encode("utf-8") + f"\r\n--{boundary}--\r\n".encode("ascii")
        return self.request("POST", path, body=body, content_type=f"multipart/form-data; boundary={boundary}")


def fetch_all_sentences(api: HttpApi, project_id: str, document_id: str) -> list[dict[str, Any]]:
    sentences: list[dict[str, Any]] = []
    offset = 0
    while True:
        page = api.json("GET", f"/api/projects/{project_id}/documents/{document_id}/sentences?offset={offset}&limit={PAGE_SIZE}")
        page_sentences = page.get("sentences")
        if not isinstance(page_sentences, list):
            raise ExperimentError("Sentence page lacks a sentences array.")
        sentences.extend(page_sentences)
        if not page.get("has_more"):
            return sentences
        if not page_sentences:
            raise ExperimentError("Sentence paging reported more results but returned none.")
        offset += len(page_sentences)


def assistance_draft_for_sentence(state: dict[str, Any], sentence_id: str) -> dict[str, Any] | None:
    for draft in ready_drafts(state):
        if str(draft.get("sentence_id")) == sentence_id:
            return draft
    return None


def failed_assistance_job_for_sentence(state: dict[str, Any], sentence_id: str) -> dict[str, Any] | None:
    assistance = unwrap_assistance(state)
    queue = assistance.get("queue", {})
    items = queue.get("items", []) if isinstance(queue, dict) else []
    for item in items if isinstance(items, list) else []:
        if isinstance(item, dict) and str(item.get("sentence_id")) == sentence_id and item.get("status") == "failed":
            return item
    return None


def wait_for_assistance_draft(
    api: HttpApi,
    project_id: str,
    document_id: str,
    sentence_id: str,
    *,
    timeout_seconds: float = DEFAULT_DRAFT_WAIT_SECONDS,
    poll_interval_seconds: float = DEFAULT_DRAFT_POLL_INTERVAL,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> dict[str, Any] | None:
    """Wait for a rolling draft once assistance has been activated.

    Before any tag has reached its five accepted human examples, callers receive
    ``None`` and continue the manual cold-start flow.  Once a tag is active, a
    missing current draft is a worker/readiness condition, not permission to turn
    the sentence into another manual example.
    """
    deadline = clock() + timeout_seconds
    while True:
        state = api.json("GET", f"/api/projects/{project_id}/documents/{document_id}/assistance")
        draft = assistance_draft_for_sentence(state, sentence_id)
        if draft is not None:
            return draft
        failed = failed_assistance_job_for_sentence(state, sentence_id)
        if failed is not None:
            message = str(failed.get("error_message") or "unknown worker failure")
            raise ExperimentError(f"Assistance job failed for sentence {sentence_id}: {message}")
        assistance = unwrap_assistance(state)
        if not assistance.get("enabled", True) or not assistance.get("active_tags", []):
            return None
        if clock() >= deadline:
            raise ExperimentError(
                f"Timed out after {timeout_seconds:.1f}s waiting for an assistance draft for sentence {sentence_id}."
            )
        sleep(max(0.0, poll_interval_seconds))


def draft_spans(draft: dict[str, Any]) -> list[dict[str, Any]]:
    spans = draft.get("spans", draft.get("suggestions", []))
    if not isinstance(spans, list):
        raise ExperimentError("Assistance draft must contain spans.")
    return [span for span in spans if isinstance(span, dict)]


def complete_skipped_drafts(
    api: HttpApi,
    project_id: str,
    document_id: str,
    skipped_draft_ids: set[str],
    gold_by_sentence: dict[str, list[dict[str, Any]]],
    counts: dict[str, int],
    predicted: list[dict[str, Any]],
    learning_curve: list[dict[str, Any]] | None = None,
) -> None:
    pending = set(skipped_draft_ids)
    while pending:
        state = api.json("GET", f"/api/projects/{project_id}/documents/{document_id}/assistance")
        ready = [draft for draft in ready_drafts(state) if str(draft.get("id")) in pending]
        if not ready:
            raise ExperimentError("A skipped draft did not reappear in the assistance queue.")
        for draft in ready:
            draft_id = str(draft["id"])
            sentence_id = str(draft["sentence_id"])
            gold = gold_by_sentence.get(sentence_id, [])
            proposed = draft_spans(draft)
            action = "confirm" if span_keys(proposed) == span_keys(gold) else "correct"
            api.json(
                "POST",
                f"/api/projects/{project_id}/sentences/{sentence_id}/assistance/decision",
                decision_payload(
                    action,
                    draft_id,
                    int(draft.get("draft_version", draft.get("version", 1))),
                    gold if action == "correct" else None,
                ),
            )
            counts[action] += 1
            counts["human_span_edits"] += span_edit_count(proposed, gold) if action == "correct" else 0
            predicted.extend(scoring_spans(sentence_id, proposed if action == "confirm" else gold))
            if learning_curve is not None:
                learning_curve.append(
                    {
                        "completed_sentences": counts["manual"] + counts["confirm"] + counts["correct"],
                        "confirmed": counts["confirm"],
                        "corrected": counts["correct"],
                        "manual": counts["manual"],
                    }
                )
            pending.discard(draft_id)


def run_experiment(
    api_base: str,
    language: str,
    limit: int,
    seed: int,
    project_id: str,
    output_dir: Path,
    skip_every: int = 0,
    root: Path = OPENNER_ROOT,
    opener: Callable[..., Any] = urlopen,
    draft_wait_seconds: float = DEFAULT_DRAFT_WAIT_SECONDS,
    draft_poll_interval: float = DEFAULT_DRAFT_POLL_INTERVAL,
) -> dict[str, Any]:
    examples = load_examples(language, limit, seed, root)
    api = HttpApi(api_base, opener=opener)
    schema = {
        "schema_version": "annopilot.tag_schema.v1",
        "record_type": "tag_schema",
        "tags": [{"id": label, "name": label, "description": f"OpenNER {label} entity", "examples": []} for label in LABELS],
    }
    api.json("POST", f"/api/projects/{project_id}/tags/schema/import", schema)
    imported = api.multipart_txt(f"/api/projects/{project_id}/import-txt", f"openner_{language}_{limit}_seed{seed}.txt", "\n".join(item.raw_text for item in examples))
    document_id = str(imported["document_id"])
    sentences = fetch_all_sentences(api, project_id, document_id)
    gold_by_sentence = map_gold_to_api_sentences(examples, sentences)

    counts = {"confirm": 0, "correct": 0, "skip": 0, "manual": 0, "human_span_edits": 0, "overwrite_violations": 0}
    final_predictions: list[dict[str, Any]] = []
    gold_all = [
        scored
        for sentence_id, spans in gold_by_sentence.items()
        for scored in scoring_spans(sentence_id, spans)
    ]
    draft_predictions: list[dict[str, Any]] = []
    assistance_gold: list[dict[str, Any]] = []
    assisted_sentence_count = 0
    draft_sentence_matches = 0
    skipped_draft_ids: set[str] = set()
    learning_curve: list[dict[str, Any]] = []

    for sentence in sentences:
        sentence_id = str(sentence["id"])
        gold = gold_by_sentence.get(sentence_id, [])
        draft = wait_for_assistance_draft(
            api,
            project_id,
            document_id,
            sentence_id,
            timeout_seconds=draft_wait_seconds,
            poll_interval_seconds=draft_poll_interval,
        )
        if draft is None:
            for span in gold:
                api.json("POST", f"/api/projects/{project_id}/sentences/{sentence_id}/annotations", span)
                counts["human_span_edits"] += 1
            api.json("POST", f"/api/projects/{project_id}/sentences/{sentence_id}/complete", {"completed": True, "answer": "accept"})
            counts["manual"] += 1
            final_predictions.extend(scoring_spans(sentence_id, gold))
        else:
            draft_id = str(draft["id"])
            draft_version = int(draft.get("draft_version", draft.get("version", 1)))
            proposed = draft_spans(draft)
            assisted_sentence_count += 1
            draft_predictions.extend(scoring_spans(sentence_id, proposed))
            assistance_gold.extend(scoring_spans(sentence_id, gold))
            if span_keys(proposed) == span_keys(gold):
                draft_sentence_matches += 1
            if skip_every and (counts["confirm"] + counts["correct"] + counts["skip"] + 1) % skip_every == 0 and draft_id not in skipped_draft_ids:
                api.json("POST", f"/api/projects/{project_id}/sentences/{sentence_id}/assistance/decision", decision_payload("skip", draft_id, draft_version))
                skipped_draft_ids.add(draft_id)
                counts["skip"] += 1
                continue
            if span_keys(proposed) == span_keys(gold):
                api.json("POST", f"/api/projects/{project_id}/sentences/{sentence_id}/assistance/decision", decision_payload("confirm", draft_id, draft_version))
                counts["confirm"] += 1
                final_predictions.extend(scoring_spans(sentence_id, proposed))
            else:
                api.json("POST", f"/api/projects/{project_id}/sentences/{sentence_id}/assistance/decision", decision_payload("correct", draft_id, draft_version, gold))
                counts["correct"] += 1
                counts["human_span_edits"] += span_edit_count(proposed, gold)
                final_predictions.extend(scoring_spans(sentence_id, gold))
        learning_curve.append({"completed_sentences": counts["manual"] + counts["confirm"] + counts["correct"], "confirmed": counts["confirm"], "corrected": counts["correct"], "manual": counts["manual"]})

    # A skipped draft must remain available and be completed on a later pass.
    complete_skipped_drafts(
        api,
        project_id,
        document_id,
        skipped_draft_ids,
        gold_by_sentence,
        counts,
        final_predictions,
        learning_curve,
    )

    typed = score_spans(draft_predictions, assistance_gold, typed=True)
    boundary = score_spans(draft_predictions, assistance_gold, typed=False)
    final_typed = score_spans(final_predictions, gold_all, typed=True)
    assisted_decisions = counts["confirm"] + counts["correct"]
    final_assistance = unwrap_assistance(
        api.json("GET", f"/api/projects/{project_id}/documents/{document_id}/assistance")
    )
    queue = final_assistance.get("queue", {}) if isinstance(final_assistance.get("queue"), dict) else {}
    queue_counts = queue.get("counts", queue) if isinstance(queue, dict) else {}
    failed_jobs = int(queue_counts.get("failed") or 0) if isinstance(queue_counts, dict) else 0
    successful_drafts = assisted_decisions
    validation_total = successful_drafts + failed_jobs
    token_usage = final_assistance.get("usage", final_assistance.get("token_usage", {}))
    token_usage = token_usage if isinstance(token_usage, dict) else {}
    result = {
        "schema_version": "annopilot.openner_assistance_experiment.v1",
        "language": language,
        "limit": limit,
        "source_example_count": len(examples),
        "reader_sentence_count": len(sentences),
        "seed": seed,
        "project_id": project_id,
        "document_id": document_id,
        "typed_exact": typed,
        "boundary": boundary,
        "sentence_exact": draft_sentence_matches / assisted_sentence_count if assisted_sentence_count else 0.0,
        "final_typed_exact": final_typed,
        "assisted_sentence_count": assisted_sentence_count,
        "decision_rates": {
            "confirm": counts["confirm"] / assisted_decisions if assisted_decisions else 0.0,
            "correct": counts["correct"] / assisted_decisions if assisted_decisions else 0.0,
        },
        "human_span_edits_per_sentence": counts["human_span_edits"] / len(sentences) if sentences else 0.0,
        "decisions": counts,
        "api_calls": api.calls,
        "latency_seconds": api.latency_seconds,
        "token_usage": token_usage,
        "validation": {
            "successful_drafts": successful_drafts,
            "failed_jobs": failed_jobs,
            "success_rate": successful_drafts / validation_total if validation_total else 1.0,
            "provider_attempts": int(token_usage.get("validation_attempts") or token_usage.get("api_calls") or 0),
            "retry_count": int(token_usage.get("validation_retries") or 0),
        },
        "alignment_coverage": {"gold_spans": len(gold_all), "mapped_gold_spans": len(gold_all), "coverage": 1.0},
        "overwrite_violations": counts["overwrite_violations"],
        "learning_curve": learning_curve,
    }
    write_results(result, output_dir)
    return result


def write_results(result: dict[str, Any], output_dir: Path) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    stem = f"openner_assistance_{result['language']}_{result['limit']}_seed{result['seed']}"
    json_path, markdown_path = output_dir / f"{stem}.json", output_dir / f"{stem}.md"
    json_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    typed, boundary, decisions = result["typed_exact"], result["boundary"], result["decisions"]
    final_typed = result.get("final_typed_exact", typed)
    rates = result.get("decision_rates", {})
    validation = result.get("validation", {})
    markdown_path.write_text(
        "# OpenNER Assistance Experiment\n\n"
        f"- Language: `{result['language']}`\n"
        f"- Source examples: `{result.get('source_example_count', result['limit'])}`\n"
        f"- Reader sentences: `{result.get('reader_sentence_count', result['limit'])}`\n"
        f"- Seed: `{result['seed']}`\n\n"
        "| Metric | Precision | Recall | F1 |\n| --- | ---: | ---: | ---: |\n"
        f"| Machine draft typed exact | {typed['precision']:.4f} | {typed['recall']:.4f} | {typed['f1']:.4f} |\n"
        f"| Machine draft boundary | {boundary['precision']:.4f} | {boundary['recall']:.4f} | {boundary['f1']:.4f} |\n"
        f"| Final submitted typed exact | {final_typed['precision']:.4f} | {final_typed['recall']:.4f} | {final_typed['f1']:.4f} |\n\n"
        f"Machine draft sentence exact: `{result['sentence_exact']:.4f}`  \n"
        f"Decisions: confirm `{decisions['confirm']}`, correct `{decisions['correct']}`, skip `{decisions['skip']}`, manual `{decisions['manual']}`  \n"
        f"Decision rates: confirm `{float(rates.get('confirm', 0.0)):.2%}`, correct `{float(rates.get('correct', 0.0)):.2%}`  \n"
        f"Human span edits: `{decisions['human_span_edits']}`  \n"
        f"Human span edits / sentence: `{float(result.get('human_span_edits_per_sentence', 0.0)):.4f}`  \n"
        f"Verifier success: `{float(validation.get('success_rate', 1.0)):.2%}`, retries `{int(validation.get('retry_count', 0))}`  \n"
        f"API calls: `{result['api_calls']}`, latency: `{result['latency_seconds']:.3f}s`  \n"
        f"Alignment coverage: `{result['alignment_coverage']['coverage']:.2%}`, overwrite violations: `{result['overwrite_violations']}`\n",
        encoding="utf-8",
    )
    return json_path, markdown_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api-base", required=True, help="AnnoPilot API origin, for example http://127.0.0.1:8888")
    parser.add_argument("--language", choices=("zh", "en"), required=True)
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--seed", type=int, default=9)
    parser.add_argument("--project-id", default="openner-experiment")
    parser.add_argument("--output-dir", type=Path, default=REPO_ROOT / "tmp" / "openner" / "experiments")
    parser.add_argument("--skip-every", type=int, default=0)
    parser.add_argument("--draft-wait-seconds", type=float, default=DEFAULT_DRAFT_WAIT_SECONDS)
    args = parser.parse_args(argv)
    if args.skip_every < 0:
        parser.error("--skip-every must be zero or positive")
    if args.draft_wait_seconds <= 0:
        parser.error("--draft-wait-seconds must be positive")
    try:
        result = run_experiment(
            args.api_base,
            args.language,
            args.limit,
            args.seed,
            args.project_id,
            args.output_dir,
            args.skip_every,
            draft_wait_seconds=args.draft_wait_seconds,
        )
    except ExperimentError as exc:
        print(f"OpenNER experiment failed: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
