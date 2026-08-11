import json
from pathlib import Path

import pytest

from backend.app.storage import AnnotationStorage


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
