from pathlib import Path

from fastapi.testclient import TestClient

from backend.app.main import create_app
from backend.app.storage import AnnotationStorage


def test_accept_suggestion_returns_400_when_span_overlaps_annotation(tmp_path: Path) -> None:
    storage = AnnotationStorage(
        database_path=tmp_path / "runtime" / "annopilot.sqlite",
        data_root=tmp_path / "projects",
    )
    with TestClient(create_app(storage)) as client:
        tag = storage.create_tag("default", "Cue", examples=["小猫"])
        imported = storage.import_txt("default", "overlap-api.txt", "小猫看见小河。".encode("utf-8"))
        document = storage.get_document("default", imported["document_id"])
        sentence_id = document["sentences"][0]["id"]
        suggestions = storage.generate_suggestions(
            "default",
            imported["document_id"],
            limit_per_sentence=4,
            min_confidence=0.98,
        )["suggestions"]
        suggestion = next(item for item in suggestions if item["start_token_index"] <= 1 and item["end_token_index"] >= 0)
        storage.create_annotation("default", sentence_id, tag["id"], 0, 1)

        response = client.post(f"/api/projects/default/suggestions/{suggestion['id']}/accept")

        assert response.status_code == 400
        assert response.json()["detail"] == "Suggestion overlaps an existing annotation."
        with storage.connect() as conn:
            status = conn.execute(
                "SELECT status FROM annotation_suggestions WHERE id = ?",
                (suggestion["id"],),
            ).fetchone()["status"]
            annotation_count = conn.execute(
                "SELECT COUNT(*) AS count FROM annotations WHERE sentence_id = ?",
                (sentence_id,),
            ).fetchone()["count"]

        assert status == "pending"
        assert annotation_count == 1
