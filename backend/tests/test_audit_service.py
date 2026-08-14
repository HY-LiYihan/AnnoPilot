from backend.app.services.audit import AuditService


def test_annotation_import_skip_reason_counts_backfills_legacy_events() -> None:
    source_record_results = [
        {"status": "matched"},
        {"status": "skipped", "reason": "invalid_span"},
        {"status": "skipped", "reason": "invalid_span"},
        {"status": "skipped", "reason": "no_sentence_match"},
    ]

    assert AuditService._annotation_import_skip_reason_counts({}, source_record_results) == {
        "invalid_span": 2,
        "no_sentence_match": 1,
    }
    assert AuditService._annotation_import_skip_reason_counts(
        {"skip_reason_counts": {"invalid_spans": 3, "invalid_span": 0}},
        source_record_results,
    ) == {"invalid_spans": 3}
