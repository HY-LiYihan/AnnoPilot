import json
from pathlib import Path

import pytest

from backend.app.storage import AnnotationStorage, ValidationError


def make_storage(tmp_path: Path) -> AnnotationStorage:
    storage = AnnotationStorage(
        database_path=tmp_path / "runtime" / "annopilot.sqlite",
        data_root=tmp_path / "projects",
    )
    storage.initialize()
    return storage


def test_accept_suggestion_rolls_back_when_decision_event_enqueue_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    storage = make_storage(tmp_path)
    tag = storage.create_tag("default", "角色", examples=["小猫"])
    imported = storage.import_txt("default", "decision-service.txt", "小猫看见小河。".encode("utf-8"))
    suggestions = storage.generate_suggestions(
        "default",
        imported["document_id"],
        limit_per_sentence=4,
        min_confidence=0.98,
    )["suggestions"]
    suggestion = next(item for item in suggestions if item["tag_id"] == tag["id"])

    original_enqueue = storage.event_outbox.enqueue

    def fail_on_decision_event(conn, project_id: str, payload: dict) -> dict:
        if payload.get("type") == "suggestion.accepted":
            raise RuntimeError("synthetic enqueue failure")
        return original_enqueue(conn, project_id, payload)

    monkeypatch.setattr(storage.event_outbox, "enqueue", fail_on_decision_event)

    with pytest.raises(RuntimeError, match="synthetic enqueue failure"):
        storage.accept_suggestion("default", suggestion["id"])

    with storage.connect() as conn:
        status = conn.execute("SELECT status FROM annotation_suggestions WHERE id = ?", (suggestion["id"],)).fetchone()["status"]
        annotation_count = conn.execute(
            "SELECT COUNT(*) AS count FROM annotations WHERE source_suggestion_id = ?",
            (suggestion["id"],),
        ).fetchone()["count"]
        outbox_events = [json.loads(row["event_json"]) for row in conn.execute("SELECT event_json FROM event_outbox").fetchall()]

    assert status == "pending"
    assert annotation_count == 0
    assert not any(event.get("source_suggestion_id") == suggestion["id"] for event in outbox_events)
    assert not any(event.get("type") == "suggestion.accepted" and event.get("suggestion_id") == suggestion["id"] for event in outbox_events)


def test_event_outbox_flush_skips_events_already_written_to_jsonl(tmp_path: Path) -> None:
    storage = make_storage(tmp_path)
    storage.append_event("default", {"type": "test.outbox_idempotency"})
    event_path = tmp_path / "projects" / "default" / "events.jsonl"
    written_events = [json.loads(line) for line in event_path.read_text(encoding="utf-8").splitlines()]
    assert len(written_events) == 1
    event_id = written_events[0]["event_id"]

    with storage.connect() as conn:
        conn.execute("UPDATE event_outbox SET flushed_at = NULL WHERE id = ?", (event_id,))

    assert storage.flush_event_outbox("default") == 1

    recovered_events = [json.loads(line) for line in event_path.read_text(encoding="utf-8").splitlines()]
    with storage.connect() as conn:
        pending = conn.execute(
            "SELECT COUNT(*) AS count FROM event_outbox WHERE id = ? AND flushed_at IS NULL",
            (event_id,),
        ).fetchone()["count"]

    assert [event["event_id"] for event in recovered_events] == [event_id]
    assert pending == 0


def test_accept_suggestion_rejects_overlap_without_mutating_state(tmp_path: Path) -> None:
    storage = make_storage(tmp_path)
    tag = storage.create_tag("default", "Cue", examples=["小猫"])
    imported = storage.import_txt("default", "overlap-decision.txt", "小猫看见小河。".encode("utf-8"))
    sentence_id = storage.get_document("default", imported["document_id"])["sentences"][0]["id"]
    suggestions = storage.generate_suggestions("default", imported["document_id"], limit_per_sentence=4, min_confidence=0.98)["suggestions"]
    suggestion = next(item for item in suggestions if item["sentence_id"] == sentence_id and item["start_token_index"] <= 1 and item["end_token_index"] >= 0)
    storage.create_annotation("default", sentence_id, tag["id"], 0, 1)

    with pytest.raises(ValidationError, match="overlaps an existing annotation"):
        storage.accept_suggestion("default", suggestion["id"])

    with storage.connect() as conn:
        status = conn.execute("SELECT status FROM annotation_suggestions WHERE id = ?", (suggestion["id"],)).fetchone()["status"]
        annotation_count = conn.execute("SELECT COUNT(*) AS count FROM annotations WHERE sentence_id = ?", (sentence_id,)).fetchone()["count"]
        accepted_events = conn.execute("SELECT COUNT(*) AS count FROM event_outbox WHERE event_json LIKE '%suggestion.accepted%'").fetchone()["count"]

    assert status == "pending"
    assert annotation_count == 1
    assert accepted_events == 0
