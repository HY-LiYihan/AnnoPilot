from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

from backend.app.services.assistance import ASSISTANCE_LEASE_SECONDS


SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "experiments" / "openner_assistance.py"
SPEC = importlib.util.spec_from_file_location("openner_assistance", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
experiment = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = experiment
SPEC.loader.exec_module(experiment)


def test_experiment_wait_window_exceeds_worker_lease() -> None:
    assert experiment.DEFAULT_DRAFT_WAIT_SECONDS > ASSISTANCE_LEASE_SECONDS


def test_parse_bio_skips_docstart_and_builds_sentences(tmp_path: Path) -> None:
    source = tmp_path / "sample.bio"
    source.write_text("-DOCSTART- O\n\nAlice B-PER\nworks O\n\nAcme B-ORG\n", encoding="utf-8")

    sentences = experiment.parse_bio(source)

    assert [item.tokens for item in sentences] == [("Alice", "works"), ("Acme",)]
    assert [item.labels for item in sentences] == [("B-PER", "O"), ("B-ORG",)]


def test_alignment_and_bio_spans_support_english_and_chinese() -> None:
    english = experiment.BioSentence(("Alice", "works", "at", "Acme", "."), ("B-PER", "O", "O", "B-ORG", "O"))
    chinese = experiment.BioSentence(("张", "三", "在", "北", "京", "工", "作"), ("B-PER", "I-PER", "O", "B-LOC", "I-LOC", "O", "O"))

    assert experiment.align_bio_tokens("Alice works at Acme .", english.tokens) == [(0, 5), (6, 11), (12, 14), (15, 19), (20, 21)]
    assert [(span.label, span.start, span.end, span.text) for span in experiment.bio_gold_spans("Alice works at Acme .", english)] == [
        ("PER", 0, 5, "Alice"),
        ("ORG", 15, 19, "Acme"),
    ]
    assert [(span.label, span.start, span.end, span.text) for span in experiment.bio_gold_spans("张三在北京工作", chinese)] == [
        ("PER", 0, 2, "张三"),
        ("LOC", 3, 5, "北京"),
    ]


def test_alignment_rejects_non_whitespace_unmapped_characters() -> None:
    with pytest.raises(experiment.ExperimentError, match="Non-whitespace gap"):
        experiment.align_bio_tokens("Alice, works", ("Alice", "works"))


def test_gold_mapping_requires_exact_api_token_boundaries() -> None:
    example = experiment.SourceExample(
        source_index=0,
        raw_text="Alice works",
        bio=experiment.BioSentence(("Alice", "works"), ("B-PER", "O")),
        gold_spans=(experiment.GoldSpan("PER", 0, 5, "Alice"),),
        raw_start=0,
    )
    sentence = {
        "id": "sent-1",
        "start_char": 0,
        "end_char": 11,
        "tokens": [
            {"token_index": 0, "start_char": 0, "end_char": 5, "text": "Alice"},
            {"token_index": 1, "start_char": 6, "end_char": 11, "text": "works"},
        ],
    }

    assert experiment.map_gold_to_api_sentences([example], [sentence]) == {
        "sent-1": [
            {"tag_id": "PER", "start_token_index": 0, "end_token_index": 0, "start_char": 0, "end_char": 5, "text": "Alice"}
        ]
    }
    sentence["tokens"][0]["end_char"] = 4
    with pytest.raises(experiment.ExperimentError, match="exact API-token range"):
        experiment.map_gold_to_api_sentences([example], [sentence])


def test_metrics_are_typed_and_boundary_aware() -> None:
    gold = [
        {"tag_id": "PER", "start_token_index": 0, "end_token_index": 0},
        {"tag_id": "ORG", "start_token_index": 2, "end_token_index": 3},
    ]
    predicted = [
        {"tag_id": "LOC", "start_token_index": 0, "end_token_index": 0},
        {"tag_id": "ORG", "start_token_index": 2, "end_token_index": 3},
    ]

    typed = experiment.score_spans(predicted, gold, typed=True)
    boundary = experiment.score_spans(predicted, gold, typed=False)

    assert typed["tp"] == 1
    assert typed["f1"] == pytest.approx(0.5)
    assert boundary["tp"] == 2
    assert boundary["f1"] == pytest.approx(1.0)


def test_metrics_keep_identical_offsets_from_different_sentences_distinct() -> None:
    spans = [
        {"sentence_id": "s-1", "tag_id": "PER", "start_token_index": 0, "end_token_index": 0},
        {"sentence_id": "s-2", "tag_id": "PER", "start_token_index": 0, "end_token_index": 0},
    ]

    assert experiment.score_spans(spans, spans, typed=True)["gold"] == 2


def test_metrics_are_reported_for_each_openner_label() -> None:
    gold = [{"sentence_id": "s-1", "tag_id": "PER", "start_token_index": 0, "end_token_index": 0}]
    predicted = [{"sentence_id": "s-1", "tag_id": "ORG", "start_token_index": 0, "end_token_index": 0}]

    metrics = experiment.score_by_label(predicted, gold)

    assert list(metrics) == ["PER", "ORG", "LOC"]
    assert metrics["PER"]["typed_exact"]["recall"] == 0.0
    assert metrics["ORG"]["typed_exact"]["precision"] == 0.0
    assert metrics["LOC"]["typed_exact"]["f1"] == 1.0


def test_seed_selection_and_decision_payload_are_deterministic(tmp_path: Path) -> None:
    root = tmp_path / "openner"
    standardized = root / "standardized"
    chinese = standardized / "UNER_Chinese_GSD" / "cmn"
    english = standardized / "UNER_English_EWT" / "eng"
    chinese.mkdir(parents=True)
    english.mkdir(parents=True)
    (root / "raw").mkdir()
    content = "Alice B-PER\nworks O\n\nBob B-PER\nworks O\n\nAcme B-ORG\n\n"
    (chinese / "train.txt").write_text(content, encoding="utf-8")
    (english / "train.txt").write_text(content, encoding="utf-8")
    (root / "raw" / "openner_chinese_1000.txt").write_text("Alice works\nBob works\nAcme\n", encoding="utf-8")
    (root / "raw" / "openner_english_1000.txt").write_text("Alice works\nBob works\nAcme\n", encoding="utf-8")

    first = experiment.load_examples("en", 2, 9, root)
    second = experiment.load_examples("en", 2, 9, root)

    assert [(item.source_index, item.raw_text) for item in first] == [(item.source_index, item.raw_text) for item in second]
    assert experiment.decision_payload("confirm", "draft-1", 3) == {"action": "confirm", "draft_id": "draft-1", "draft_version": 3}
    assert experiment.decision_payload("correct", "draft-2", 4, [{"tag_id": "PER"}]) == {
        "action": "correct",
        "draft_id": "draft-2",
        "draft_version": 4,
        "final_spans": [{"tag_id": "PER"}],
    }


def test_ready_drafts_reads_api_queue_items() -> None:
    payload = {
        "enabled": True,
        "queue": {
            "items": [
                {"id": "queued", "sentence_id": "s-1", "status": "queued"},
                {"id": "ready", "sentence_id": "s-2", "status": "ready"},
            ]
        },
    }

    assert experiment.ready_drafts(payload) == [{"id": "ready", "sentence_id": "s-2", "status": "ready"}]


def test_wait_for_draft_fails_immediately_with_worker_error() -> None:
    class FailedJobApi:
        def json(self, _method: str, _path: str) -> dict:
            return {
                "enabled": True,
                "active_tags": [{"tag_id": "PER"}],
                "queue": {
                    "items": [
                        {
                            "id": "draft-failed",
                            "sentence_id": "s-1",
                            "status": "failed",
                            "error_message": "model_not_supported",
                        }
                    ]
                },
            }

    with pytest.raises(experiment.ExperimentError, match="model_not_supported"):
        experiment.wait_for_assistance_draft(
            FailedJobApi(),
            "default",
            "doc-1",
            "s-1",
            timeout_seconds=90,
            sleep=lambda _seconds: pytest.fail("failed jobs must not be polled again"),
        )


def test_complete_skipped_drafts_consumes_multiple_ready_items_once() -> None:
    class SkippedApi:
        def __init__(self) -> None:
            self.reads = 0
            self.decisions: list[dict] = []

        def json(self, method: str, path: str, payload: dict | None = None) -> dict:
            if method == "GET":
                self.reads += 1
                if self.reads > 1:
                    raise AssertionError("all ready skipped drafts should be consumed in one pass")
                return {
                    "queue": {
                        "items": [
                            {"id": "draft-1", "sentence_id": "s-1", "status": "ready", "draft_version": 1, "spans": []},
                            {"id": "draft-2", "sentence_id": "s-2", "status": "ready", "draft_version": 1, "spans": []},
                        ]
                    }
                }
            assert method == "POST" and path.endswith("/assistance/decision")
            self.decisions.append(payload or {})
            return {"completed": True}

    api = SkippedApi()
    counts = {"confirm": 0, "correct": 0, "manual": 1, "human_span_edits": 0}
    predicted: list[dict] = []
    learning_curve: list[dict] = []
    experiment.complete_skipped_drafts(
        api,
        "default",
        "doc-1",
        {"draft-1", "draft-2"},
        {"s-1": [], "s-2": []},
        counts,
        predicted,
        learning_curve,
    )

    assert api.reads == 1
    assert counts == {"confirm": 2, "correct": 0, "manual": 1, "human_span_edits": 0}
    assert [item["draft_id"] for item in api.decisions] == ["draft-1", "draft-2"]
    assert [point["completed_sentences"] for point in learning_curve] == [2, 3]


def test_run_experiment_waits_for_ready_draft_and_uses_usage(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "openner"
    english = root / "standardized" / "UNER_English_EWT" / "eng"
    english.mkdir(parents=True)
    (root / "raw").mkdir()
    (english / "train.txt").write_text("Alice B-PER\n", encoding="utf-8")
    (root / "raw" / "openner_english_1000.txt").write_text("Alice\n", encoding="utf-8")

    class DelayedDraftApi:
        instance: "DelayedDraftApi"

        def __init__(self, _api_base: str, opener: object = None) -> None:
            self.calls = 0
            self.latency_seconds = 0.0
            self.assistance_reads = 0
            self.paths: list[tuple[str, str, dict | None]] = []
            DelayedDraftApi.instance = self

        def json(self, method: str, path: str, payload: dict | None = None) -> dict:
            self.calls += 1
            self.paths.append((method, path, payload))
            if path.endswith("/tags/schema/import"):
                return {}
            if "/documents/doc-1/sentences?" in path:
                return {
                    "sentences": [
                        {
                            "id": "s-1",
                            "start_char": 0,
                            "end_char": 5,
                            "tokens": [{"token_index": 0, "start_char": 0, "end_char": 5, "text": "Alice"}],
                        }
                    ],
                    "has_more": False,
                }
            if path.endswith("/assistance"):
                self.assistance_reads += 1
                status = "queued" if self.assistance_reads == 1 else "ready"
                return {
                    "enabled": True,
                    "active_tags": [{"tag_id": "PER"}],
                    "queue": {
                        "items": [
                            {
                                "id": "draft-1",
                                "sentence_id": "s-1",
                                "status": status,
                                "draft_version": 1,
                                "spans": [{"tag_id": "PER", "start_token_index": 0, "end_token_index": 0}],
                            }
                        ]
                    },
                    "usage": {"prompt_tokens": 11, "completion_tokens": 3, "total_tokens": 14, "api_calls": 1},
                }
            if path.endswith("/assistance/decision"):
                assert payload == {"action": "confirm", "draft_id": "draft-1", "draft_version": 1}
                return {"completed": True}
            raise AssertionError(f"Unexpected API call: {method} {path}")

        def multipart_txt(self, path: str, filename: str, text: str) -> dict:
            self.calls += 1
            self.paths.append(("POST", path, None))
            assert filename == "openner_en_1_seed9.txt"
            assert text == "Alice"
            return {"document_id": "doc-1"}

    monkeypatch.setattr(experiment, "HttpApi", DelayedDraftApi)

    result = experiment.run_experiment(
        "http://example.test",
        "en",
        1,
        9,
        "experiment",
        tmp_path / "results",
        root=root,
        draft_wait_seconds=1,
        draft_poll_interval=0,
    )

    api = DelayedDraftApi.instance
    assert api.assistance_reads >= 2
    assert result["decisions"] == {
        "confirm": 1,
        "correct": 0,
        "skip": 0,
        "manual": 0,
        "human_span_edits": 0,
        "overwrite_violations": 0,
    }
    assert result["token_usage"]["total_tokens"] == 14
    assert not any("/annotations" in path or path.endswith("/complete") for _method, path, _payload in api.paths)


def test_write_results_uses_markdown_line_breaks_not_literal_escape_suffix(tmp_path: Path) -> None:
    result = {
        "language": "en",
        "limit": 1,
        "seed": 9,
        "typed_exact": {"precision": 1.0, "recall": 1.0, "f1": 1.0},
        "boundary": {"precision": 1.0, "recall": 1.0, "f1": 1.0},
        "sentence_exact": 1.0,
        "decisions": {"confirm": 1, "correct": 0, "skip": 0, "manual": 0, "human_span_edits": 0},
        "api_calls": 4,
        "latency_seconds": 0.125,
        "alignment_coverage": {"coverage": 1.0},
        "overwrite_violations": 0,
    }

    _json_path, markdown_path = experiment.write_results(result, tmp_path)
    markdown = markdown_path.read_text(encoding="utf-8")

    assert r"\n+" not in markdown
    assert "Machine draft sentence exact: `1.0000`  \nDecisions:" in markdown
