from pathlib import Path

from fastapi.testclient import TestClient

from backend.app.main import create_app
from backend.app.storage import AnnotationStorage


def test_openner_preset_replaces_project_with_empty_label_schema(tmp_path: Path) -> None:
    storage = AnnotationStorage(
        database_path=tmp_path / "runtime" / "annopilot.sqlite",
        data_root=tmp_path / "projects",
    )
    with TestClient(create_app(storage)) as client:
        assert client.post("/api/projects/default/tags", json={"name": "Existing label"}).status_code == 200
        response = client.post(
            "/api/projects/default/sample-presets/openner-zh-en-1000/load",
            json={"generate_suggestions": False},
        )

        assert response.status_code == 200
        loaded = response.json()
        assert loaded["filename"] == "openner-zh-en-1000.txt"
        assert loaded["sentence_count"] >= 2000
        assert loaded["suggestions_created"] == 0
        assert loaded["tags"] == []
        assert client.get("/api/projects/default/tags").json()["tags"] == []


def test_project_reset_removes_tags_with_runtime_data(tmp_path: Path) -> None:
    storage = AnnotationStorage(
        database_path=tmp_path / "runtime" / "annopilot.sqlite",
        data_root=tmp_path / "projects",
    )
    with TestClient(create_app(storage)) as client:
        assert client.post("/api/projects/default/tags", json={"name": "PER"}).status_code == 200
        assert client.post(
            "/api/projects/default/import-txt",
            files={"file": ("example.txt", "Ada met Bob.", "text/plain")},
        ).status_code == 200

        response = client.post("/api/projects/default/reset")

        assert response.status_code == 200
        assert client.get("/api/projects/default/tags").json()["tags"] == []
        assert client.get("/api/projects/default/documents").json()["documents"] == []
