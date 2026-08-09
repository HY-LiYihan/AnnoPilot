import hashlib
import json
from pathlib import Path

from fastapi.testclient import TestClient

from backend.app.main import create_app
from backend.app.rebuild import rebuild_project_from_events
from backend.app.storage import AnnotationStorage


class FakeSuggestionReviewer:
    def review(self, context: dict) -> dict:
        assert context["suggestion"]["text"]
        assert context["suggestion"]["span_context"]
        assert f"[{context['suggestion']['text']}]" in context["suggestion"]["span_context"]
        return {
            "model": "fake-gpt5.5",
            "recommendation": "accept",
            "confidence": 0.91,
            "rationale": "候选词面和词性标签匹配。",
        }


def make_client(tmp_path: Path) -> TestClient:
    storage = AnnotationStorage(
        database_path=tmp_path / "runtime" / "annopilot.sqlite",
        data_root=tmp_path / "projects",
    )
    app = create_app(storage)
    with TestClient(app) as client:
        client.storage = storage  # type: ignore[attr-defined]
        return client


def seed_pos_span_labels(client: TestClient) -> dict[str, dict]:
    specs = [
        {
            "name": "名词",
            "description": "人、物、地点、抽象概念等实体或对象。",
            "examples": ["小猫", "柳树", "小河", "石桥", "叶子", "太阳", "男孩", "书包", "爪子", "水流", "桥边"],
        },
        {
            "name": "动词",
            "description": "动作、变化、状态或行为。",
            "examples": ["发芽", "走来", "看见", "伸出", "碰", "漂走", "坐", "看着", "升起来", "经过", "笑", "说", "抬起", "回答"],
        },
        {
            "name": "形容词",
            "description": "性质、状态、颜色、程度等修饰性词语。",
            "examples": ["金色", "安静", "轻轻", "慢慢"],
        },
    ]
    labels: dict[str, dict] = {}
    for spec in specs:
        response = client.post("/api/projects/default/tags", json=spec)
        assert response.status_code == 200
        labels[spec["name"]] = response.json()["tag"]
    return labels


def test_import_fetch_annotate_complete_and_export(tmp_path: Path) -> None:
    storage = AnnotationStorage(
        database_path=tmp_path / "runtime" / "annopilot.sqlite",
        data_root=tmp_path / "projects",
    )
    with TestClient(
        create_app(storage)
    ) as client:
        response = client.post(
            "/api/projects/default/import-txt",
            files={"file": ("sample.txt", "The company reduced emissions. 这是第二句！", "text/plain")},
        )
        assert response.status_code == 200
        imported = response.json()
        document_id = imported["document_id"]
        assert imported["sentence_count"] == 2
        assert imported["tags"] == []

        tag_response = client.post(
            "/api/projects/default/tags",
            json={"name": "动词", "examples": ["reduced"]},
        )
        assert tag_response.status_code == 200
        tag = tag_response.json()["tag"]
        assert tag["shortcut"] == "1"
        assert tag["color"] == "#0b7565"

        document_response = client.get(f"/api/projects/default/documents/{document_id}")
        assert document_response.status_code == 200
        document = document_response.json()
        sentence = document["sentences"][0]

        annotation_response = client.post(
            f"/api/projects/default/sentences/{sentence['id']}/annotations",
            json={"tag_id": tag["id"], "start_token_index": 2, "end_token_index": 2},
        )
        assert annotation_response.status_code == 200
        created_annotation = annotation_response.json()["annotations"][0]
        assert created_annotation["text"] == "reduced"
        assert created_annotation["source"] == "human"
        assert created_annotation["source_suggestion_id"] is None

        complete_response = client.post(
            f"/api/projects/default/sentences/{sentence['id']}/complete",
            json={"completed": True},
        )
        assert complete_response.status_code == 200

        export_response = client.get(f"/api/projects/default/documents/{document_id}/export.jsonl")
        assert export_response.status_code == 200
        exported_lines = [json.loads(line) for line in export_response.text.splitlines()]
        assert exported_lines[0]["schema_version"] == "annopilot.task.v1"
        assert exported_lines[0]["record_type"] == "annotation_task"
        assert exported_lines[0]["answer"] == "accept"
        assert exported_lines[0]["_view_id"] == "spans_manual"
        assert exported_lines[0]["_session_id"] == f"annopilot-default-{document_id}-human"
        assert exported_lines[0]["_annotator_id"] == "annopilot-human"
        assert isinstance(exported_lines[0]["_input_hash"], int)
        assert isinstance(exported_lines[0]["_task_hash"], int)
        assert exported_lines[0]["meta"]["session_id"] == exported_lines[0]["_session_id"]
        assert exported_lines[0]["meta"]["annotator_id"] == exported_lines[0]["_annotator_id"]
        assert exported_lines[0]["spans"][0]["text"] == "reduced"
        assert exported_lines[0]["spans"][0]["source"] == "human"
        assert exported_lines[0]["tokens"][0]["start"] == 0
        assert exported_lines[1]["_session_id"] == f"annopilot-default-{document_id}-unannotated"
        assert exported_lines[1]["_annotator_id"] == "annopilot-unannotated"

        prodigy_export_response = client.get(f"/api/projects/default/documents/{document_id}/export.prodigy.jsonl")
        assert prodigy_export_response.status_code == 200
        prodigy_lines = [json.loads(line) for line in prodigy_export_response.text.splitlines()]
        assert prodigy_lines[0]["_view_id"] == "ner_manual"
        assert prodigy_lines[0]["_session_id"] == f"annopilot-default-{document_id}-human"
        assert prodigy_lines[0]["_annotator_id"] == "annopilot-human"
        assert prodigy_lines[0]["answer"] == "accept"
        assert prodigy_lines[0]["text"] == "The company reduced emissions."
        assert prodigy_lines[0]["tokens"][0] == {"text": "The", "start": 0, "end": 3, "id": 0, "ws": True}
        assert prodigy_lines[0]["spans"][0] == {
            "start": 12,
            "end": 19,
            "token_start": 2,
            "token_end": 2,
            "label": "动词",
        }
        assert isinstance(prodigy_lines[0]["_input_hash"], int)
        assert isinstance(prodigy_lines[0]["_task_hash"], int)
        assert prodigy_lines[0]["meta"]["source"] == "annopilot"
        assert prodigy_lines[0]["meta"]["annotation_sources"] == [
            {"annotation_id": created_annotation["id"], "label_id": tag["id"], "source": "human"}
        ]
        assert prodigy_lines[1]["answer"] == "ignore"
        assert prodigy_lines[1]["_session_id"] == f"annopilot-default-{document_id}-unannotated"
        assert prodigy_lines[1]["_annotator_id"] == "annopilot-unannotated"
        assert prodigy_lines[1]["tokens"][0]["start"] == 0
        assert prodigy_lines[1]["meta"]["completed"] is False

        prodigy_spans_export_response = client.get(f"/api/projects/default/documents/{document_id}/export.prodigy.spans.jsonl")
        assert prodigy_spans_export_response.status_code == 200
        prodigy_spans_lines = [json.loads(line) for line in prodigy_spans_export_response.text.splitlines()]
        assert prodigy_spans_lines[0]["_view_id"] == "spans_manual"
        assert prodigy_spans_lines[0]["spans"] == prodigy_lines[0]["spans"]
        assert prodigy_spans_lines[1]["tokens"][0]["start"] == 0

        event_export_response = client.get("/api/projects/default/events.jsonl")
        assert event_export_response.status_code == 200
        events = [json.loads(line) for line in event_export_response.text.splitlines()]
        assert events[0]["schema_version"] == "annopilot.event.v1"
        assert events[0]["record_type"] == "event"
        assert events[0]["actor_type"] == "human"
        assert events[0]["actor_id"] == "annopilot-human"
        assert events[0]["type"] == "document.imported"
        assert events[0]["snapshot_version"] == "annopilot.import_snapshot.v1"
        assert events[0]["text"] == "The company reduced emissions. 这是第二句！"
        assert events[0]["text_sha256"] == hashlib.sha256(events[0]["text"].encode("utf-8")).hexdigest()
        assert events[0]["sentences"][0]["id"] == sentence["id"]
        assert events[0]["sentences"][0]["tokens"][0] == {
            "id": sentence["tokens"][0]["id"],
            "token_index": 0,
            "text": "The",
            "start_char": 0,
            "end_char": 3,
        }
        created_event = next(event for event in events if event["type"] == "annotation.created")
        assert created_event["source"] == "human"
        assert created_event["source_suggestion_id"] is None

        tag_schema_response = client.get("/api/projects/default/tags/schema.json")
        assert tag_schema_response.status_code == 200
        assert tag_schema_response.headers["content-disposition"] == 'attachment; filename="default-tag-schema.json"'
        tag_schema = tag_schema_response.json()
        assert tag_schema["schema_version"] == "annopilot.tag_schema.v1"
        assert tag_schema["record_type"] == "tag_schema"
        assert len(tag_schema["content_sha256"]) == 64
        assert tag_schema["retrieval"] == "character_rag_lexical_examples"
        assert tag_schema["tags"][0]["name"] == "动词"
        assert "reduced" in tag_schema["tags"][0]["examples"]
        assert "usage_count" not in tag_schema["tags"][0]

        manifest_response = client.get(f"/api/projects/default/documents/{document_id}/export.manifest.json")
        assert manifest_response.status_code == 200
        assert manifest_response.headers["content-disposition"] == f'attachment; filename="{document_id}.manifest.json"'
        manifest = manifest_response.json()
        assert manifest["schema_version"] == "annopilot.export_manifest.v1"
        assert manifest["record_type"] == "export_manifest"
        assert manifest["document"]["id"] == document_id
        assert manifest["metrics"]["answer_counts"] == {"accept": 1, "reject": 0, "ignore": 0, "pending": 1}
        assert manifest["annotation_source_counts"] == {"human": 1}
        assert manifest["source_run_ids"] == []
        assert manifest["annotation_imports"] == []
        assert manifest["event_audit"]["rebuild_status"] == "ready"
        assert manifest["event_audit"]["event_count"] == manifest["artifacts"]["events_jsonl"]["line_count"]
        assert manifest["event_audit"]["actor_type_counts"] == {"human": manifest["event_audit"]["event_count"]}
        assert manifest["event_audit"]["actor_id_counts"] == {"annopilot-human": manifest["event_audit"]["event_count"]}
        assert manifest["artifacts"]["tasks_jsonl"]["line_count"] == 2
        assert manifest["artifacts"]["tasks_jsonl"]["sha256"] == hashlib.sha256(export_response.text.encode("utf-8")).hexdigest()
        assert manifest["artifacts"]["prodigy_jsonl"]["sha256"] == hashlib.sha256(prodigy_export_response.text.encode("utf-8")).hexdigest()
        assert manifest["artifacts"]["prodigy_spans_jsonl"]["schema_version"] == "prodigy.spans_manual.compat.v1"
        assert manifest["artifacts"]["prodigy_spans_jsonl"]["sha256"] == hashlib.sha256(prodigy_spans_export_response.text.encode("utf-8")).hexdigest()
        assert manifest["artifacts"]["events_jsonl"]["sha256"] == hashlib.sha256(event_export_response.text.encode("utf-8")).hexdigest()
        assert manifest["artifacts"]["tag_schema_json"]["schema_version"] == "annopilot.tag_schema.v1"
        assert manifest["artifacts"]["tag_schema_json"]["line_count"] == 1
        assert manifest["artifacts"]["tag_schema_json"]["content_sha256"] == tag_schema["content_sha256"]

        event_path = tmp_path / "projects" / "default" / "events.jsonl"
        assert event_path.exists()
        assert "document.imported" in event_path.read_text(encoding="utf-8")
        with storage.connect() as conn:
            outbox = conn.execute("SELECT COUNT(*) AS total, SUM(flushed_at IS NULL) AS pending FROM event_outbox").fetchone()
        assert outbox["total"] >= 3
        assert outbox["pending"] == 0

        audit_response = client.get("/api/projects/default/audit")
        assert audit_response.status_code == 200
        audit = audit_response.json()
        assert audit["rebuild_status"] == "ready"
        assert audit["pending_outbox_count"] == 0
        assert audit["invalid_event_count"] == 0
        assert audit["non_replayable_event_count"] == 0
        assert audit["replay_issue_counts"] == {}
        assert audit["replay_issues"] == []
        assert audit["event_types"]["document.imported"] == 1
        assert audit["actor_type_counts"] == {"human": audit["event_count"]}
        assert audit["actor_id_counts"] == {"annopilot-human": audit["event_count"]}
        assert "annopilot.event.v1" in audit["schema_versions"]


