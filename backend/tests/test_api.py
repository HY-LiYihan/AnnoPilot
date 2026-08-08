from pathlib import Path

from fastapi.testclient import TestClient

from backend.app.main import create_app
from backend.app.storage import AnnotationStorage


def make_client(tmp_path: Path) -> TestClient:
    storage = AnnotationStorage(
        database_path=tmp_path / "runtime" / "annopilot.sqlite",
        data_root=tmp_path / "projects",
    )
    app = create_app(storage)
    with TestClient(app) as client:
        client.storage = storage  # type: ignore[attr-defined]
        return client


def test_import_fetch_annotate_complete_and_export(tmp_path: Path) -> None:
    with TestClient(
        create_app(
            AnnotationStorage(
                database_path=tmp_path / "runtime" / "annopilot.sqlite",
                data_root=tmp_path / "projects",
            )
        )
    ) as client:
        response = client.post(
            "/api/projects/default/import-txt",
            files={"file": ("sample.txt", "The company reduced emissions. 这是第二句！", "text/plain")},
        )
        assert response.status_code == 200
        imported = response.json()
        document_id = imported["document_id"]
        assert imported["sentence_count"] == 2

        document_response = client.get(f"/api/projects/default/documents/{document_id}")
        assert document_response.status_code == 200
        document = document_response.json()
        sentence = document["sentences"][0]

        annotation_response = client.post(
            f"/api/projects/default/sentences/{sentence['id']}/annotations",
            json={"tag_id": "action", "start_token_index": 2, "end_token_index": 2},
        )
        assert annotation_response.status_code == 200
        assert annotation_response.json()["annotations"][0]["text"] == "reduced"

        complete_response = client.post(
            f"/api/projects/default/sentences/{sentence['id']}/complete",
            json={"completed": True},
        )
        assert complete_response.status_code == 200

        export_response = client.get(f"/api/projects/default/documents/{document_id}/export.jsonl")
        assert export_response.status_code == 200
        assert "reduced" in export_response.text
        assert "completed" in export_response.text

        event_path = tmp_path / "projects" / "default" / "events.jsonl"
        assert event_path.exists()
        assert "document.imported" in event_path.read_text(encoding="utf-8")


def test_empty_txt_returns_400(tmp_path: Path) -> None:
    with TestClient(
        create_app(
            AnnotationStorage(
                database_path=tmp_path / "runtime" / "annopilot.sqlite",
                data_root=tmp_path / "projects",
            )
        )
    ) as client:
        response = client.post(
            "/api/projects/default/import-txt",
            files={"file": ("empty.txt", "   \n", "text/plain")},
        )

        assert response.status_code == 400
