from __future__ import annotations

import json

from backend.app.services.export_verification import ExportVerificationService


TAG_SCHEMA = {
    "schema_version": "annopilot.tag_schema.v1",
    "content_sha256": "test-schema",
    "tags": [{"id": "cue", "name": "Cue"}],
}


def _jsonl(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=False) + "\n"


def _verification_report(prodigy_record: dict) -> dict:
    service = ExportVerificationService("annopilot.goldsmith_verification_report.v1")
    line = service.build_lines(
        project_id="default",
        document_id="doc_demo",
        document={"metrics": {"sentence_count": 1, "annotation_count": len(prodigy_record.get("spans") or [])}},
        generated_at="2026-08-14T00:00:00+00:00",
        tag_schema=TAG_SCHEMA,
        prodigy_lines=[_jsonl(prodigy_record)],
        prodigy_spans_lines=[_jsonl({**prodigy_record, "_view_id": "spans_manual"})],
        goldsmith_candidate_run_lines=[],
        goldsmith_review_task_lines=[],
        goldsmith_prompt_package_lines=[],
    )[0]
    return json.loads(line)


def test_export_verification_warns_on_overlapping_prodigy_spans() -> None:
    report = _verification_report(
        {
            "text": "clearly shows improvement",
            "answer": "accept",
            "_view_id": "ner_manual",
            "spans": [
                {"start": 0, "end": 13, "text": "clearly shows", "label": "Cue"},
                {"start": 8, "end": 13, "text": "shows", "label": "Cue"},
            ],
            "meta": {"sentence_id": "sent_demo"},
        }
    )

    assert report["summary"]["status"] == "warning"
    overlap_issues = [issue for issue in report["issues"] if issue["code"] == "overlapping_spans"]
    assert {issue["artifact"] for issue in overlap_issues} == {"prodigy_jsonl", "prodigy_spans_jsonl"}
    assert all(issue["severity"] == "warning" for issue in overlap_issues)
    assert overlap_issues[0]["left_span"] == {"start": 0, "end": 13, "label": "Cue"}
    assert overlap_issues[0]["right_span"] == {"start": 8, "end": 13, "label": "Cue"}


def test_export_verification_allows_adjacent_prodigy_spans() -> None:
    report = _verification_report(
        {
            "text": "may show",
            "answer": "accept",
            "_view_id": "ner_manual",
            "spans": [
                {"start": 0, "end": 3, "text": "may", "label": "Cue"},
                {"start": 4, "end": 8, "text": "show", "label": "Cue"},
            ],
            "meta": {"sentence_id": "sent_demo"},
        }
    )

    assert report["summary"]["status"] == "ok"
    assert not [issue for issue in report["issues"] if issue["code"] == "overlapping_spans"]