def test_health_reports_llm_runtime_without_secret(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("LLM_BASE_URL", "https://api.aixhan.com/v1")
    monkeypatch.setenv("LLM_API_KEY", "sk-test-secret")
    monkeypatch.setenv("LLM_MODEL", "gpt5.5")
    with TestClient(
        create_app(
            AnnotationStorage(
                database_path=tmp_path / "runtime" / "annopilot.sqlite",
                data_root=tmp_path / "projects",
            )
        )
    ) as client:
        response = client.get("/api/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "llm_configured": True,
        "llm_model": "gpt5.5",
        "llm_base_host": "api.aixhan.com",
    }
    assert "sk-test-secret" not in response.text


def test_static_fallback_can_be_disabled_for_split_deploy(tmp_path: Path, monkeypatch) -> None:
    static_dir = tmp_path / "static"
    static_dir.mkdir()
    (static_dir / "index.html").write_text("<div>SPA</div>", encoding="utf-8")
    monkeypatch.setenv("STATIC_DIR", str(static_dir))
    monkeypatch.setenv("SERVE_STATIC", "false")

    storage = AnnotationStorage(
        database_path=tmp_path / "runtime" / "annopilot.sqlite",
        data_root=tmp_path / "projects",
    )
    with TestClient(create_app(storage)) as client:
        response = client.get("/workspace")

    assert response.status_code == 404


def test_cors_allow_origins_can_be_configured_from_env(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("CORS_ALLOW_ORIGINS", "https://app.example.com, https://admin.example.com")
    storage = AnnotationStorage(
        database_path=tmp_path / "runtime" / "annopilot.sqlite",
        data_root=tmp_path / "projects",
    )

    with TestClient(create_app(storage)) as client:
        response = client.options(
            "/api/health",
            headers={"Origin": "https://app.example.com", "Access-Control-Request-Method": "GET"},
        )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "https://app.example.com"


def test_document_summary_and_sentence_paging(tmp_path: Path) -> None:
    storage = AnnotationStorage(
        database_path=tmp_path / "runtime" / "annopilot.sqlite",
        data_root=tmp_path / "projects",
    )
    with TestClient(create_app(storage)) as client:
        response = client.post(
            "/api/projects/default/import-txt",
            files={"file": ("sample.txt", "第一句。第二句。第三句。第四句。", "text/plain")},
        )
        assert response.status_code == 200
        document_id = response.json()["document_id"]

        summary_response = client.get(f"/api/projects/default/documents/{document_id}/summary")
        assert summary_response.status_code == 200
        summary = summary_response.json()
        assert "sentences" not in summary
        assert summary["document"]["sentence_count"] == 4
        assert summary["document"]["token_count"] > 0
        assert summary["metrics"]["sentence_count"] == 4
        assert [item["index"] for item in summary["queue"]] == [0, 1, 2, 3]
        assert all(item["completed"] is False for item in summary["queue"])
        assert all(item["suggestion_count"] == 0 for item in summary["queue"])

        page_response = client.get(f"/api/projects/default/documents/{document_id}/sentences?offset=1&limit=2")
        assert page_response.status_code == 200
        page = page_response.json()
        assert page["offset"] == 1
        assert page["limit"] == 2
        assert page["total"] == 4
        assert page["has_more"] is True
        assert [sentence["index"] for sentence in page["sentences"]] == [1, 2]
        assert page["sentences"][0]["text"] == "第二句。"
        assert page["sentences"][0]["tokens"]

        legacy_response = client.get(f"/api/projects/default/documents/{document_id}")
        assert legacy_response.status_code == 200
        assert len(legacy_response.json()["sentences"]) == 4


def test_llm_settings_runtime_model_selection_updates_health(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("LLM_BASE_URL", "https://llm.example.test/v1")
    monkeypatch.setenv("LLM_API_KEY", "test-secret")
    monkeypatch.setenv("LLM_MODEL", "gpt5.5")
    storage = AnnotationStorage(
        database_path=tmp_path / "runtime" / "annopilot.sqlite",
        data_root=tmp_path / "projects",
    )
    with TestClient(create_app(storage)) as client:
        settings_response = client.get("/api/settings/llm")
        assert settings_response.status_code == 200
        settings_payload = settings_response.json()
        assert settings_payload["configured"] is True
        assert settings_payload["model"] == "gpt5.5"
        assert settings_payload["selected_model_option_id"] == "gpt5.5-medium"
        assert {option["id"] for option in settings_payload["model_options"]} >= {"gpt5.5-low", "gpt5.6-high"}

        update_response = client.post("/api/settings/llm", json={"model_option_id": "gpt5.6-high"})
        assert update_response.status_code == 200
        updated_payload = update_response.json()
        assert updated_payload["model"] == "gpt5.6-high"
        assert updated_payload["selected_model_option_id"] == "gpt5.6-high"
        assert client.get("/api/health").json()["llm_model"] == "gpt5.6-high"
        with storage.connect() as conn:
            stored = conn.execute("SELECT value FROM runtime_settings WHERE key = 'llm_model_option_id'").fetchone()
        assert stored["value"] == "gpt5.6-high"

        bad_response = client.post("/api/settings/llm", json={"model_option_id": "unknown-model"})
        assert bad_response.status_code == 400


def test_merge_txt_appends_to_existing_document_and_preserves_state(tmp_path: Path) -> None:
    storage = AnnotationStorage(
        database_path=tmp_path / "runtime" / "annopilot.sqlite",
        data_root=tmp_path / "projects",
    )
    with TestClient(create_app(storage)) as client:
        tag = client.post("/api/projects/default/tags", json={"name": "角色"}).json()["tag"]
        imported = client.post(
            "/api/projects/default/import-txt",
            files={"file": ("first.txt", "第一句。第二句。", "text/plain")},
        ).json()
        document_id = imported["document_id"]
        first_sentence = client.get(f"/api/projects/default/documents/{document_id}/sentences?offset=0&limit=1").json()[
            "sentences"
        ][0]
        assert client.post(
            f"/api/projects/default/sentences/{first_sentence['id']}/annotations",
            json={"tag_id": tag["id"], "start_token_index": 0, "end_token_index": 1},
        ).status_code == 200
        assert client.post(
            f"/api/projects/default/sentences/{first_sentence['id']}/complete",
            json={"completed": True, "answer": "accept"},
        ).status_code == 200

        merge_response = client.post(
            f"/api/projects/default/documents/{document_id}/merge-txt",
            files={"file": ("next.txt", "第三句。\nFourth sentence.", "text/plain")},
        )
        assert merge_response.status_code == 200
        merged = merge_response.json()
        assert merged["document_id"] == document_id
        assert merged["sentence_count"] == 4
        assert merged["token_count"] > imported["token_count"]

        summary = client.get(f"/api/projects/default/documents/{document_id}/summary").json()
        assert summary["metrics"]["sentence_count"] == 4
        assert summary["metrics"]["completed_count"] == 1
        assert [item["index"] for item in summary["queue"]] == [0, 1, 2, 3]

        first_after_merge = client.get(f"/api/projects/default/documents/{document_id}/sentences?offset=0&limit=1").json()[
            "sentences"
        ][0]
        assert first_after_merge["completed"] is True
        assert first_after_merge["annotations"][0]["tag_name"] == "角色"
        appended_page = client.get(f"/api/projects/default/documents/{document_id}/sentences?offset=2&limit=2").json()
        assert [sentence["text"] for sentence in appended_page["sentences"]] == ["第三句。", "Fourth sentence."]
        assert appended_page["sentences"][0]["start_char"] == len("第一句。第二句。\n\n")

        events = [json.loads(line) for line in client.get("/api/projects/default/events.jsonl").text.splitlines()]
        import_snapshots = [event for event in events if event["type"] == "document.imported"]
        assert len(import_snapshots) == 2
        assert import_snapshots[-1]["merge_source_filename"] == "next.txt"
        assert import_snapshots[-1]["sentence_count"] == 4
        assert client.get("/api/projects/default/audit").json()["non_replayable_event_count"] == 0
        event_path = tmp_path / "projects" / "default" / "events.jsonl"

    rebuild_result = rebuild_project_from_events(
        project_id="default",
        event_path=event_path,
        database_path=tmp_path / "rebuilt" / "annopilot.sqlite",
        data_root=tmp_path / "rebuilt-projects",
        force=True,
    )
    assert rebuild_result.ok
    assert rebuild_result.documents == 1
    assert rebuild_result.sentences == 4


def test_list_documents_returns_runtime_document_index(tmp_path: Path) -> None:
    storage = AnnotationStorage(
        database_path=tmp_path / "runtime" / "annopilot.sqlite",
        data_root=tmp_path / "projects",
    )
    with TestClient(create_app(storage)) as client:
        labels = seed_pos_span_labels(client)
        first_import = client.post(
            "/api/projects/default/import-txt",
            files={"file": ("first.txt", "第一句。第二句。", "text/plain")},
        ).json()
        second_import = client.post(
            "/api/projects/default/import-txt",
            files={"file": ("second.txt", "清晨，小猫看见金色的叶子。", "text/plain")},
        ).json()

        first_page = client.get(f"/api/projects/default/documents/{first_import['document_id']}/sentences").json()
        first_sentence = first_page["sentences"][0]
        assert client.post(
            f"/api/projects/default/sentences/{first_sentence['id']}/annotations",
            json={"tag_id": labels["名词"]["id"], "start_token_index": 0, "end_token_index": 1},
        ).status_code == 200
        assert client.post(
            f"/api/projects/default/sentences/{first_sentence['id']}/complete",
            json={"completed": True, "answer": "accept"},
        ).status_code == 200

        second_suggestions = client.post(
            f"/api/projects/default/documents/{second_import['document_id']}/suggestions/run",
            json={"limit_per_sentence": 2, "min_confidence": 0.98},
        ).json()["suggestions"]
        assert second_suggestions

        response = client.get("/api/projects/default/documents?limit=10")
        assert response.status_code == 200
        documents = response.json()["documents"]
        assert [document["id"] for document in documents] == [second_import["document_id"], first_import["document_id"]]

        by_id = {document["id"]: document for document in documents}
        first = by_id[first_import["document_id"]]
        assert first["filename"] == "first.txt"
        assert first["sentence_count"] == 2
        assert first["completed_count"] == 1
        assert first["progress"] == 0.5
        assert first["annotation_count"] == 1
        assert first["suggestion_count"] == 0

        second = by_id[second_import["document_id"]]
        assert second["filename"] == "second.txt"
        assert second["sentence_count"] == 1
        assert second["annotation_count"] == 0
        assert second["suggestion_count"] == len(second_suggestions)


def test_session_cursor_persists_reader_position(tmp_path: Path) -> None:
    storage = AnnotationStorage(
        database_path=tmp_path / "runtime" / "annopilot.sqlite",
        data_root=tmp_path / "projects",
    )
    with TestClient(create_app(storage)) as client:
        imported = client.post(
            "/api/projects/default/import-txt",
            files={"file": ("sample.txt", "第一句。第二句。第三句。", "text/plain")},
        ).json()
        document_id = imported["document_id"]

        initial_summary = client.get(f"/api/projects/default/documents/{document_id}/summary")
        assert initial_summary.status_code == 200
        assert initial_summary.json()["session"] == {
            "id": "annopilot-human",
            "actor_id": "annopilot-human",
            "current_sentence_index": None,
            "updated_at": None,
        }

        cursor_response = client.post(
            f"/api/projects/default/documents/{document_id}/session/cursor",
            json={"current_sentence_index": 2},
        )
        assert cursor_response.status_code == 200
        assert cursor_response.json()["session"]["current_sentence_index"] == 2
        assert cursor_response.json()["session"]["updated_at"]

        summary = client.get(f"/api/projects/default/documents/{document_id}/summary").json()
        assert summary["session"]["current_sentence_index"] == 2

        document_list = client.get("/api/projects/default/documents?limit=10").json()["documents"]
        assert document_list[0]["current_sentence_index"] == 2
        assert document_list[0]["session_updated_at"]

        out_of_range = client.post(
            f"/api/projects/default/documents/{document_id}/session/cursor",
            json={"current_sentence_index": 3},
        )
        assert out_of_range.status_code == 400


def test_sentence_ignore_answer_exports_as_ignore(tmp_path: Path) -> None:
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
            files={"file": ("sample.txt", "第一句。第二句。", "text/plain")},
        )
        document_id = response.json()["document_id"]
        page = client.get(f"/api/projects/default/documents/{document_id}/sentences?offset=0&limit=2").json()
        second_sentence = page["sentences"][1]

        ignore_response = client.post(
            f"/api/projects/default/sentences/{second_sentence['id']}/complete",
            json={"completed": True, "answer": "ignore"},
        )
        assert ignore_response.status_code == 200
        assert ignore_response.json() == {"completed": True, "answer": "ignore"}

        summary = client.get(f"/api/projects/default/documents/{document_id}/summary").json()
        assert summary["metrics"]["completed_count"] == 1
        assert summary["metrics"]["answer_counts"] == {"accept": 0, "reject": 0, "ignore": 1, "pending": 1}
        assert summary["queue"][1]["answer"] == "ignore"

        export_lines = [json.loads(line) for line in client.get(f"/api/projects/default/documents/{document_id}/export.jsonl").text.splitlines()]
        assert export_lines[1]["completed"] is True
        assert export_lines[1]["answer"] == "ignore"

        prodigy_lines = [json.loads(line) for line in client.get(f"/api/projects/default/documents/{document_id}/export.prodigy.jsonl").text.splitlines()]
        assert prodigy_lines[1]["answer"] == "ignore"
        assert prodigy_lines[1]["meta"]["answer"] == "ignore"

        events = [json.loads(line) for line in client.get("/api/projects/default/events.jsonl").text.splitlines()]
        complete_event = next(event for event in events if event["type"] == "sentence.completed")
        assert complete_event["actor_type"] == "human"
        assert complete_event["actor_id"] == "annopilot-human"
        assert complete_event["old_completed"] is False
        assert complete_event["old_answer"] == "pending"
        assert complete_event["completed"] is True
        assert complete_event["answer"] == "ignore"

        accept_response = client.post(
            f"/api/projects/default/sentences/{second_sentence['id']}/complete",
            json={"completed": True, "answer": "accept"},
        )
        assert accept_response.status_code == 200
        updated_events = [json.loads(line) for line in client.get("/api/projects/default/events.jsonl").text.splitlines()]
        updated_complete_event = [event for event in updated_events if event["type"] == "sentence.completed"][-1]
        assert updated_complete_event["old_completed"] is True
        assert updated_complete_event["old_answer"] == "ignore"
        assert updated_complete_event["completed"] is True
        assert updated_complete_event["answer"] == "accept"

        updated_summary = client.get(f"/api/projects/default/documents/{document_id}/summary").json()
        assert updated_summary["metrics"]["answer_counts"] == {"accept": 1, "reject": 0, "ignore": 0, "pending": 1}

        reject_response = client.post(
            f"/api/projects/default/sentences/{second_sentence['id']}/complete",
            json={"completed": True, "answer": "reject"},
        )
        assert reject_response.status_code == 200
        assert reject_response.json() == {"completed": True, "answer": "reject"}
        rejected_summary = client.get(f"/api/projects/default/documents/{document_id}/summary").json()
        assert rejected_summary["metrics"]["answer_counts"] == {"accept": 0, "reject": 1, "ignore": 0, "pending": 1}

        reopen_response = client.post(
            f"/api/projects/default/sentences/{second_sentence['id']}/complete",
            json={"completed": False, "answer": "pending"},
        )
        assert reopen_response.status_code == 200
        assert reopen_response.json() == {"completed": False, "answer": "pending"}
        reopened_summary = client.get(f"/api/projects/default/documents/{document_id}/summary").json()
        assert reopened_summary["metrics"]["answer_counts"] == {"accept": 0, "reject": 0, "ignore": 0, "pending": 2}


def test_import_prodigy_jsonl_updates_annotations_and_answers(tmp_path: Path) -> None:
    storage = AnnotationStorage(
        database_path=tmp_path / "runtime" / "annopilot.sqlite",
        data_root=tmp_path / "projects",
    )
    with TestClient(create_app(storage)) as client:
        response = client.post(
            "/api/projects/default/import-txt",
            files={"file": ("story.txt", "清晨，小猫看见金色的叶子。小猫坐在桥边。", "text/plain")},
        )
        assert response.status_code == 200
        document_id = response.json()["document_id"]
        document = client.get(f"/api/projects/default/documents/{document_id}").json()
        first_sentence = document["sentences"][0]
        second_sentence = document["sentences"][1]

        prodigy_records = [
            {
                "text": first_sentence["text"],
                "spans": [{"token_start": 3, "token_end": 4, "label": "角色"}],
                "answer": "accept",
                "_view_id": "spans_manual",
                "_session_id": "review-session-1",
                "_annotator_id": "reviewer-a",
                "_input_hash": 101,
                "_task_hash": 202,
                "meta": {
                    "sentence_id": first_sentence["id"],
                    "sentence_index": first_sentence["index"],
                    "session_id": "review-session-1",
                    "annotator_id": "reviewer-a",
                },
            },
            {
                "text": second_sentence["text"],
                "spans": [],
                "answer": "reject",
                "meta": {"sentence_id": second_sentence["id"], "sentence_index": second_sentence["index"]},
            },
        ]
        import_payload = "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in prodigy_records)

        import_response = client.post(
            f"/api/projects/default/documents/{document_id}/import-annotations-jsonl",
            files={"file": ("review.prodigy.jsonl", import_payload, "application/x-ndjson")},
        )
        assert import_response.status_code == 200
        imported = import_response.json()
        assert imported["record_count"] == 2
        assert imported["matched_count"] == 2
        assert imported["skipped_count"] == 0
        assert imported["created_tag_count"] == 1
        assert imported["created_annotation_count"] == 1
        assert imported["deleted_annotation_count"] == 0
        assert imported["completed_sentence_count"] == 2
        assert imported["source_sha256"] == hashlib.sha256(import_payload.encode("utf-8")).hexdigest()
        assert any(tag["name"] == "角色" for tag in imported["tags"])

        updated_document = client.get(f"/api/projects/default/documents/{document_id}").json()
        assert updated_document["metrics"]["answer_counts"] == {"accept": 1, "reject": 1, "ignore": 0, "pending": 0}
        assert updated_document["metrics"]["annotation_count"] == 1
        first_annotation = updated_document["sentences"][0]["annotations"][0]
        assert first_annotation["tag_name"] == "角色"
        assert first_annotation["text"] == "小猫"
        assert first_annotation["source"] == "prodigy_import"
        assert updated_document["sentences"][1]["answer"] == "reject"

        prodigy_lines = [json.loads(line) for line in client.get(f"/api/projects/default/documents/{document_id}/export.prodigy.jsonl").text.splitlines()]
        assert prodigy_lines[0]["answer"] == "accept"
        assert prodigy_lines[0]["spans"] == [{"start": 3, "end": 5, "token_start": 3, "token_end": 4, "label": "角色"}]
        assert prodigy_lines[0]["meta"]["annotation_sources"][0]["source"] == "prodigy_import"
        assert prodigy_lines[1]["answer"] == "reject"

        events = [json.loads(line) for line in client.get("/api/projects/default/events.jsonl").text.splitlines()]
        imported_event = next(event for event in events if event["type"] == "annotations.imported")
        assert imported_event["record_count"] == 2
        assert imported_event["created_annotation_count"] == 1
        assert imported_event["source_sha256"] == imported["source_sha256"]
        assert len(imported_event["source_record_results"]) == 2
        first_record_result = imported_event["source_record_results"][0]
        assert first_record_result["line_number"] == 1
        assert first_record_result["record_sha256"]
        assert first_record_result["status"] == "matched"
        assert first_record_result["sentence_id"] == first_sentence["id"]
        assert first_record_result["sentence_index"] == first_sentence["index"]
        assert first_record_result["answer"] == "accept"
        assert first_record_result["completed"] is True
        assert first_record_result["raw_span_count"] == 1
        assert first_record_result["created_annotation_count"] == 1
        assert first_record_result["deleted_annotation_count"] == 0
        assert first_record_result["source_metadata"] == {
            "_view_id": "spans_manual",
            "_session_id": "review-session-1",
            "_annotator_id": "reviewer-a",
            "_input_hash": 101,
            "_task_hash": 202,
            "meta.session_id": "review-session-1",
            "meta.annotator_id": "reviewer-a",
        }
        assert imported_event["source_record_results"][1]["answer"] == "reject"
        assert imported_event["source_record_results"][1]["created_annotation_count"] == 0
        created_event = next(event for event in events if event["type"] == "annotation.created")
        assert created_event["source"] == "prodigy_import"
        assert first_record_result["created_annotation_ids"] == [created_event["annotation_id"]]
        assert client.get("/api/projects/default/audit").json()["non_replayable_event_count"] == 0

        import_history_response = client.get(f"/api/projects/default/annotation-imports?document_id={document_id}")
        assert import_history_response.status_code == 200
        import_history = import_history_response.json()["imports"]
        assert len(import_history) == 1
        assert import_history[0]["event_id"] == imported_event["event_id"]
        assert import_history[0]["filename"] == "review.prodigy.jsonl"
        assert import_history[0]["record_count"] == 2
        assert import_history[0]["matched_count"] == 2
        assert import_history[0]["source_sha256"] == imported["source_sha256"]
        assert import_history[0]["source_record_results"][0]["source_metadata"]["_session_id"] == "review-session-1"

        manifest = client.get(f"/api/projects/default/documents/{document_id}/export.manifest.json").json()
        assert manifest["annotation_imports"][0]["event_id"] == imported_event["event_id"]
        assert manifest["annotation_imports"][0]["source_sha256"] == imported["source_sha256"]
        assert manifest["annotation_imports"][0]["source_record_results"][0]["source_metadata"]["_annotator_id"] == "reviewer-a"

        event_path = tmp_path / "projects" / "default" / "events.jsonl"

    rebuilt_database = tmp_path / "rebuilt" / "annopilot.sqlite"
    rebuild_result = rebuild_project_from_events(
        project_id="default",
        event_path=event_path,
        database_path=rebuilt_database,
        data_root=tmp_path / "rebuilt-projects",
        force=True,
    )
    assert rebuild_result.ok
    rebuilt_storage = AnnotationStorage(database_path=rebuilt_database, data_root=tmp_path / "rebuilt-projects")
    rebuilt_document = rebuilt_storage.get_document("default", document_id)
    assert rebuilt_document["metrics"] == updated_document["metrics"]
    assert rebuilt_document["sentences"] == updated_document["sentences"]


def test_list_tags_persists_project_tag_schema_without_document(tmp_path: Path) -> None:
    with TestClient(
        create_app(
            AnnotationStorage(
                database_path=tmp_path / "runtime" / "annopilot.sqlite",
                data_root=tmp_path / "projects",
            )
        )
    ) as client:
        initial_response = client.get("/api/projects/default/tags")
        assert initial_response.status_code == 200
        initial_tags = initial_response.json()["tags"]
        assert initial_tags == []

        create_response = client.post(
            "/api/projects/default/tags",
            json={"name": "地点", "examples": ["桥边", " 小河 ", "小河"]},
        )
        assert create_response.status_code == 200

        updated_response = client.get("/api/projects/default/tags")
        assert updated_response.status_code == 200
        updated_tags = updated_response.json()["tags"]
        assert [tag["name"] for tag in updated_tags] == ["地点"]
        assert updated_tags[0]["shortcut"] == "1"
        assert updated_tags[0]["color"] == "#0b7565"
        assert updated_tags[0]["description"] is None
        assert updated_tags[0]["examples"] == ["桥边", "小河"]
        assert updated_tags[0]["usage_count"] == 0


def test_legacy_seed_labels_are_removed_and_custom_shortcuts_compacted(tmp_path: Path) -> None:
    storage = AnnotationStorage(
        database_path=tmp_path / "runtime" / "annopilot.sqlite",
        data_root=tmp_path / "projects",
    )
    with TestClient(create_app(storage)) as client:
        with storage.connect() as conn:
            conn.executemany(
                """
                INSERT INTO tags (id, project_id, name, description, examples_json, shortcut, color)
                VALUES (?, 'default', ?, NULL, '[]', ?, ?)
                """,
                [
                    ("noun", "名词", "1", "#0b7565"),
                    ("verb", "动词", "2", "#326bd8"),
                    ("adjective", "形容词", "3", "#c45a2e"),
                    ("tag_manual", "verb", "4", "#7a3db8"),
                ],
            )

        response = client.get("/api/projects/default/tags")
        assert response.status_code == 200
        tags = response.json()["tags"]
        assert [tag["name"] for tag in tags] == ["verb"]
        assert tags[0]["shortcut"] == "1"
        assert tags[0]["color"] == "#0b7565"


def test_create_and_delete_tag_removes_related_annotations(tmp_path: Path) -> None:
    with TestClient(
        create_app(
            AnnotationStorage(
                database_path=tmp_path / "runtime" / "annopilot.sqlite",
                data_root=tmp_path / "projects",
            )
        )
    ) as client:
        import_response = client.post(
            "/api/projects/default/import-txt",
            files={"file": ("story.txt", "小猫跑了。", "text/plain")},
        )
        document_id = import_response.json()["document_id"]

        noun_response = client.post("/api/projects/default/tags", json={"name": "名词"})
        assert noun_response.status_code == 200
        noun_tag = noun_response.json()["tag"]
        assert noun_tag["shortcut"] == "1"

        create_tag_response = client.post("/api/projects/default/tags", json={"name": "地点"})
        assert create_tag_response.status_code == 200
        tag = create_tag_response.json()["tag"]
        assert tag["name"] == "地点"
        assert tag["shortcut"] == "2"

        duplicate_rename_response = client.patch(f"/api/projects/default/tags/{tag['id']}", json={"name": "名词"})
        assert duplicate_rename_response.status_code == 400

        rename_response = client.patch(
            f"/api/projects/default/tags/{tag['id']}",
            json={"name": "地名", "description": "地理位置、方位、场所名称。"},
        )
        assert rename_response.status_code == 200
        tag = rename_response.json()["tag"]
        assert tag["name"] == "地名"
        assert tag["description"] == "地理位置、方位、场所名称。"
        assert tag["shortcut"] == "2"

        clear_description_response = client.patch(f"/api/projects/default/tags/{tag['id']}", json={"description": ""})
        assert clear_description_response.status_code == 200
        tag = clear_description_response.json()["tag"]
        assert tag["name"] == "地名"
        assert tag["description"] is None
        assert tag["examples"] == []

        document = client.get(f"/api/projects/default/documents/{document_id}").json()
        sentence = document["sentences"][0]
        annotation_response = client.post(
            f"/api/projects/default/sentences/{sentence['id']}/annotations",
            json={"tag_id": tag["id"], "start_token_index": 0, "end_token_index": 1},
        )
        assert annotation_response.status_code == 200
        assert annotation_response.json()["annotations"][0]["tag_name"] == "地名"

        delete_response = client.delete(f"/api/projects/default/tags/{tag['id']}")
        assert delete_response.status_code == 200
        assert delete_response.json()["annotation_count"] == 1

        updated_document = client.get(f"/api/projects/default/documents/{document_id}").json()
        assert all(tag_item["id"] != tag["id"] for tag_item in updated_document["tags"])
        assert updated_document["sentences"][0]["annotations"] == []
        assert updated_document["metrics"]["annotation_count"] == 0

        final_delete_response = client.delete(f"/api/projects/default/tags/{noun_tag['id']}")
        assert final_delete_response.status_code == 200
        assert client.get("/api/projects/default/tags").json()["tags"] == []

        events = [json.loads(line) for line in client.get("/api/projects/default/events.jsonl").text.splitlines()]
        update_event = next(event for event in events if event["type"] == "tag.updated")
        assert update_event["tag_id"] == tag["id"]
        assert update_event["old_name"] == "地点"
        assert update_event["name"] == "地名"
        assert update_event["old_description"] is None
        assert update_event["description"] == "地理位置、方位、场所名称。"
        clear_event = [event for event in events if event["type"] == "tag.updated"][-1]
        assert clear_event["name"] == "地名"
        assert clear_event["old_description"] == "地理位置、方位、场所名称。"
        assert clear_event["description"] is None
        assert clear_event["examples"] == []
        assert client.get("/api/projects/default/audit").json()["non_replayable_event_count"] == 0


def test_custom_tag_examples_drive_character_rag(tmp_path: Path) -> None:
    with TestClient(
        create_app(
            AnnotationStorage(
                database_path=tmp_path / "runtime" / "annopilot.sqlite",
                data_root=tmp_path / "projects",
            )
        )
    ) as client:
        imported = client.post(
            "/api/projects/default/import-txt",
            files={"file": ("story.txt", "老师走来。", "text/plain")},
        ).json()
        document_id = imported["document_id"]
        role_tag = client.post(
            "/api/projects/default/tags",
            json={"name": "角色", "description": "故事中的人物或动物。", "examples": ["老师"]},
        ).json()["tag"]

        suggestion_response = client.post(
            f"/api/projects/default/documents/{document_id}/suggestions/run",
            json={"limit_per_sentence": 5, "min_confidence": 0.98},
        )
        assert suggestion_response.status_code == 200
        suggestions = suggestion_response.json()["suggestions"]
        assert any(
            suggestion["tag_id"] == role_tag["id"]
            and suggestion["text"] == "老师"
            and suggestion["source"] == "lexical_exact"
            for suggestion in suggestions
        )


def test_character_rag_preserves_ascii_word_gaps_for_phrase_examples(tmp_path: Path) -> None:
    with TestClient(
        create_app(
            AnnotationStorage(
                database_path=tmp_path / "runtime" / "annopilot.sqlite",
                data_root=tmp_path / "projects",
            )
        )
    ) as client:
        imported = client.post(
            "/api/projects/default/import-txt",
            files={"file": ("report.txt", "carbon emissions rose quickly.", "text/plain")},
        ).json()
        document_id = imported["document_id"]
        concept_tag = client.post(
            "/api/projects/default/tags",
            json={"name": "概念", "description": "英文或中文概念短语。", "examples": ["carbon emissions"]},
        ).json()["tag"]

        suggestion_response = client.post(
            f"/api/projects/default/documents/{document_id}/suggestions/run",
            json={"limit_per_sentence": 5, "min_confidence": 0.98},
        )

        assert suggestion_response.status_code == 200
        suggestions = suggestion_response.json()["suggestions"]
        phrase_suggestion = next(suggestion for suggestion in suggestions if suggestion["tag_id"] == concept_tag["id"])
        assert phrase_suggestion["text"] == "carbon emissions"
        assert phrase_suggestion["source"] == "lexical_exact"
        assert phrase_suggestion["start_char"] == 0
        assert phrase_suggestion["end_char"] == len("carbon emissions")


def test_character_rag_matches_phrase_examples_case_insensitively(tmp_path: Path) -> None:
    with TestClient(
        create_app(
            AnnotationStorage(
                database_path=tmp_path / "runtime" / "annopilot.sqlite",
                data_root=tmp_path / "projects",
            )
        )
    ) as client:
        imported = client.post(
            "/api/projects/default/import-txt",
            files={"file": ("report.txt", "Carbon Emissions rose quickly.", "text/plain")},
        ).json()
        document_id = imported["document_id"]
        concept_tag = client.post(
            "/api/projects/default/tags",
            json={"name": "概念", "description": "英文或中文概念短语。", "examples": ["carbon emissions"]},
        ).json()["tag"]

        suggestion_response = client.post(
            f"/api/projects/default/documents/{document_id}/suggestions/run",
            json={"limit_per_sentence": 5, "min_confidence": 0.98},
        )

        assert suggestion_response.status_code == 200
        suggestions = suggestion_response.json()["suggestions"]
        phrase_suggestion = next(suggestion for suggestion in suggestions if suggestion["tag_id"] == concept_tag["id"])
        assert phrase_suggestion["text"] == "Carbon Emissions"
        assert phrase_suggestion["source"] == "lexical_exact"
        assert phrase_suggestion["evidence_text"] == "carbon emissions"
        assert phrase_suggestion["start_char"] == 0
        assert phrase_suggestion["end_char"] == len("Carbon Emissions")


def test_import_tag_schema_merges_non_destructively(tmp_path: Path) -> None:
    with TestClient(
        create_app(
            AnnotationStorage(
                database_path=tmp_path / "runtime" / "annopilot.sqlite",
                data_root=tmp_path / "projects",
            )
        )
    ) as client:
        import_response = client.post(
            "/api/projects/default/tags/schema/import",
            json={
                "schema_version": "annopilot.tag_schema.v1",
                "record_type": "tag_schema",
                "tags": [
                    {
                        "id": "role",
                        "name": "角色",
                        "description": "故事中的人物或动物。",
                        "examples": ["老师", "小狗", "老师"],
                        "shortcut": "4",
                        "color": "#7a3db8",
                    }
                ],
            },
        )
        assert import_response.status_code == 200
        imported = import_response.json()
        assert imported["created"] == 1
        assert imported["updated"] == 0
        assert imported["skipped"] == 0
        assert len(imported["content_sha256"]) == 64
        assert [tag["name"] for tag in imported["tags"]] == ["角色"]
        role_tag = imported["tags"][0]
        assert role_tag["examples"] == ["老师", "小狗"]

        bad_hash_response = client.post(
            "/api/projects/default/tags/schema/import",
            json={
                "schema_version": "annopilot.tag_schema.v1",
                "record_type": "tag_schema",
                "content_sha256": "0" * 64,
                "tags": [{"id": "place", "name": "地点", "examples": ["桥边"]}],
            },
        )
        assert bad_hash_response.status_code == 400

        events = [json.loads(line) for line in client.get("/api/projects/default/events.jsonl").text.splitlines()]
        created_event = next(event for event in events if event["type"] == "tag.created" and event["tag_id"] == "role")
        assert created_event["examples"] == ["老师", "小狗"]
        assert client.get("/api/projects/default/audit").json()["non_replayable_event_count"] == 0


def test_generate_accept_and_reject_suggestions(tmp_path: Path) -> None:
    with TestClient(
        create_app(
            AnnotationStorage(
                database_path=tmp_path / "runtime" / "annopilot.sqlite",
                data_root=tmp_path / "projects",
            )
        )
    ) as client:
        labels = seed_pos_span_labels(client)
        response = client.post(
            "/api/projects/default/import-txt",
            files={"file": ("story.txt", "清晨，小猫看见金色的叶子。小猫坐在桥边。", "text/plain")},
        )
        document_id = response.json()["document_id"]

        suggestion_response = client.post(
            f"/api/projects/default/documents/{document_id}/suggestions/run",
            json={"limit_per_sentence": 2, "min_confidence": 0.98},
        )
        assert suggestion_response.status_code == 200
        suggestion_payload = suggestion_response.json()
        assert suggestion_payload["run_id"].startswith("run_")
        suggestions = suggestion_payload["suggestions"]
        assert len(suggestions) >= 2
        assert len(suggestions) <= 4
        assert all(suggestion["run_id"] == suggestion_payload["run_id"] for suggestion in suggestions)
        assert all(suggestion["evidence_text"] for suggestion in suggestions)
        assert all("context_before" in suggestion and "context_after" in suggestion for suggestion in suggestions)
        assert any(suggestion["context_before"] or suggestion["context_after"] for suggestion in suggestions)
        assert all(suggestion["confidence"] >= 0.98 for suggestion in suggestions)

        with client.app.state.storage.connect() as conn:  # type: ignore[attr-defined]
            run = conn.execute("SELECT recipe, suggestion_count FROM annotation_runs WHERE id = ?", (suggestion_payload["run_id"],)).fetchone()
        assert run["recipe"] == "character_rag"
        assert run["suggestion_count"] == len(suggestions)

        runs_response = client.get(f"/api/projects/default/runs?document_id={document_id}")
        assert runs_response.status_code == 200
        runs = runs_response.json()["runs"]
        assert runs[0]["id"] == suggestion_payload["run_id"]
        assert runs[0]["recipe"] == "character_rag"
        assert runs[0]["config"]["limit_per_sentence"] == 2
        assert runs[0]["config"]["min_confidence"] == 0.98
        assert runs[0]["config"]["tag_schema_version"] == "annopilot.tag_schema.v1"
        assert len(runs[0]["config"]["tag_schema_sha256"]) == 64
        assert len(runs[0]["config"]["examples_sha256"]) == 64
        assert runs[0]["config"]["match_normalization"] == {
            "schema_version": "annopilot.match_normalization.v1",
            "steps": ["strip", "collapse_whitespace", "casefold"],
            "preserves_source_text": True,
        }
        assert runs[0]["config"]["retrieval"] == "offset_gap_span_text|casefold_whitespace_normalized|lexical_exact|lexical_contains|char_ngram"
        assert runs[0]["config"]["examples_match_key_count"] == runs[0]["config"]["example_count"]
        noun_id = labels["名词"]["id"]
        assert "小猫" in runs[0]["config"]["examples_match_keys_by_tag"][noun_id]
        assert len(runs[0]["config"]["examples_match_keys_sha256"]) == 64
        assert len(runs[0]["config"]["negative_examples_sha256"]) == 64
        assert "小猫" in runs[0]["config"]["examples_by_tag"][noun_id]
        assert runs[0]["config"]["negative_examples_by_tag"] == {}
        assert runs[0]["config"]["negative_examples_match_key_count"] == 0
        assert runs[0]["config"]["negative_examples_match_keys_by_tag"] == {}
        assert runs[0]["config"]["examples_sha256"] == hashlib.sha256(
            json.dumps(runs[0]["config"]["examples_by_tag"], ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        assert runs[0]["config"]["examples_match_keys_sha256"] == hashlib.sha256(
            json.dumps(runs[0]["config"]["examples_match_keys_by_tag"], ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        assert runs[0]["config"]["negative_examples_sha256"] == hashlib.sha256(
            json.dumps(runs[0]["config"]["negative_examples_by_tag"], ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        assert runs[0]["config"]["negative_examples_match_keys_sha256"] == hashlib.sha256(
            json.dumps(runs[0]["config"]["negative_examples_match_keys_by_tag"], ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        assert runs[0]["pending_count"] == len(suggestions)
        assert runs[0]["accepted_count"] == 0
        assert runs[0]["rejected_count"] == 0
        assert runs[0]["acceptance_rate"] is None

        accepted = client.post(f"/api/projects/default/suggestions/{suggestions[0]['id']}/accept")
        assert accepted.status_code == 200
        assert accepted.json()["accepted"] is True
        accepted_annotation = accepted.json()["annotations"][-1]
        assert accepted_annotation["source"] == "accepted_suggestion"
        assert accepted_annotation["source_suggestion_id"] == suggestions[0]["id"]

        rejected = client.post(f"/api/projects/default/suggestions/{suggestions[1]['id']}/reject")
        assert rejected.status_code == 200
        assert rejected.json()["rejected"] is True

        updated_runs = client.get(f"/api/projects/default/runs?document_id={document_id}").json()["runs"]
        assert updated_runs[0]["accepted_count"] == 1
        assert updated_runs[0]["rejected_count"] == 1
        assert updated_runs[0]["pending_count"] == len(suggestions) - 2
        assert updated_runs[0]["acceptance_rate"] == 0.5

        provenance_response = client.get(f"/api/projects/default/runs/{suggestion_payload['run_id']}/provenance.json")
        assert provenance_response.status_code == 200
        assert provenance_response.headers["content-disposition"] == f'attachment; filename="{suggestion_payload["run_id"]}.provenance.json"'
        provenance = provenance_response.json()
        assert provenance["schema_version"] == "annopilot.run_provenance.v1"
        assert provenance["record_type"] == "run_provenance"
        assert provenance["project_id"] == "default"
        assert provenance["run"]["id"] == suggestion_payload["run_id"]
        assert provenance["run"]["config"]["tag_schema_sha256"] == runs[0]["config"]["tag_schema_sha256"]
        assert provenance["run"]["config"]["examples_by_tag"] == runs[0]["config"]["examples_by_tag"]
        assert provenance["run"]["config"]["examples_match_keys_by_tag"] == runs[0]["config"]["examples_match_keys_by_tag"]
        assert provenance["status_counts"]["accepted"] == 1
        assert provenance["status_counts"]["rejected"] == 1
        assert provenance["status_counts"]["pending"] == len(suggestions) - 2
        assert len(provenance["suggestions"]) == len(suggestions)
        assert provenance["suggestions"][0]["sentence_index"] == 0
        assert provenance["suggestions"][0]["evidence_text"]
        assert "context_before" in provenance["suggestions"][0]
        assert "context_after" in provenance["suggestions"][0]
        assert provenance["suggestions"][0]["latest_review"] is None
        provenance_by_id = {suggestion["id"]: suggestion for suggestion in provenance["suggestions"]}
        assert provenance_by_id[suggestions[0]["id"]]["decision_event"]["type"] == "suggestion.accepted"
        assert provenance_by_id[suggestions[0]["id"]]["decision_event"]["action"] == "accept"
        assert provenance_by_id[suggestions[0]["id"]]["decision_event"]["event_id"].startswith("evt_")
        assert provenance_by_id[suggestions[0]["id"]]["decision_event"]["actor_type"] == "human"
        assert provenance_by_id[suggestions[0]["id"]]["decision_event"]["actor_id"] == "annopilot-human"
        assert provenance_by_id[suggestions[1]["id"]]["decision_event"]["type"] == "suggestion.rejected"
        assert provenance_by_id[suggestions[1]["id"]]["decision_event"]["action"] == "reject"
        assert provenance_by_id[suggestions[1]["id"]]["decision_event"]["actor_type"] == "human"
        assert provenance_by_id[suggestions[1]["id"]]["decision_event"]["actor_id"] == "annopilot-human"
        if len(suggestions) > 2:
            assert provenance_by_id[suggestions[2]["id"]]["decision_event"] is None
        stable_provenance_payload = {key: value for key, value in provenance.items() if key not in {"generated_at", "content_sha256"}}
        assert provenance["content_sha256"] == hashlib.sha256(
            json.dumps(stable_provenance_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()

        document = client.get(f"/api/projects/default/documents/{document_id}").json()
        assert document["metrics"]["annotation_count"] == 1
        document_annotation = document["sentences"][0]["annotations"][0]
        assert document_annotation["source"] == "accepted_suggestion"
        assert document_annotation["source_suggestion_id"] == suggestions[0]["id"]
        pending_ids = {suggestion["id"] for sentence in document["sentences"] for suggestion in sentence["suggestions"]}
        assert suggestions[0]["id"] not in pending_ids
        assert suggestions[1]["id"] not in pending_ids

        export_response = client.get(f"/api/projects/default/documents/{document_id}/export.jsonl")
        exported_spans = [span for line in export_response.text.splitlines() for span in json.loads(line)["spans"]]
        assert exported_spans[0]["source"] == "accepted_suggestion"
        assert exported_spans[0]["source_suggestion_id"] == suggestions[0]["id"]

        prodigy_export_response = client.get(f"/api/projects/default/documents/{document_id}/export.prodigy.jsonl")
        prodigy_lines = [json.loads(line) for line in prodigy_export_response.text.splitlines()]
        annotated_prodigy_lines = [line for line in prodigy_lines if line["meta"]["annotation_sources"]]
        assert annotated_prodigy_lines[0]["_session_id"] == f"annopilot-default-{document_id}-character-rag"
        assert annotated_prodigy_lines[0]["_annotator_id"] == "annopilot-character-rag"
        prodigy_sources = [source for line in prodigy_lines for source in line["meta"]["annotation_sources"]]
        assert prodigy_sources[0]["annotation_id"] == accepted_annotation["id"]
        assert prodigy_sources[0]["source"] == "accepted_suggestion"
        assert prodigy_sources[0]["source_suggestion_id"] == suggestions[0]["id"]

        manifest = client.get(f"/api/projects/default/documents/{document_id}/export.manifest.json").json()
        assert manifest["source_run_ids"] == [suggestion_payload["run_id"]]
        assert manifest["annotation_source_counts"] == {"accepted_suggestion": 1}
        assert manifest["event_audit"]["event_types"]["suggestions.generated"] == 1
        assert manifest["event_audit"]["actor_type_counts"]["system"] >= 2
        assert manifest["event_audit"]["actor_id_counts"]["annopilot-character-rag"] >= 2
        assert manifest["runs"][0]["config"]["min_confidence"] == 0.98
        assert manifest["run_provenance_artifacts"][suggestion_payload["run_id"]]["schema_version"] == "annopilot.run_provenance.v1"
        assert manifest["run_provenance_artifacts"][suggestion_payload["run_id"]]["filename"].endswith(".provenance.json")
        assert manifest["run_provenance_artifacts"][suggestion_payload["run_id"]]["content_sha256"] == provenance["content_sha256"]
        second_manifest = client.get(f"/api/projects/default/documents/{document_id}/export.manifest.json").json()
        assert second_manifest["run_provenance_artifacts"][suggestion_payload["run_id"]]["content_sha256"] == provenance["content_sha256"]

        events = [json.loads(line) for line in client.get("/api/projects/default/events.jsonl").text.splitlines()]
        accepted_annotation_event = next(event for event in events if event["type"] == "annotation.created")
        assert accepted_annotation_event["actor_type"] == "system"
        assert accepted_annotation_event["actor_id"] == "annopilot-character-rag"
        assert accepted_annotation_event["source"] == "accepted_suggestion"
        assert accepted_annotation_event["source_suggestion_id"] == suggestions[0]["id"]
        generated_event = next(event for event in events if event["type"] == "suggestions.generated")
        assert generated_event["actor_type"] == "system"
        assert generated_event["actor_id"] == "annopilot-character-rag"
        assert generated_event["suggestion_count"] == len(generated_event["suggestions"])
        assert generated_event["config"]["limit_per_sentence"] == 2
        assert generated_event["config"]["min_confidence"] == 0.98
        assert generated_event["config"]["tag_schema_sha256"] == runs[0]["config"]["tag_schema_sha256"]
        assert generated_event["config"]["examples_sha256"] == runs[0]["config"]["examples_sha256"]
        assert generated_event["config"]["negative_examples_sha256"] == runs[0]["config"]["negative_examples_sha256"]
        assert generated_event["suggestions"][0]["id"] == suggestions[0]["id"]
        assert generated_event["suggestions"][0]["status"] == "pending"
        assert generated_event["suggestions"][0]["evidence_text"] == suggestions[0]["evidence_text"]
        assert generated_event["suggestions"][0]["context_before"] == suggestions[0]["context_before"]
        assert generated_event["suggestions"][0]["context_after"] == suggestions[0]["context_after"]
        assert client.get("/api/projects/default/audit").json()["non_replayable_event_count"] == 0


def test_generate_sentence_suggestions_only_scopes_current_sentence(tmp_path: Path) -> None:
    with TestClient(
        create_app(
            AnnotationStorage(
                database_path=tmp_path / "runtime" / "annopilot.sqlite",
                data_root=tmp_path / "projects",
            )
        )
    ) as client:
        seed_pos_span_labels(client)
        response = client.post(
            "/api/projects/default/import-txt",
            files={"file": ("story.txt", "小猫看见叶子。男孩走来。", "text/plain")},
        )
        document_id = response.json()["document_id"]
        page = client.get(f"/api/projects/default/documents/{document_id}/sentences?offset=0&limit=2").json()
        first_sentence_id = page["sentences"][0]["id"]
        second_sentence_id = page["sentences"][1]["id"]

        first_run = client.post(
            f"/api/projects/default/documents/{document_id}/sentences/{first_sentence_id}/suggestions/run",
            json={"limit_per_sentence": 4, "min_confidence": 0.98},
        )
        assert first_run.status_code == 200
        first_payload = first_run.json()
        assert first_payload["suggestions_created"] > 0
        assert {suggestion["sentence_id"] for suggestion in first_payload["suggestions"]} == {first_sentence_id}

        second_run = client.post(
            f"/api/projects/default/documents/{document_id}/sentences/{second_sentence_id}/suggestions/run",
            json={"limit_per_sentence": 4, "min_confidence": 0.98},
        )
        assert second_run.status_code == 200
        assert second_run.json()["suggestions_created"] > 0

        summary = client.get(f"/api/projects/default/documents/{document_id}/summary").json()
        suggestion_counts = {item["id"]: item["suggestion_count"] for item in summary["queue"]}
        assert suggestion_counts[first_sentence_id] > 0
        assert suggestion_counts[second_sentence_id] > 0

        rerun_first = client.post(
            f"/api/projects/default/documents/{document_id}/sentences/{first_sentence_id}/suggestions/run",
            json={"limit_per_sentence": 4, "min_confidence": 0.98},
        )
        assert rerun_first.status_code == 200
        summary_after_rerun = client.get(f"/api/projects/default/documents/{document_id}/summary").json()
        suggestion_counts_after_rerun = {item["id"]: item["suggestion_count"] for item in summary_after_rerun["queue"]}
        assert suggestion_counts_after_rerun[first_sentence_id] > 0
        assert suggestion_counts_after_rerun[second_sentence_id] == suggestion_counts[second_sentence_id]

        runs = client.get(f"/api/projects/default/runs?document_id={document_id}&limit=3").json()["runs"]
        assert runs[0]["input_count"] == 1
        assert runs[0]["config"]["scope"] == "sentence"
        assert runs[0]["config"]["sentence_id"] == first_sentence_id


def test_sentence_batch_accept_and_reject_suggestions(tmp_path: Path) -> None:
    with TestClient(
        create_app(
            AnnotationStorage(
                database_path=tmp_path / "runtime" / "annopilot.sqlite",
                data_root=tmp_path / "projects",
            )
        )
    ) as client:
        seed_pos_span_labels(client)
        response = client.post(
            "/api/projects/default/import-txt",
            files={"file": ("story.txt", "小猫看见金色的叶子。男孩走来。", "text/plain")},
        )
        document_id = response.json()["document_id"]
        page = client.get(f"/api/projects/default/documents/{document_id}/sentences?offset=0&limit=2").json()
        first_sentence_id = page["sentences"][0]["id"]
        second_sentence_id = page["sentences"][1]["id"]

        first_payload = client.post(
            f"/api/projects/default/documents/{document_id}/sentences/{first_sentence_id}/suggestions/run",
            json={"limit_per_sentence": 4, "min_confidence": 0.98},
        ).json()
        second_payload = client.post(
            f"/api/projects/default/documents/{document_id}/sentences/{second_sentence_id}/suggestions/run",
            json={"limit_per_sentence": 4, "min_confidence": 0.98},
        ).json()
        first_ids = {suggestion["id"] for suggestion in first_payload["suggestions"]}
        second_ids = {suggestion["id"] for suggestion in second_payload["suggestions"]}
        assert first_ids
        assert second_ids

        accepted = client.post(f"/api/projects/default/sentences/{first_sentence_id}/suggestions/accept")
        assert accepted.status_code == 200
        accepted_payload = accepted.json()
        assert accepted_payload["accepted"] == len(first_ids)
        assert accepted_payload["skipped"] == 0
        assert set(accepted_payload["accepted_suggestion_ids"]) == first_ids
        assert accepted_payload["affected_sentence_ids"] == [first_sentence_id]
        assert len(accepted_payload["annotations"]) == len(first_ids)
        assert all(annotation["source"] == "accepted_suggestion" for annotation in accepted_payload["annotations"])

        rejected = client.post(f"/api/projects/default/sentences/{second_sentence_id}/suggestions/reject")
        assert rejected.status_code == 200
        rejected_payload = rejected.json()
        assert rejected_payload["rejected"] == len(second_ids)
        assert set(rejected_payload["rejected_suggestion_ids"]) == second_ids
        assert rejected_payload["affected_sentence_ids"] == [second_sentence_id]

        document = client.get(f"/api/projects/default/documents/{document_id}").json()
        assert document["metrics"]["annotation_count"] == len(first_ids)
        assert document["metrics"]["suggestion_count"] == 0
        assert all(not sentence["suggestions"] for sentence in document["sentences"])

        events = [json.loads(line) for line in client.get("/api/projects/default/events.jsonl").text.splitlines()]
        assert sum(1 for event in events if event["type"] == "annotation.created") == len(first_ids)
        assert sum(1 for event in events if event["type"] == "suggestion.accepted") == len(first_ids)
        assert sum(1 for event in events if event["type"] == "suggestion.rejected") == len(second_ids)
        assert client.get("/api/projects/default/audit").json()["non_replayable_event_count"] == 0


def test_review_queue_lists_pending_suggestion_sentences(tmp_path: Path) -> None:
    with TestClient(
        create_app(
            AnnotationStorage(
                database_path=tmp_path / "runtime" / "annopilot.sqlite",
                data_root=tmp_path / "projects",
            )
        )
    ) as client:
        seed_pos_span_labels(client)
        response = client.post(
            "/api/projects/default/import-txt",
            files={"file": ("story.txt", "小猫看见金色的叶子。男孩走来。", "text/plain")},
        )
        document_id = response.json()["document_id"]
        page = client.get(f"/api/projects/default/documents/{document_id}/sentences?offset=0&limit=2").json()
        first_sentence_id = page["sentences"][0]["id"]
        second_sentence_id = page["sentences"][1]["id"]

        for sentence_id in (first_sentence_id, second_sentence_id):
            suggestion_response = client.post(
                f"/api/projects/default/documents/{document_id}/sentences/{sentence_id}/suggestions/run",
                json={"limit_per_sentence": 4, "min_confidence": 0.98},
            )
            assert suggestion_response.status_code == 200
            assert suggestion_response.json()["suggestions_created"] > 0

        queue_response = client.get(f"/api/projects/default/documents/{document_id}/review-queue?limit=10")
        assert queue_response.status_code == 200
        queue = queue_response.json()
        assert queue["total"] == 2
        assert [item["id"] for item in queue["items"]] == [first_sentence_id, second_sentence_id]
        assert queue["items"][0]["index"] == 0
        assert queue["items"][0]["first_suggestion"]["sentence_id"] == first_sentence_id
        assert queue["items"][0]["first_suggestion"]["status"] == "pending"

        assert client.post(f"/api/projects/default/sentences/{first_sentence_id}/suggestions/accept").status_code == 200
        assert client.post(
            f"/api/projects/default/sentences/{second_sentence_id}/complete",
            json={"completed": True, "answer": "ignore"},
        ).status_code == 200

        updated_queue = client.get(f"/api/projects/default/documents/{document_id}/review-queue?limit=10").json()
        assert updated_queue["total"] == 0
        assert updated_queue["items"] == []


def test_review_queue_can_prioritize_uncertain_suggestions(tmp_path: Path) -> None:
    with TestClient(
        create_app(
            AnnotationStorage(
                database_path=tmp_path / "runtime" / "annopilot.sqlite",
                data_root=tmp_path / "projects",
            )
        )
    ) as client:
        imported = client.post(
            "/api/projects/default/import-txt",
            files={"file": ("report.txt", "Carbon emissions rose. Carbon emission rose.", "text/plain")},
        ).json()
        document_id = imported["document_id"]
        concept_tag = client.post(
            "/api/projects/default/tags",
            json={"name": "概念", "description": "英文或中文概念短语。", "examples": ["carbon emissions"]},
        ).json()["tag"]
        page = client.get(f"/api/projects/default/documents/{document_id}/sentences?offset=0&limit=2").json()
        exact_sentence_id = page["sentences"][0]["id"]
        uncertain_sentence_id = page["sentences"][1]["id"]

        suggestion_response = client.post(
            f"/api/projects/default/documents/{document_id}/suggestions/run",
            json={"limit_per_sentence": 3, "min_confidence": 0.7},
        )
        assert suggestion_response.status_code == 200
        suggestions = suggestion_response.json()["suggestions"]
        assert any(suggestion["sentence_id"] == exact_sentence_id and suggestion["confidence"] == 0.98 for suggestion in suggestions)
        assert any(
            suggestion["sentence_id"] == uncertain_sentence_id
            and suggestion["tag_id"] == concept_tag["id"]
            and suggestion["confidence"] < 0.98
            for suggestion in suggestions
        )

        by_position = client.get(f"/api/projects/default/documents/{document_id}/review-queue?order=position").json()
        assert [item["id"] for item in by_position["items"]] == [exact_sentence_id, uncertain_sentence_id]

        by_uncertainty = client.get(f"/api/projects/default/documents/{document_id}/review-queue?order=uncertain").json()
        assert [item["id"] for item in by_uncertainty["items"]] == [uncertain_sentence_id, exact_sentence_id]
        assert by_uncertainty["items"][0]["priority_score"] < by_uncertainty["items"][1]["priority_score"]

        invalid_order = client.get(f"/api/projects/default/documents/{document_id}/review-queue?order=random")
        assert invalid_order.status_code == 400


def test_auto_accept_document_suggestions_by_confidence(tmp_path: Path) -> None:
    with TestClient(
        create_app(
            AnnotationStorage(
                database_path=tmp_path / "runtime" / "annopilot.sqlite",
                data_root=tmp_path / "projects",
            )
        )
    ) as client:
        seed_pos_span_labels(client)
        response = client.post(
            "/api/projects/default/import-txt",
            files={"file": ("story.txt", "清晨，小猫看见金色的叶子。小猫坐在桥边。", "text/plain")},
        )
        document_id = response.json()["document_id"]
        suggestion_payload = client.post(
            f"/api/projects/default/documents/{document_id}/suggestions/run",
            json={"limit_per_sentence": 4, "min_confidence": 0.98},
        ).json()
        suggestions = suggestion_payload["suggestions"]
        assert len(suggestions) >= 2

        auto_accept = client.post(
            f"/api/projects/default/documents/{document_id}/suggestions/auto-accept",
            json={"min_confidence": 0.98},
        )
        assert auto_accept.status_code == 200
        accepted_payload = auto_accept.json()
        assert accepted_payload["accepted"] == len(suggestions)
        assert accepted_payload["skipped"] == 0
        assert set(accepted_payload["accepted_suggestion_ids"]) == {suggestion["id"] for suggestion in suggestions}

        document = client.get(f"/api/projects/default/documents/{document_id}").json()
        annotations = [annotation for sentence in document["sentences"] for annotation in sentence["annotations"]]
        assert len(annotations) == len(suggestions)
        assert all(annotation["source"] == "accepted_suggestion" for annotation in annotations)
        assert document["metrics"]["suggestion_count"] == 0

        events = [json.loads(line) for line in client.get("/api/projects/default/events.jsonl").text.splitlines()]
        assert sum(1 for event in events if event["type"] == "annotation.created") == len(suggestions)
        assert sum(1 for event in events if event["type"] == "suggestion.accepted") == len(suggestions)
        assert client.get("/api/projects/default/audit").json()["non_replayable_event_count"] == 0

        manifest = client.get(f"/api/projects/default/documents/{document_id}/export.manifest.json").json()
        assert manifest["annotation_source_counts"] == {"accepted_suggestion": len(suggestions)}
        assert manifest["source_run_ids"] == [suggestion_payload["run_id"]]


def test_auto_annotate_generates_and_accepts_high_confidence_spans(tmp_path: Path) -> None:
    with TestClient(
        create_app(
            AnnotationStorage(
                database_path=tmp_path / "runtime" / "annopilot.sqlite",
                data_root=tmp_path / "projects",
            )
        )
    ) as client:
        seed_pos_span_labels(client)
        response = client.post(
            "/api/projects/default/import-txt",
            files={"file": ("story.txt", "清晨，小猫看见金色的叶子。小猫坐在桥边。", "text/plain")},
        )
        document_id = response.json()["document_id"]

        auto_annotate = client.post(
            f"/api/projects/default/documents/{document_id}/suggestions/auto-annotate",
            json={"limit_per_sentence": 4, "min_confidence": 0.98},
        )
        assert auto_annotate.status_code == 200
        payload = auto_annotate.json()
        assert payload["run_id"].startswith("run_")
        assert payload["suggestions_created"] >= 2
        assert payload["accepted"] == payload["suggestions_created"]
        assert payload["skipped"] == 0
        assert len(payload["accepted_suggestion_ids"]) == payload["accepted"]
        assert payload["affected_sentence_ids"]

        document = client.get(f"/api/projects/default/documents/{document_id}").json()
        annotations = [annotation for sentence in document["sentences"] for annotation in sentence["annotations"]]
        assert len(annotations) == payload["accepted"]
        assert all(annotation["source"] == "accepted_suggestion" for annotation in annotations)
        assert document["metrics"]["suggestion_count"] == 0

        runs = client.get(f"/api/projects/default/runs?document_id={document_id}").json()["runs"]
        assert runs[0]["id"] == payload["run_id"]
        assert runs[0]["accepted_count"] == payload["accepted"]
        assert runs[0]["pending_count"] == 0

        events = [json.loads(line) for line in client.get("/api/projects/default/events.jsonl").text.splitlines()]
        assert sum(1 for event in events if event["type"] == "suggestions.generated") == 1
        assert sum(1 for event in events if event["type"] == "annotation.created") == payload["accepted"]
        assert sum(1 for event in events if event["type"] == "suggestion.accepted") == payload["accepted"]
        assert client.get("/api/projects/default/audit").json()["non_replayable_event_count"] == 0

        manifest = client.get(f"/api/projects/default/documents/{document_id}/export.manifest.json").json()
        assert manifest["source_run_ids"] == [payload["run_id"]]
        assert manifest["annotation_source_counts"] == {"accepted_suggestion": payload["accepted"]}


def test_auto_reject_document_suggestions_clears_review_queue(tmp_path: Path) -> None:
    with TestClient(
        create_app(
            AnnotationStorage(
                database_path=tmp_path / "runtime" / "annopilot.sqlite",
                data_root=tmp_path / "projects",
            )
        )
    ) as client:
        seed_pos_span_labels(client)
        response = client.post(
            "/api/projects/default/import-txt",
            files={"file": ("story.txt", "清晨，小猫看见金色的叶子。小猫坐在桥边。", "text/plain")},
        )
        document_id = response.json()["document_id"]
        suggestion_payload = client.post(
            f"/api/projects/default/documents/{document_id}/suggestions/run",
            json={"limit_per_sentence": 4, "min_confidence": 0.98},
        ).json()
        suggestions = suggestion_payload["suggestions"]
        assert len(suggestions) >= 2

        auto_reject = client.post(f"/api/projects/default/documents/{document_id}/suggestions/auto-reject")
        assert auto_reject.status_code == 200
        rejected_payload = auto_reject.json()
        assert rejected_payload["rejected"] == len(suggestions)
        assert set(rejected_payload["rejected_suggestion_ids"]) == {suggestion["id"] for suggestion in suggestions}

        document = client.get(f"/api/projects/default/documents/{document_id}").json()
        assert document["metrics"]["suggestion_count"] == 0
        assert document["metrics"]["annotation_count"] == 0
        assert all(not sentence["suggestions"] for sentence in document["sentences"])

        events = [json.loads(line) for line in client.get("/api/projects/default/events.jsonl").text.splitlines()]
        assert sum(1 for event in events if event["type"] == "suggestion.rejected") == len(suggestions)
        assert client.get("/api/projects/default/audit").json()["non_replayable_event_count"] == 0

        manifest = client.get(f"/api/projects/default/documents/{document_id}/export.manifest.json").json()
        assert manifest["annotation_source_counts"] == {}
        assert manifest["source_run_ids"] == [suggestion_payload["run_id"]]


def test_rejected_suggestions_become_project_negative_examples(tmp_path: Path) -> None:
    with TestClient(
        create_app(
            AnnotationStorage(
                database_path=tmp_path / "runtime" / "annopilot.sqlite",
                data_root=tmp_path / "projects",
            )
        )
    ) as client:
        seed_pos_span_labels(client)
        first_import = client.post(
            "/api/projects/default/import-txt",
            files={"file": ("first.txt", "清晨，小猫看见金色的叶子。", "text/plain")},
        )
        first_document_id = first_import.json()["document_id"]
        first_run = client.post(
            f"/api/projects/default/documents/{first_document_id}/suggestions/run",
            json={"limit_per_sentence": 4, "min_confidence": 0.98},
        ).json()
        rejected_candidate = first_run["suggestions"][0]

        reject_response = client.post(f"/api/projects/default/suggestions/{rejected_candidate['id']}/reject")
        assert reject_response.status_code == 200

        second_import = client.post(
            "/api/projects/default/import-txt",
            files={"file": ("second.txt", "清晨，小猫看见金色的叶子。", "text/plain")},
        )
        second_document_id = second_import.json()["document_id"]
        second_run = client.post(
            f"/api/projects/default/documents/{second_document_id}/suggestions/run",
            json={"limit_per_sentence": 4, "min_confidence": 0.98},
        ).json()

        assert second_run["suggestions"]
        assert all(
            suggestion["tag_id"] != rejected_candidate["tag_id"] or suggestion["text"] != rejected_candidate["text"]
            for suggestion in second_run["suggestions"]
        )
        runs = client.get(f"/api/projects/default/runs?document_id={second_document_id}").json()["runs"]
        assert runs[0]["config"]["negative_example_count"] == 1
        assert runs[0]["config"]["negative_examples_by_tag"] == {rejected_candidate["tag_id"]: [rejected_candidate["text"]]}
        assert runs[0]["config"]["negative_examples_sha256"] == hashlib.sha256(
            json.dumps(runs[0]["config"]["negative_examples_by_tag"], ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        generated_event = next(
            event
            for event in [json.loads(line) for line in client.get("/api/projects/default/events.jsonl").text.splitlines()]
            if event["type"] == "suggestions.generated" and event["run_id"] == second_run["run_id"]
        )
        assert generated_event["config"]["negative_example_count"] == 1
        assert generated_event["config"]["negative_examples_by_tag"] == runs[0]["config"]["negative_examples_by_tag"]


def test_rejected_phrase_suggestions_block_case_variants(tmp_path: Path) -> None:
    with TestClient(
        create_app(
            AnnotationStorage(
                database_path=tmp_path / "runtime" / "annopilot.sqlite",
                data_root=tmp_path / "projects",
            )
        )
    ) as client:
        concept_tag = client.post(
            "/api/projects/default/tags",
            json={"name": "概念", "description": "英文或中文概念短语。", "examples": ["carbon emissions"]},
        ).json()["tag"]
        first_import = client.post(
            "/api/projects/default/import-txt",
            files={"file": ("first.txt", "Carbon Emissions rose quickly.", "text/plain")},
        ).json()
        first_run = client.post(
            f"/api/projects/default/documents/{first_import['document_id']}/suggestions/run",
            json={"limit_per_sentence": 5, "min_confidence": 0.98},
        ).json()
        rejected_candidate = next(suggestion for suggestion in first_run["suggestions"] if suggestion["tag_id"] == concept_tag["id"])

        reject_response = client.post(f"/api/projects/default/suggestions/{rejected_candidate['id']}/reject")
        assert reject_response.status_code == 200

        second_import = client.post(
            "/api/projects/default/import-txt",
            files={"file": ("second.txt", "carbon emissions rose again.", "text/plain")},
        ).json()
        second_run = client.post(
            f"/api/projects/default/documents/{second_import['document_id']}/suggestions/run",
            json={"limit_per_sentence": 5, "min_confidence": 0.98},
        ).json()

        assert all(suggestion["tag_id"] != concept_tag["id"] for suggestion in second_run["suggestions"])
        runs = client.get(f"/api/projects/default/runs?document_id={second_import['document_id']}").json()["runs"]
        assert runs[0]["config"]["negative_examples_by_tag"] == {concept_tag["id"]: ["Carbon Emissions"]}
        assert runs[0]["config"]["negative_examples_match_keys_by_tag"] == {concept_tag["id"]: ["carbon emissions"]}
        assert runs[0]["config"]["negative_examples_match_key_count"] == 1


def test_llm_review_suggestion_is_persisted_and_audited(tmp_path: Path) -> None:
    storage = AnnotationStorage(
        database_path=tmp_path / "runtime" / "annopilot.sqlite",
        data_root=tmp_path / "projects",
    )
    with TestClient(create_app(storage, suggestion_reviewer=FakeSuggestionReviewer())) as client:
        seed_pos_span_labels(client)
        response = client.post(
            "/api/projects/default/import-txt",
            files={"file": ("story.txt", "清晨，小猫看见金色的叶子。", "text/plain")},
        )
        document_id = response.json()["document_id"]
        suggestion_response = client.post(f"/api/projects/default/documents/{document_id}/suggestions/run")
        suggestion_id = suggestion_response.json()["suggestions"][0]["id"]
        expected_context_sha256 = storage._payload_sha256(storage.get_suggestion_review_context("default", suggestion_id))

        review_response = client.post(f"/api/projects/default/suggestions/{suggestion_id}/llm-review")
        assert review_response.status_code == 200
        review = review_response.json()
        assert review["model"] == "fake-gpt5.5"
        assert review["recommendation"] == "accept"
        assert review["confidence"] == 0.91
        assert review["context_sha256"] == expected_context_sha256
        assert len(review["context_sha256"]) == 64

        with storage.connect() as conn:
            stored = conn.execute(
                "SELECT recommendation, rationale, context_sha256 FROM annotation_suggestion_reviews WHERE suggestion_id = ?",
                (suggestion_id,),
            ).fetchone()
        assert stored["recommendation"] == "accept"
        assert "匹配" in stored["rationale"]
        assert stored["context_sha256"] == expected_context_sha256

        document = client.get(f"/api/projects/default/documents/{document_id}").json()
        suggestion = next(
            suggestion
            for sentence in document["sentences"]
            for suggestion in sentence["suggestions"]
            if suggestion["id"] == suggestion_id
        )
        assert suggestion["latest_review"]["recommendation"] == "accept"
        assert suggestion["latest_review"]["model"] == "fake-gpt5.5"
        assert suggestion["latest_review"]["context_sha256"] == expected_context_sha256
        assert document["metrics"]["accuracy"] is None
        assert document["metrics"]["accuracy_label"] == "Waiting for reviewed accept/reject data"

        export_response = client.get(f"/api/projects/default/documents/{document_id}/export.jsonl")
        exported = [json.loads(line) for line in export_response.text.splitlines()]
        exported_suggestions = [suggestion for line in exported for suggestion in line["suggestions"]]
        assert any(item["latest_review"] and item["latest_review"]["recommendation"] == "accept" for item in exported_suggestions)
        assert any(item["latest_review"] and item["latest_review"]["context_sha256"] == expected_context_sha256 for item in exported_suggestions)
        assert all("evidence_text" in item for item in exported_suggestions)
        assert all("context_before" in item and "context_after" in item for item in exported_suggestions)

        accept_response = client.post(f"/api/projects/default/suggestions/{suggestion_id}/accept")
        assert accept_response.status_code == 200
        reviewed_document = client.get(f"/api/projects/default/documents/{document_id}").json()
        assert reviewed_document["metrics"]["accuracy"] == 1.0
        assert reviewed_document["metrics"]["accuracy_label"] == "LLM review agreement (1/1)"

        audit = client.get("/api/projects/default/audit").json()
        assert audit["event_types"]["suggestions.generated"] == 1
        assert audit["event_types"]["suggestion.llm_reviewed"] == 1
        assert audit["actor_type_counts"]["system"] == 2
        assert audit["actor_type_counts"]["llm"] == 1
        assert audit["actor_id_counts"]["annopilot-character-rag"] == 2
        assert audit["actor_id_counts"]["fake-gpt5.5"] == 1
        assert audit["non_replayable_event_count"] == 0

        events = [json.loads(line) for line in client.get("/api/projects/default/events.jsonl").text.splitlines()]
        review_event = next(event for event in events if event["type"] == "suggestion.llm_reviewed")
        assert review_event["actor_type"] == "llm"
        assert review_event["actor_id"] == "fake-gpt5.5"
        assert review_event["rationale"] == "候选词面和词性标签匹配。"
        assert review_event["context_sha256"] == expected_context_sha256


def test_rebuild_project_from_events_restores_runtime_state(tmp_path: Path) -> None:
    storage = AnnotationStorage(
        database_path=tmp_path / "runtime" / "annopilot.sqlite",
        data_root=tmp_path / "projects",
    )
    with TestClient(create_app(storage, suggestion_reviewer=FakeSuggestionReviewer())) as client:
        seed_pos_span_labels(client)
        response = client.post(
            "/api/projects/default/import-txt",
            files={"file": ("story.txt", "清晨，小猫看见金色的叶子。小猫坐在桥边。", "text/plain")},
        )
        document_id = response.json()["document_id"]
        document = client.get(f"/api/projects/default/documents/{document_id}").json()
        first_sentence = document["sentences"][0]
        second_sentence = document["sentences"][1]

        tag = client.post("/api/projects/default/tags", json={"name": "地点"}).json()["tag"]
        tag = client.patch(
            f"/api/projects/default/tags/{tag['id']}",
            json={"name": "地名", "description": "地理位置、方位、场所名称。"},
        ).json()["tag"]
        manual_annotation = client.post(
            f"/api/projects/default/sentences/{first_sentence['id']}/annotations",
            json={"tag_id": tag["id"], "start_token_index": 3, "end_token_index": 4},
        ).json()["annotations"][0]
        assert client.delete(f"/api/projects/default/annotations/{manual_annotation['id']}").status_code == 200

        first_run = client.post(f"/api/projects/default/documents/{document_id}/suggestions/run").json()
        suggestions = first_run["suggestions"]
        assert len(suggestions) >= 2
        reviewed_suggestion_id = suggestions[0]["id"]
        assert client.post(f"/api/projects/default/suggestions/{suggestions[0]['id']}/llm-review").status_code == 200
        assert client.post(f"/api/projects/default/suggestions/{suggestions[0]['id']}/accept").status_code == 200
        assert client.post(f"/api/projects/default/suggestions/{suggestions[1]['id']}/reject").status_code == 200
        assert client.post(f"/api/projects/default/sentences/{first_sentence['id']}/complete", json={"completed": True}).status_code == 200

        second_run = client.post(
            f"/api/projects/default/documents/{document_id}/sentences/{second_sentence['id']}/suggestions/run"
        ).json()
        assert second_run["run_id"] != first_run["run_id"]
        assert client.get(f"/api/projects/default/runs?document_id={document_id}").json()["runs"][0]["config"]["scope"] == "sentence"

        original_document = storage.get_document("default", document_id)
        original_counts = _db_counts(storage)
        with storage.connect() as conn:
            original_review_hash = conn.execute(
                "SELECT context_sha256 FROM annotation_suggestion_reviews WHERE suggestion_id = ?",
                (reviewed_suggestion_id,),
            ).fetchone()["context_sha256"]
        assert len(original_review_hash) == 64
        preview = client.post("/api/projects/default/rebuild/preview")
        assert preview.status_code == 200
        preview_payload = preview.json()
        assert preview_payload["ok"] is True
        assert preview_payload["documents"] == 1
        assert preview_payload["runs"] == 2
        assert preview_payload["issues"] == []
        event_path = tmp_path / "projects" / "default" / "events.jsonl"

    rebuilt_database = tmp_path / "rebuilt" / "annopilot.sqlite"
    rebuild_result = rebuild_project_from_events(
        project_id="default",
        event_path=event_path,
        database_path=rebuilt_database,
        data_root=tmp_path / "rebuilt-projects",
        force=True,
    )
    assert rebuild_result.ok
    assert rebuild_result.documents == 1
    assert rebuild_result.runs == 2
    assert rebuild_result.annotations == original_counts["annotations"]
    assert rebuild_result.suggestion_reviews == original_counts["suggestion_reviews"]

    rebuilt_storage = AnnotationStorage(database_path=rebuilt_database, data_root=tmp_path / "rebuilt-projects")
    rebuilt_document = rebuilt_storage.get_document("default", document_id)
    assert rebuilt_document["document"] == original_document["document"]
    assert rebuilt_document["metrics"] == original_document["metrics"]
    assert rebuilt_document["tags"] == original_document["tags"]
    assert rebuilt_document["sentences"] == original_document["sentences"]
    with rebuilt_storage.connect() as conn:
        rebuilt_review_hash = conn.execute(
            "SELECT context_sha256 FROM annotation_suggestion_reviews WHERE suggestion_id = ?",
            (reviewed_suggestion_id,),
        ).fetchone()["context_sha256"]
    assert rebuilt_review_hash == original_review_hash
    assert any(
        suggestion["evidence_text"]
        for sentence in rebuilt_document["sentences"]
        for suggestion in sentence["suggestions"]
    )
    assert _db_counts(rebuilt_storage) == original_counts


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


def test_audit_reports_replay_issue_details(tmp_path: Path) -> None:
    storage = AnnotationStorage(
        database_path=tmp_path / "runtime" / "annopilot.sqlite",
        data_root=tmp_path / "projects",
    )
    storage.initialize()
    event_path = tmp_path / "projects" / "default" / "events.jsonl"
    event_path.parent.mkdir(parents=True, exist_ok=True)
    event_path.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "ts": "2026-08-09T00:00:00+00:00",
                        "project_id": "default",
                        "type": "document.imported",
                        "document_id": "doc_old",
                        "filename": "old.txt",
                        "sentence_count": 1,
                        "token_count": 1,
                    },
                    ensure_ascii=False,
                ),
                json.dumps(
                    {
                        "schema_version": "annopilot.event.v1",
                        "record_type": "event",
                        "event_id": "evt_unknown",
                        "ts": "2026-08-09T00:00:01+00:00",
                        "project_id": "default",
                        "type": "mystery.event",
                    },
                    ensure_ascii=False,
                ),
                "{not json",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    audit = storage.audit_project("default")
    assert audit["rebuild_status"] == "needs_attention"
    assert audit["event_count"] == 2
    assert audit["invalid_event_count"] == 1
    assert audit["legacy_event_count"] == 1
    assert audit["non_replayable_event_count"] == 2
    assert audit["replay_issue_counts"] == {
        "invalid_json": 1,
        "legacy_event": 1,
        "unknown_replay_event": 1,
    }
    assert audit["actor_type_counts"] == {"unknown": 2}
    assert audit["actor_id_counts"] == {"unknown": 2}
    assert audit["replay_issues"] == [
        {"line_number": 1, "event_id": None, "event_type": "document.imported", "message": "legacy_event"},
        {"line_number": 2, "event_id": "evt_unknown", "event_type": "mystery.event", "message": "unknown_replay_event"},
        {"line_number": 3, "event_id": None, "event_type": None, "message": "invalid_json"},
    ]


def _db_counts(storage: AnnotationStorage) -> dict[str, int]:
    with storage.connect() as conn:
        return {
            "documents": conn.execute("SELECT COUNT(*) AS count FROM documents").fetchone()["count"],
            "sentences": conn.execute("SELECT COUNT(*) AS count FROM sentences").fetchone()["count"],
            "tokens": conn.execute("SELECT COUNT(*) AS count FROM tokens").fetchone()["count"],
            "tags": conn.execute("SELECT COUNT(*) AS count FROM tags").fetchone()["count"],
            "annotations": conn.execute("SELECT COUNT(*) AS count FROM annotations").fetchone()["count"],
            "suggestions": conn.execute("SELECT COUNT(*) AS count FROM annotation_suggestions").fetchone()["count"],
            "suggestion_reviews": conn.execute("SELECT COUNT(*) AS count FROM annotation_suggestion_reviews").fetchone()["count"],
            "runs": conn.execute("SELECT COUNT(*) AS count FROM annotation_runs").fetchone()["count"],
        }
