from __future__ import annotations

import json

from backend.app.services.bootstrap_report import GoldsmithBootstrapReportService


def _jsonl(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=False) + "\n"


def test_bootstrap_report_top_entity_tokens_skip_zero_entity_noise() -> None:
    service = GoldsmithBootstrapReportService("annopilot.goldsmith_bootstrap_report.v1")

    lines = service.build_lines(
        project_id="default",
        document_id="doc_demo",
        generated_at="2026-08-14T00:00:00+00:00",
        document={
            "document": {"filename": "demo.txt"},
            "metrics": {
                "sentence_count": 1,
                "completed_count": 0,
                "progress": 0,
                "annotation_count": 1,
                "suggestion_status_counts": {"pending": 0},
            },
        },
        prodigy_readiness={"status": "needs_attention", "blockers": ["complete sentences"]},
        goldsmith_review_queue_lines=[],
        goldsmith_consistency_score_lines=[],
        goldsmith_label_statistics_lines=[
            _jsonl(
                {
                    "token": "noise",
                    "entity_count": 0,
                    "entity_probability": 0,
                    "context_probability": 0,
                    "other_probability": 1,
                }
            ),
            _jsonl(
                {
                    "token": "实体",
                    "entity_count": 2,
                    "entity_probability": 0.67,
                    "context_probability": 0.33,
                    "other_probability": 0,
                }
            ),
        ],
        goldsmith_reflection_plan_lines=[],
        goldsmith_review_task_lines=[],
        goldsmith_verification_report_lines=[_jsonl({"summary": {"status": "ok", "issue_count": 0, "error_count": 0, "warning_count": 0}})],
    )

    report = "".join(lines)

    assert "| 实体 | 2 | 0.67 | 0.33 | 0.00 |" in report
    assert "| noise |" not in report


def test_bootstrap_report_top_entity_tokens_empty_when_no_entities() -> None:
    service = GoldsmithBootstrapReportService("annopilot.goldsmith_bootstrap_report.v1")

    lines = service.build_lines(
        project_id="default",
        document_id="doc_empty",
        generated_at="2026-08-14T00:00:00+00:00",
        document={"document": {"filename": "empty.txt"}, "metrics": {}},
        prodigy_readiness={"status": "needs_attention"},
        goldsmith_review_queue_lines=[],
        goldsmith_consistency_score_lines=[],
        goldsmith_label_statistics_lines=[
            _jsonl(
                {
                    "token": "ordinary",
                    "entity_count": 0,
                    "entity_probability": 0,
                    "context_probability": 0,
                    "other_probability": 1,
                }
            )
        ],
        goldsmith_reflection_plan_lines=[],
        goldsmith_review_task_lines=[],
        goldsmith_verification_report_lines=[],
    )

    report = "".join(lines)

    assert "| _none_ | 0 | 0.00 | 0.00 | 0.00 |" in report
    assert "| ordinary |" not in report


def test_bootstrap_report_uses_full_review_queue_summary_from_truncated_export() -> None:
    service = GoldsmithBootstrapReportService("annopilot.goldsmith_bootstrap_report.v1")
    queue_meta = {
        "total_queue_items": 150,
        "rosetta_route_counts": {"low": 70, "medium": 60, "high": 20},
    }

    lines = service.build_lines(
        project_id="default",
        document_id="doc_large",
        generated_at="2026-08-15T00:00:00+00:00",
        document={
            "document": {"filename": "large.txt"},
            "metrics": {"suggestion_status_counts": {"pending": 150}},
        },
        prodigy_readiness={"status": "needs_attention", "blockers": ["pending suggestions"]},
        goldsmith_review_queue_lines=[
            _jsonl({"rank": rank, "sentence_index": rank - 1, "meta": queue_meta})
            for rank in (1, 2)
        ],
        goldsmith_consistency_score_lines=[_jsonl({"rosetta_route": "high"})],
        goldsmith_label_statistics_lines=[],
        goldsmith_reflection_plan_lines=[],
        goldsmith_review_task_lines=[],
        goldsmith_verification_report_lines=[],
    )

    report = "".join(lines)

    assert "- Queue size: 150" in report
    assert "| high | 20 |" in report
    assert "| medium | 60 |" in report
    assert "| low | 70 |" in report
