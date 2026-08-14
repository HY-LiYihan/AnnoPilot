import hashlib
import json
from io import BytesIO
from pathlib import Path
from zipfile import ZipFile

import pytest
from fastapi.testclient import TestClient

from backend.app.main import create_app
from backend.app.rebuild import rebuild_project_from_events
from backend.app.storage import AnnotationStorage


class FakeSuggestionReviewer:
    def review(self, context: dict) -> dict:
        assert context["suggestion"]["text"]
        assert context["suggestion"]["span_context"]
        assert f"[{context['suggestion']['text']}]" in context["suggestion"]["span_context"]
        assert context["review_guidance"]["schema_version"] == "annopilot.suggestion_review_context.v1"
        assert context["tag_schema"]["record_type"] == "tag_schema_context"
        assert context["boundary_feedback"]["schema_version"] == "annopilot.boundary_feedback.v1"
        assert "tag_description" in context["suggestion"]
        assert "tag_examples" in context["suggestion"]
        return {
            "model": "fake-gpt5.5",
            "recommendation": "accept",
            "confidence": 0.91,
            "rationale": "候选词面和词性标签匹配。",
            "judge": {
                "format_score": 1.0,
                "concept_fit_score": 0.94,
                "boundary_score": 0.87,
                "relation_score": 1.0,
                "missed_span_risk": 0.05,
                "extra_span_risk": 0.08,
                "overall_score": 0.92,
                "needs_review": False,
                "error_types": [],
                "risk_flags": ["borderline_concept"],
            },
        }


class RejectingSuggestionReviewer:
    def review(self, context: dict) -> dict:
        assert context["suggestion"]["text"]
        return {
            "model": "fake-gpt5.5",
            "recommendation": "reject",
            "confidence": 0.89,
            "rationale": "候选边界不应自动进入该标签。",
        }


class UncertainSuggestionReviewer:
    def review(self, context: dict) -> dict:
        assert context["suggestion"]["text"]
        return {
            "model": "fake-gpt5.5",
            "recommendation": "uncertain",
            "confidence": 0.62,
            "rationale": "候选可能是边界样例，需要人工校准。",
        }


class CyclingSuggestionReviewer:
    def __init__(self) -> None:
        self.index = 0

    def review(self, context: dict) -> dict:
        assert context["suggestion"]["text"]
        recommendation = ["accept", "reject", "uncertain"][self.index % 3]
        self.index += 1
        return {
            "model": "fake-gpt5.5",
            "recommendation": recommendation,
            "confidence": 0.88,
            "rationale": f"循环评审结果：{recommendation}",
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


def _stable_manifest_payload(manifest: dict) -> dict:
    payload = json.loads(json.dumps({key: value for key, value in manifest.items() if key not in {"generated_at", "content_sha256"}}))
    for group_name in ("artifacts", "run_provenance_artifacts"):
        group = payload.get(group_name)
        if not isinstance(group, dict):
            continue
        for artifact in group.values():
            if isinstance(artifact, dict) and artifact.get("content_sha256"):
                artifact.pop("sha256", None)
    return payload


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
        assert prodigy_lines[0]["meta"]["tag_schema"]["schema_version"] == "annopilot.tag_schema.v1"
        assert prodigy_lines[0]["meta"]["tag_schema"]["tag_count"] == 1
        assert prodigy_lines[0]["meta"]["tag_schema"]["labels"] == [
            {
                "id": tag["id"],
                "name": "动词",
                "description": None,
                "examples": ["reduced"],
                "shortcut": "1",
                "color": "#0b7565",
            }
        ]
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
        assert prodigy_lines[0]["meta"]["tag_schema"]["content_sha256"] == tag_schema["content_sha256"]

        prodigy_labels_response = client.get("/api/projects/default/tags/prodigy-labels.json")
        assert prodigy_labels_response.status_code == 200
        assert prodigy_labels_response.headers["content-disposition"] == 'attachment; filename="default-prodigy-labels.json"'
        prodigy_labels = prodigy_labels_response.json()
        assert prodigy_labels["schema_version"] == "annopilot.prodigy_labels.v1"
        assert prodigy_labels["record_type"] == "prodigy_labels"
        assert len(prodigy_labels["content_sha256"]) == 64
        assert prodigy_labels["tag_schema_sha256"] == tag_schema["content_sha256"]
        assert prodigy_labels["labels"] == ["动词"]
        assert prodigy_labels["labels_csv"] == "动词"
        assert prodigy_labels["label_definitions"] == prodigy_lines[0]["meta"]["tag_schema"]["labels"]
        assert '--label "动词"' in prodigy_labels["command_templates"]["ner_manual"]

        manifest_response = client.get(f"/api/projects/default/documents/{document_id}/export.manifest.json")
        assert manifest_response.status_code == 200
        assert manifest_response.headers["content-disposition"] == f'attachment; filename="{document_id}.manifest.json"'
        manifest = manifest_response.json()
        assert manifest["schema_version"] == "annopilot.export_manifest.v1"
        assert manifest["record_type"] == "export_manifest"
        assert len(manifest["content_sha256"]) == 64
        stable_manifest_payload = _stable_manifest_payload(manifest)
        assert manifest["content_sha256"] == hashlib.sha256(
            json.dumps(stable_manifest_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        assert manifest["document"]["id"] == document_id
        assert manifest["metrics"]["answer_counts"] == {"accept": 1, "reject": 0, "ignore": 0, "pending": 1}
        assert manifest["prodigy_readiness"]["ready"] is False
        assert manifest["prodigy_readiness"]["status"] == "needs_attention"
        assert manifest["prodigy_readiness"]["blockers"] == ["incomplete_sentences"]
        assert manifest["prodigy_readiness"]["sentence_count"] == 2
        assert manifest["prodigy_readiness"]["completed_sentence_count"] == 1
        assert manifest["prodigy_readiness"]["pending_suggestion_count"] == 0
        assert manifest["prodigy_readiness"]["formats"] == {
            "ner_manual": "prodigy.ner_manual.compat.v1",
            "spans_manual": "prodigy.spans_manual.compat.v1",
        }
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
        assert manifest["artifacts"]["prodigy_labels_json"]["schema_version"] == "annopilot.prodigy_labels.v1"
        assert manifest["artifacts"]["prodigy_labels_json"]["content_sha256"] == prodigy_labels["content_sha256"]
        assert manifest["artifacts"]["events_jsonl"]["sha256"] == hashlib.sha256(event_export_response.text.encode("utf-8")).hexdigest()
        assert manifest["artifacts"]["tag_schema_json"]["schema_version"] == "annopilot.tag_schema.v1"
        assert manifest["artifacts"]["tag_schema_json"]["line_count"] == 1
        assert manifest["artifacts"]["tag_schema_json"]["content_sha256"] == tag_schema["content_sha256"]

        bundle_response = client.get(f"/api/projects/default/documents/{document_id}/export.prodigy.bundle.zip")
        assert bundle_response.status_code == 200
        assert bundle_response.headers["content-disposition"] == f'attachment; filename="{document_id}.prodigy.bundle.zip"'
        assert bundle_response.headers["content-type"] == "application/zip"
        with ZipFile(BytesIO(bundle_response.content)) as archive:
            bundle_names = set(archive.namelist())
            artifact_names = {artifact["filename"] for artifact in manifest["artifacts"].values()}
            assert {
                "README.txt",
                f"{document_id}.manifest.json",
                *artifact_names,
            } <= bundle_names
            bundled_artifacts = {
                name: archive.read(name).decode("utf-8")
                for name in artifact_names
            }
            bundled_manifest = json.loads(archive.read(f"{document_id}.manifest.json").decode("utf-8"))
            bundled_readme = archive.read("README.txt").decode("utf-8")

        assert "AnnoPilot Prodigy Export Bundle" in bundled_readme
        assert bundled_manifest["content_sha256"] == manifest["content_sha256"]
        for artifact in bundled_manifest["artifacts"].values():
            bundled_content = bundled_artifacts[artifact["filename"]]
            assert artifact["sha256"] == hashlib.sha256(bundled_content.encode("utf-8")).hexdigest()
        second_manifest = client.get(f"/api/projects/default/documents/{document_id}/export.manifest.json").json()
        assert second_manifest["content_sha256"] == manifest["content_sha256"]

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
        assert summary["metrics"]["suggestion_status_counts"] == {"pending": 0, "accepted": 0, "rejected": 0}
        assert summary["metrics"]["suggestion_source_counts"] == {}
        assert summary["metrics"]["suggestion_confidence_counts"] == {}
        assert summary["metrics"]["suggestion_review_counts"] == {"accept": 0, "reject": 0, "uncertain": 0}
        assert summary["metrics"]["reviewed_suggestion_count"] == 0
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
        assert len(manifest["content_sha256"]) == 64
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


def test_appraisal_engagement_samples_generate_and_export_prodigy(tmp_path: Path) -> None:
    sample_root = Path(__file__).resolve().parents[2] / "samples"
    schema = json.loads((sample_root / "appraisal-engagement-tag-schema.json").read_text(encoding="utf-8"))
    source_text = (sample_root / "appraisal-engagement-cn-en.txt").read_text(encoding="utf-8")

    with TestClient(
        create_app(
            AnnotationStorage(
                database_path=tmp_path / "runtime" / "annopilot.sqlite",
                data_root=tmp_path / "projects",
            )
        )
    ) as client:
        schema_response = client.post("/api/projects/default/tags/schema/import", json=schema)
        assert schema_response.status_code == 200
        imported_schema = schema_response.json()
        assert imported_schema["created"] == 9
        assert [tag["id"] for tag in imported_schema["tags"]] == [tag["id"] for tag in schema["tags"]]

        import_response = client.post(
            "/api/projects/default/import-txt",
            files={"file": ("appraisal-engagement-cn-en.txt", source_text, "text/plain")},
        )
        assert import_response.status_code == 200
        document_id = import_response.json()["document_id"]

        suggestion_response = client.post(
            f"/api/projects/default/documents/{document_id}/suggestions/run",
            json={"limit_per_sentence": 10, "min_confidence": 0.98},
        )
        assert suggestion_response.status_code == 200
        suggestion_payload = suggestion_response.json()
        suggestions = suggestion_payload["suggestions"]
        assert suggestion_payload["source_counts"] == {"lexical_exact": len(suggestions)}

        suggestions_by_text = {(suggestion["text"], suggestion["tag_id"]) for suggestion in suggestions}
        expected_hits = {
            ("said", "engagement_attribute_acknowledge"),
            ("may", "engagement_entertain"),
            ("but", "engagement_disclaim_counter"),
            ("does not", "engagement_disclaim_deny"),
            ("According to", "engagement_attribute_acknowledge"),
            ("shows", "engagement_proclaim_endorse"),
            ("clearly", "engagement_proclaim_pronounce"),
            ("allegedly", "engagement_attribute_distance"),
            ("yet", "engagement_disclaim_counter"),
            ("Of course", "engagement_proclaim_concur"),
            ("表示", "engagement_attribute_acknowledge"),
            ("可能", "engagement_entertain"),
            ("但", "engagement_disclaim_counter"),
            ("不能", "engagement_disclaim_deny"),
            ("显然", "engagement_proclaim_pronounce"),
            ("据称", "engagement_attribute_distance"),
            ("然而", "engagement_disclaim_counter"),
            ("诚然", "engagement_proclaim_concur"),
        }
        assert expected_hits.issubset(suggestions_by_text)

        auto_accept_response = client.post(
            f"/api/projects/default/documents/{document_id}/suggestions/auto-accept",
            json={"min_confidence": 0.98},
        )
        assert auto_accept_response.status_code == 200
        accepted = auto_accept_response.json()
        assert accepted["accepted"] >= len(expected_hits)

        prodigy_response = client.get(f"/api/projects/default/documents/{document_id}/export.prodigy.jsonl")
        assert prodigy_response.status_code == 200
        prodigy_lines = [json.loads(line) for line in prodigy_response.text.splitlines()]
        exported_spans = [span for line in prodigy_lines for span in line["spans"]]
        exported_labels = {span["label"] for span in exported_spans}
        assert "Entertain 可能化" in exported_labels
        assert "Attribute Acknowledge 归因承认" in exported_labels
        assert "Disclaim Counter 转折反驳" in exported_labels
        assert "Disclaim Deny 否认" in exported_labels
        assert any(line["_view_id"] == "ner_manual" for line in prodigy_lines)
        assert any(source["source"] == "accepted_suggestion" for line in prodigy_lines for source in line["meta"]["annotation_sources"])
        assert all(line["answer"] == "accept" for line in prodigy_lines if line["spans"])
        assert all(line["meta"]["answer"] == "pending" for line in prodigy_lines if line["spans"])


def test_load_builtin_appraisal_engagement_sample_preset(tmp_path: Path) -> None:
    with TestClient(
        create_app(
            AnnotationStorage(
                database_path=tmp_path / "runtime" / "annopilot.sqlite",
                data_root=tmp_path / "projects",
            )
        )
    ) as client:
        presets_response = client.get("/api/projects/default/sample-presets")
        assert presets_response.status_code == 200
        presets = presets_response.json()["presets"]
        expected_preset_ids = [
            "appraisal-engagement-cn-en",
            "appraisal-engagement-news-policy-cn-en",
            "appraisal-engagement-academic-method-cn-en",
            "appraisal-engagement-platform-review-cn-en",
            "appraisal-engagement-customer-support-cn-en",
            "appraisal-engagement-legal-compliance-cn-en",
            "appraisal-engagement-social-opinion-cn-en",
            "appraisal-engagement-finance-investor-cn-en",
            "appraisal-engagement-health-science-cn-en",
            "appraisal-engagement-ai-education-cn-en",
            "appraisal-engagement-climate-energy-cn-en",
            "appraisal-engagement-workplace-labor-cn-en",
            "appraisal-engagement-product-safety-cn-en",
            "appraisal-engagement-crisis-response-cn-en",
            "appraisal-engagement-calibration-cn-en",
        ]
        assert [preset["id"] for preset in presets] == expected_preset_ids
        assert all(preset["tag_count"] == 9 for preset in presets)
        preset_by_id = {preset["id"]: preset for preset in presets}
        assert preset_by_id["appraisal-engagement-cn-en"]["auto_accept_on_load"] is True
        assert preset_by_id["appraisal-engagement-cn-en"]["complete_sentences_on_load"] is True
        assert preset_by_id["appraisal-engagement-calibration-cn-en"]["auto_accept_on_load"] is False
        assert preset_by_id["appraisal-engagement-calibration-cn-en"]["complete_sentences_on_load"] is False

        loaded_by_id = {}
        for preset_id in expected_preset_ids:
            load_response = client.post(
                f"/api/projects/default/sample-presets/{preset_id}/load",
                json={"auto_accept_suggestions": False, "complete_sentences": False},
            )
            assert load_response.status_code == 200
            loaded_preset = load_response.json()
            loaded_by_id[preset_id] = loaded_preset
            assert loaded_preset["filename"].endswith(".txt")
            assert loaded_preset["sentence_count"] >= 8
            assert loaded_preset["token_count"] > 0
            assert len(loaded_preset["tags"]) == 9
            assert loaded_preset["suggestion_run_id"].startswith("run_")
            assert loaded_preset["suggestions_created"] > 0
            assert sum(loaded_preset["source_counts"].values()) == loaded_preset["suggestions_created"]
            if preset_id == "appraisal-engagement-calibration-cn-en":
                assert loaded_preset["preset"]["calibration_candidate_count"] == loaded_preset["suggestions_created"]
                assert loaded_preset["source_counts"] == {"calibration_seed": loaded_preset["suggestions_created"]}
            else:
                assert loaded_preset["source_counts"] == {"lexical_exact": loaded_preset["suggestions_created"]}

            auto_accept_response = client.post(
                f"/api/projects/default/documents/{loaded_preset['document_id']}/suggestions/auto-accept",
                json={"min_confidence": 0.98},
            )
            assert auto_accept_response.status_code == 200
            assert auto_accept_response.json()["accepted"] > 0

            prodigy_response = client.get(f"/api/projects/default/documents/{loaded_preset['document_id']}/export.prodigy.jsonl")
            assert prodigy_response.status_code == 200
            prodigy_lines = [json.loads(line) for line in prodigy_response.text.splitlines()]
            exported_labels = {span["label"] for line in prodigy_lines for span in line["spans"]}
            assert "Entertain 可能化" in exported_labels
            assert "Disclaim Counter 转折反驳" in exported_labels
            assert any(line["_view_id"] == "ner_manual" for line in prodigy_lines)
            assert any(source["source"] == "accepted_suggestion" for line in prodigy_lines for source in line["meta"]["annotation_sources"])
            assert all(line["answer"] == "accept" for line in prodigy_lines if line["spans"])
            assert all(line["meta"]["answer"] == "pending" for line in prodigy_lines if line["spans"])

            prodigy_spans_response = client.get(f"/api/projects/default/documents/{loaded_preset['document_id']}/export.prodigy.spans.jsonl")
            assert prodigy_spans_response.status_code == 200
            prodigy_spans_lines = [json.loads(line) for line in prodigy_spans_response.text.splitlines()]
            assert all(line["_view_id"] == "spans_manual" for line in prodigy_spans_lines)
            assert sum(len(line["spans"]) for line in prodigy_spans_lines) == sum(len(line["spans"]) for line in prodigy_lines)

        loaded = loaded_by_id["appraisal-engagement-cn-en"]
        document_id = loaded["document_id"]
        assert loaded["filename"] == "appraisal-engagement-cn-en.txt"
        assert loaded["suggestions_created"] >= 20
        assert loaded["source_counts"] == {"lexical_exact": loaded["suggestions_created"]}

        summary_response = client.get(f"/api/projects/default/documents/{document_id}/summary")
        assert summary_response.status_code == 200
        summary = summary_response.json()
        assert summary["metrics"]["annotation_count"] >= 20
        assert summary["metrics"]["suggestion_status_counts"]["accepted"] >= 20
        annotation_label_counts = {item["name"]: item["count"] for item in summary["metrics"]["annotation_label_counts"]}
        assert annotation_label_counts["Entertain 可能化"] > 0
        assert annotation_label_counts["Disclaim Counter 转折反驳"] > 0
        assert all("tag_id" in item and "color" in item for item in summary["metrics"]["annotation_label_counts"])
        assert summary["metrics"]["suggestion_label_counts"] == []
        assert [tag["id"] for tag in summary["tags"]] == [tag["id"] for tag in loaded["tags"]]

        assert loaded_by_id["appraisal-engagement-platform-review-cn-en"]["suggestions_created"] >= 20
        assert loaded_by_id["appraisal-engagement-customer-support-cn-en"]["suggestions_created"] >= 20
        assert loaded_by_id["appraisal-engagement-legal-compliance-cn-en"]["suggestions_created"] >= 20
        assert loaded_by_id["appraisal-engagement-social-opinion-cn-en"]["suggestions_created"] >= 20
        assert loaded_by_id["appraisal-engagement-finance-investor-cn-en"]["suggestions_created"] >= 20
        assert loaded_by_id["appraisal-engagement-health-science-cn-en"]["suggestions_created"] >= 20
        assert loaded_by_id["appraisal-engagement-ai-education-cn-en"]["suggestions_created"] >= 20
        assert loaded_by_id["appraisal-engagement-climate-energy-cn-en"]["suggestions_created"] >= 20
        assert loaded_by_id["appraisal-engagement-workplace-labor-cn-en"]["suggestions_created"] >= 20
        assert loaded_by_id["appraisal-engagement-product-safety-cn-en"]["suggestions_created"] >= 20
        assert loaded_by_id["appraisal-engagement-crisis-response-cn-en"]["suggestions_created"] >= 20
        assert loaded_by_id["appraisal-engagement-calibration-cn-en"]["suggestions_created"] >= 20


def test_load_appraisal_preset_can_auto_accept_and_complete_for_prodigy(tmp_path: Path) -> None:
    with TestClient(
        create_app(
            AnnotationStorage(
                database_path=tmp_path / "runtime" / "annopilot.sqlite",
                data_root=tmp_path / "projects",
            )
        )
    ) as client:
        load_response = client.post("/api/projects/default/sample-presets/appraisal-engagement-product-safety-cn-en/load")
        assert load_response.status_code == 200
        loaded = load_response.json()
        document_id = loaded["document_id"]
        assert loaded["preset"]["id"] == "appraisal-engagement-product-safety-cn-en"
        assert loaded["suggestions_created"] >= 20
        assert loaded["auto_accepted"] == loaded["suggestions_created"]
        assert loaded["auto_accept_skipped"] == 0
        assert loaded["auto_completed"] > 0
        assert len(loaded["auto_accepted_suggestion_ids"]) == loaded["auto_accepted"]
        assert len(loaded["auto_completed_sentence_ids"]) == loaded["auto_completed"]

        summary = client.get(f"/api/projects/default/documents/{document_id}/summary").json()
        assert summary["metrics"]["annotation_count"] == loaded["auto_accepted"]
        assert summary["metrics"]["completed_count"] == loaded["auto_completed"]
        assert summary["metrics"]["suggestion_count"] == 0

        prodigy_lines = [
            json.loads(line)
            for line in client.get(f"/api/projects/default/documents/{document_id}/export.prodigy.jsonl").text.splitlines()
        ]
        annotated_lines = [line for line in prodigy_lines if line["spans"]]
        assert annotated_lines
        assert all(line["answer"] == "accept" for line in annotated_lines)
        assert all(line["meta"]["answer"] == "accept" for line in annotated_lines)
        assert all(line["meta"]["completed"] is True for line in annotated_lines)

        events = [json.loads(line) for line in client.get("/api/projects/default/events.jsonl").text.splitlines()]
        assert sum(1 for event in events if event["type"] == "sentence.completed") == loaded["auto_completed"]
        assert all(
            event["actor_type"] == "system"
            for event in events
            if event["type"] == "sentence.completed" and event.get("source") == "auto_accept_suggestions"
        )
        assert client.get("/api/projects/default/audit").json()["non_replayable_event_count"] == 0


def test_calibration_preset_recommended_load_preserves_goldsmith_review_queue(tmp_path: Path) -> None:
    with TestClient(
        create_app(
            AnnotationStorage(
                database_path=tmp_path / "runtime" / "annopilot.sqlite",
                data_root=tmp_path / "projects",
            )
        )
    ) as client:
        presets = client.get("/api/projects/default/sample-presets").json()["presets"]
        calibration_preset = next(preset for preset in presets if preset["id"] == "appraisal-engagement-calibration-cn-en")
        load_response = client.post(
            "/api/projects/default/sample-presets/appraisal-engagement-calibration-cn-en/load",
            json={
                "generate_suggestions": True,
                "auto_accept_suggestions": calibration_preset["auto_accept_on_load"],
                "complete_sentences": calibration_preset["complete_sentences_on_load"],
            },
        )
        assert load_response.status_code == 200
        loaded = load_response.json()
        document_id = loaded["document_id"]
        assert loaded["suggestions_created"] == loaded["preset"]["calibration_candidate_count"]
        assert loaded["auto_accepted"] == 0
        assert loaded["auto_completed"] == 0

        summary = client.get(f"/api/projects/default/documents/{document_id}/summary").json()
        assert summary["metrics"]["suggestion_count"] == loaded["suggestions_created"]
        assert summary["metrics"]["annotation_count"] == 0
        assert summary["metrics"]["completed_count"] == 0

        queue = client.get(f"/api/projects/default/documents/{document_id}/review-queue?order=goldsmith&limit=20").json()
        assert queue["total"] == 8
        assert all(item["review_route"] == "risk" for item in queue["items"])
        assert any("candidate_conflict" in item["risk_reason_codes"] for item in queue["items"])

        review_tasks = [
            json.loads(line)
            for line in client.get(f"/api/projects/default/documents/{document_id}/export.goldsmith.review-tasks.jsonl").text.splitlines()
        ]
        assert len(review_tasks) == queue["total"]
        assert review_tasks[0]["record_type"] == "human_review_task"
        assert review_tasks[0]["manual_option_id"] == "__manual__"

        manifest = client.get(f"/api/projects/default/documents/{document_id}/export.manifest.json").json()
        assert manifest["artifacts"]["goldsmith_review_queue_jsonl"]["line_count"] == queue["total"]
        assert manifest["artifacts"]["goldsmith_review_tasks_jsonl"]["line_count"] == len(review_tasks)
        assert manifest["artifacts"]["goldsmith_candidate_runs_jsonl"]["line_count"] == loaded["suggestions_created"]
        assert manifest["artifacts"]["goldsmith_consistency_scores_jsonl"]["line_count"] == queue["total"]
        assert manifest["artifacts"]["goldsmith_risk_reasons_jsonl"]["line_count"] >= 1

        bundle_response = client.get(f"/api/projects/default/documents/{document_id}/export.prodigy.bundle.zip")
        assert bundle_response.status_code == 200
        with ZipFile(BytesIO(bundle_response.content)) as archive:
            readme = archive.read("README.txt").decode("utf-8")
            bundled_manifest = json.loads(archive.read(f"{document_id}.manifest.json").decode("utf-8"))
            bundle_names = set(archive.namelist())

        assert "Goldsmith/Rosetta review artifacts" in readme
        assert "human review tasks with candidate options and manual fallback" in readme
        assert manifest["artifacts"]["goldsmith_review_tasks_jsonl"]["filename"] in readme
        assert manifest["artifacts"]["goldsmith_risk_reasons_jsonl"]["filename"] in readme
        assert manifest["artifacts"]["goldsmith_candidate_runs_jsonl"]["filename"] in readme
        assert bundled_manifest["content_sha256"] == manifest["content_sha256"]
        assert {artifact["filename"] for artifact in manifest["artifacts"].values()} <= bundle_names


def test_load_appraisal_engagement_calibration_preset_seeds_conflict_candidates(tmp_path: Path) -> None:
    storage = AnnotationStorage(
        database_path=tmp_path / "runtime" / "annopilot.sqlite",
        data_root=tmp_path / "projects",
    )
    with TestClient(create_app(storage)) as client:
        load_response = client.post("/api/projects/default/sample-presets/appraisal-engagement-calibration-cn-en/load")
        assert load_response.status_code == 200
        loaded = load_response.json()
        document_id = loaded["document_id"]

        assert loaded["filename"] == "appraisal-engagement-calibration-cn-en.txt"
        assert loaded["sentence_count"] == 8
        assert loaded["suggestions_created"] == loaded["preset"]["calibration_candidate_count"]
        assert loaded["source_counts"] == {"calibration_seed": loaded["suggestions_created"]}
        assert loaded["confidence_counts"]["high"] > 0
        assert loaded["confidence_counts"]["medium"] > 0

        queue = client.get(f"/api/projects/default/documents/{document_id}/review-queue?order=goldsmith&limit=8").json()
        assert queue["items"]
        assert any(item["candidate_disagreement_score"] > 0 for item in queue["items"])
        assert queue["items"][0]["risk_score"] >= queue["items"][-1]["risk_score"]

        consistency_response = client.get(f"/api/projects/default/documents/{document_id}/export.goldsmith.consistency-scores.jsonl")
        assert consistency_response.status_code == 200
        consistency_lines = [json.loads(line) for line in consistency_response.text.splitlines()]
        assert any(line["overlap_conflict_rate"] > 0 for line in consistency_lines)
        assert any(line["pairwise_span_f1"] < 1 for line in consistency_lines)
        assert any(
            candidate["overlap_conflict_count"] > 0
            for line in consistency_lines
            for candidate in line["candidate_scores"]
        )

        candidate_runs_response = client.get(f"/api/projects/default/documents/{document_id}/export.goldsmith.candidate-runs.jsonl")
        assert candidate_runs_response.status_code == 200
        candidate_runs = [json.loads(line) for line in candidate_runs_response.text.splitlines()]
        assert len(candidate_runs) == loaded["suggestions_created"]
        assert any(run["meta"]["consistency"]["overlap_conflict_rate"] > 0 for run in candidate_runs)
        assert all(run["schema_version"] == "rosetta.prodigy_candidate.v1" for run in candidate_runs)
        assert all(run["meta"]["candidate_order"] == "sentence_index,candidate_id" for run in candidate_runs)
        candidate_ids_by_sample: dict[str, list[str]] = {}
        for run in candidate_runs:
            candidate_ids_by_sample.setdefault(run["sample_id"], []).append(run["candidate_id"])
        assert all(candidate_ids == sorted(candidate_ids) for candidate_ids in candidate_ids_by_sample.values())

        review_tasks_response = client.get(f"/api/projects/default/documents/{document_id}/export.goldsmith.review-tasks.jsonl")
        assert review_tasks_response.status_code == 200
        assert review_tasks_response.headers["content-disposition"] == f'attachment; filename="{document_id}.goldsmith.review-tasks.jsonl"'
        review_tasks = [json.loads(line) for line in review_tasks_response.text.splitlines()]
        assert review_tasks
        assert review_tasks[0]["schema_version"] == "annopilot.goldsmith_review_tasks.v1"
        assert review_tasks[0]["record_type"] == "human_review_task"
        assert review_tasks[0]["manual_option_id"] == "__manual__"
        assert review_tasks[0]["route"] in {"low", "medium"}
        assert review_tasks[0]["priority"] >= 50
        assert review_tasks[0]["candidate_count"] == len(review_tasks[0]["options"])
        assert review_tasks[0]["options"][0]["option_id"] == "A"
        assert review_tasks[0]["options"][0]["candidate_id"]
        assert review_tasks[0]["options"][0]["action_hint"]
        assert [option["candidate_id"] for option in review_tasks[0]["options"]] == sorted(
            option["candidate_id"] for option in review_tasks[0]["options"]
        )
        assert "[" in review_tasks[0]["options"][0]["annotation_markup"]
        assert review_tasks[0]["review_guidance"]["domain"] == "appraisal_engagement"
        assert review_tasks[0]["review_guidance"]["primary_action"] in {"expert_boundary_review", "compare_candidates"}
        assert review_tasks[0]["review_guidance"]["risk_reason_codes"] == sorted(
            review_tasks[0]["review_guidance"]["risk_reason_codes"]
        )
        assert "candidate_conflict" in review_tasks[0]["review_guidance"]["risk_reason_codes"]
        assert review_tasks[0]["review_guidance"]["span_conflict_summary"]["candidate_count"] == review_tasks[0]["candidate_count"]
        assert (
            review_tasks[0]["review_guidance"]["span_conflict_summary"]["has_boundary_conflict"]
            or review_tasks[0]["review_guidance"]["span_conflict_summary"]["has_label_conflict"]
        )
        assert review_tasks[0]["review_guidance"]["boundary_checks"]
        assert review_tasks[0]["consistency"]["rosetta_route"] == review_tasks[0]["route"]
        assert review_tasks[0]["meta"]["rosetta_reference"] == "human_review_queue.jsonl"
        assert review_tasks[0]["meta"]["option_order"] == "candidate_id"

        manifest = client.get(f"/api/projects/default/documents/{document_id}/export.manifest.json").json()
        assert manifest["artifacts"]["goldsmith_review_tasks_jsonl"]["schema_version"] == "annopilot.goldsmith_review_tasks.v1"
        assert manifest["artifacts"]["goldsmith_review_tasks_jsonl"]["line_count"] == len(review_tasks)

        events_response = client.get("/api/projects/default/events.jsonl")
        assert events_response.status_code == 200
        events = [json.loads(line) for line in events_response.text.splitlines()]
        generated_events = [event for event in events if event["type"] == "suggestions.generated"]
        assert generated_events[-1]["recipe"] == "goldsmith_rosetta_calibration"
        assert generated_events[-1]["source_counts"] == {"calibration_seed": loaded["suggestions_created"]}


def test_appraisal_engagement_review_context_includes_guidelines(tmp_path: Path) -> None:
    storage = AnnotationStorage(
        database_path=tmp_path / "runtime" / "annopilot.sqlite",
        data_root=tmp_path / "projects",
    )
    with TestClient(create_app(storage)) as client:
        load_response = client.post(
            "/api/projects/default/sample-presets/appraisal-engagement-cn-en/load",
            json={"auto_accept_suggestions": False, "complete_sentences": False},
        )
        assert load_response.status_code == 200
        document_id = load_response.json()["document_id"]
        queue = client.get(f"/api/projects/default/documents/{document_id}/review-queue?order=goldsmith").json()
        suggestion_id = queue["items"][0]["first_suggestion"]["id"]

        context = storage.get_suggestion_review_context("default", suggestion_id)

    assert context["review_guidance"]["domain"] == "appraisal_engagement"
    assert context["review_guidance"]["framework"]["name"] == "Appraisal Theory: Engagement"
    assert any("Monogloss" in rule for rule in context["review_guidance"]["framework"]["boundary_rules"])
    assert context["tag_schema"]["tag_count"] == 9
    assert len(context["tags"]) == 9
    assert all("description" in tag and "examples" in tag for tag in context["tags"])
    suggested_tag = next(tag for tag in context["tags"] if tag["id"] == context["suggestion"]["tag_id"])
    assert context["suggestion"]["tag_description"] == suggested_tag["description"]
    assert context["suggestion"]["tag_examples"] == suggested_tag["examples"]
    assert context["suggestion"]["tag_examples"]


def test_appraisal_engagement_human_review_session_exports_prodigy_and_goldsmith_bundle(tmp_path: Path) -> None:
    storage = AnnotationStorage(
        database_path=tmp_path / "runtime" / "annopilot.sqlite",
        data_root=tmp_path / "projects",
    )
    with TestClient(create_app(storage, suggestion_reviewer=FakeSuggestionReviewer())) as client:
        load_response = client.post(
            "/api/projects/default/sample-presets/appraisal-engagement-finance-investor-cn-en/load",
            json={"auto_accept_suggestions": False, "complete_sentences": False},
        )
        assert load_response.status_code == 200
        loaded = load_response.json()
        document_id = loaded["document_id"]
        assert loaded["suggestions_created"] >= 20

        queue_response = client.get(f"/api/projects/default/documents/{document_id}/review-queue?order=goldsmith&limit=10")
        assert queue_response.status_code == 200
        queue = queue_response.json()
        assert queue["total"] > 0
        rejected_suggestion = queue["items"][0]["first_suggestion"]
        rejected_suggestion_id = rejected_suggestion["id"]

        review_response = client.post(f"/api/projects/default/suggestions/{rejected_suggestion_id}/llm-review")
        assert review_response.status_code == 200
        review = review_response.json()
        assert review["recommendation"] == "accept"
        assert review["model"] == "fake-gpt5.5"

        reject_response = client.post(f"/api/projects/default/suggestions/{rejected_suggestion_id}/reject")
        assert reject_response.status_code == 200
        assert reject_response.json()["rejected"] is True

        document_after_reject = client.get(f"/api/projects/default/documents/{document_id}").json()
        pending_suggestions = [
            suggestion
            for sentence in document_after_reject["sentences"]
            for suggestion in sentence["suggestions"]
            if suggestion["id"] != rejected_suggestion_id
        ]
        assert pending_suggestions
        accepted_suggestion = pending_suggestions[0]

        accept_response = client.post(f"/api/projects/default/suggestions/{accepted_suggestion['id']}/accept")
        assert accept_response.status_code == 200
        accepted_payload = accept_response.json()
        assert accepted_payload["accepted"] is True
        accepted_annotation = next(
            annotation for annotation in accepted_payload["annotations"] if annotation["source_suggestion_id"] == accepted_suggestion["id"]
        )
        assert accepted_annotation["source"] == "accepted_suggestion"

        prodigy_response = client.get(f"/api/projects/default/documents/{document_id}/export.prodigy.jsonl")
        assert prodigy_response.status_code == 200
        prodigy_lines = [json.loads(line) for line in prodigy_response.text.splitlines()]
        assert any(line["_view_id"] == "ner_manual" for line in prodigy_lines)
        assert any(
            span["label"] == accepted_annotation["tag_name"]
            and line["text"][span["start"] : span["end"]] == accepted_annotation["text"]
            for line in prodigy_lines
            for span in line["spans"]
        )
        assert any(
            source["source"] == "accepted_suggestion" and source["source_suggestion_id"] == accepted_suggestion["id"]
            for line in prodigy_lines
            for source in line["meta"]["annotation_sources"]
        )

        choices_response = client.get(f"/api/projects/default/documents/{document_id}/export.goldsmith.human-choices.jsonl")
        assert choices_response.status_code == 200
        choices = [json.loads(line) for line in choices_response.text.splitlines()]
        choices_by_id = {choice["suggestion_id"]: choice for choice in choices}
        assert choices_by_id[rejected_suggestion_id]["human_decision"] == "reject"
        assert choices_by_id[rejected_suggestion_id]["latest_review"]["recommendation"] == "accept"
        assert choices_by_id[rejected_suggestion_id]["disagreement"] is True
        assert choices_by_id[accepted_suggestion["id"]]["human_decision"] == "accept"
        assert choices_by_id[accepted_suggestion["id"]]["span"]["text"] == accepted_annotation["text"]

        hard_examples_response = client.get(f"/api/projects/default/documents/{document_id}/export.goldsmith.hard-examples.jsonl")
        assert hard_examples_response.status_code == 200
        hard_examples = [json.loads(line) for line in hard_examples_response.text.splitlines()]
        rejected_hard_example = next(example for example in hard_examples if example["suggestion_id"] == rejected_suggestion_id)
        assert rejected_hard_example["schema_version"] == "annopilot.goldsmith_hard_examples.v1"
        assert rejected_hard_example["hard_example_reasons"] == ["llm_human_disagreement", "human_rejected_suggestion"]
        assert rejected_hard_example["human_decision"] == "reject"

        boundary_response = client.get(f"/api/projects/default/documents/{document_id}/export.goldsmith.boundary-feedback.jsonl")
        assert boundary_response.status_code == 200
        boundary_feedback = [json.loads(line) for line in boundary_response.text.splitlines()]
        rejected_boundary = next(item for item in boundary_feedback if item["suggestion_id"] == rejected_suggestion_id)
        assert rejected_boundary["schema_version"] == "annopilot.goldsmith_boundary_feedback.v1"
        assert rejected_boundary["source_type"] == "human_choice"
        assert rejected_boundary["feedback_polarity"] == "negative"
        assert rejected_boundary["latest_review"]["recommendation"] == "accept"

        candidate_runs_response = client.get(f"/api/projects/default/documents/{document_id}/export.goldsmith.candidate-runs.jsonl")
        assert candidate_runs_response.status_code == 200
        candidate_runs = [json.loads(line) for line in candidate_runs_response.text.splitlines()]
        assert candidate_runs
        assert candidate_runs[0]["schema_version"] == "rosetta.prodigy_candidate.v1"
        assert candidate_runs[0]["meta"]["rosetta_reference"] == "candidate_runs.jsonl"

        manifest = client.get(f"/api/projects/default/documents/{document_id}/export.manifest.json").json()
        assert manifest["metrics"]["calibration_count"] == 1
        assert manifest["metrics"]["calibration_disagreement_count"] == 1
        assert manifest["metrics"]["calibration_error_rate"] == 1.0
        assert manifest["artifacts"]["prodigy_jsonl"]["schema_version"] == "prodigy.ner_manual.compat.v1"
        assert manifest["artifacts"]["goldsmith_human_choices_jsonl"]["line_count"] == 2
        assert manifest["artifacts"]["goldsmith_hard_examples_jsonl"]["line_count"] >= 1
        assert manifest["artifacts"]["goldsmith_boundary_feedback_jsonl"]["line_count"] >= 1
        assert manifest["artifacts"]["goldsmith_candidate_runs_jsonl"]["schema_version"] == "rosetta.prodigy_candidate.v1"


def test_suggestion_review_context_includes_same_label_boundary_feedback(tmp_path: Path) -> None:
    storage = AnnotationStorage(
        database_path=tmp_path / "runtime" / "annopilot.sqlite",
        data_root=tmp_path / "projects",
    )
    with TestClient(create_app(storage, suggestion_reviewer=FakeSuggestionReviewer())) as client:
        tag = client.post("/api/projects/default/tags", json={"name": "Concept", "examples": ["Alpha"]}).json()["tag"]
        response = client.post(
            "/api/projects/default/import-txt",
            files={"file": ("alpha.txt", "Alpha beta. Alpha gamma.", "text/plain")},
        )
        document_id = response.json()["document_id"]
        suggestion_response = client.post(
            f"/api/projects/default/documents/{document_id}/suggestions/run",
            json={"limit_per_sentence": 5, "min_confidence": 0.98},
        )
        suggestions = [suggestion for suggestion in suggestion_response.json()["suggestions"] if suggestion["tag_id"] == tag["id"]]
        assert len(suggestions) >= 2
        rejected_suggestion = suggestions[0]
        target_suggestion = suggestions[1]

        review_response = client.post(f"/api/projects/default/suggestions/{rejected_suggestion['id']}/llm-review")
        assert review_response.status_code == 200
        assert review_response.json()["recommendation"] == "accept"
        reject_response = client.post(f"/api/projects/default/suggestions/{rejected_suggestion['id']}/reject")
        assert reject_response.status_code == 200

        context = storage.get_suggestion_review_context("default", target_suggestion["id"])

    feedback = context["boundary_feedback"]
    assert feedback["schema_version"] == "annopilot.boundary_feedback.v1"
    assert feedback["record_type"] == "boundary_feedback"
    assert feedback["target_tag_id"] == tag["id"]
    assert feedback["negative_example_count"] == 1
    assert feedback["hard_example_count"] == 1
    assert feedback["negative_examples"][0]["suggestion_id"] == rejected_suggestion["id"]
    assert feedback["negative_examples"][0]["human_decision"] == "reject"
    assert feedback["negative_examples"][0]["latest_review"]["recommendation"] == "accept"
    assert feedback["negative_examples"][0]["hard_example_reasons"] == [
        "llm_human_disagreement",
        "human_rejected_suggestion",
    ]
    assert feedback["hard_examples"][0]["suggestion_id"] == rejected_suggestion["id"]
    assert f"[{rejected_suggestion['text']}]" in feedback["hard_examples"][0]["span_context"]


def test_suggestion_review_context_includes_pending_llm_rejected_boundary_feedback(tmp_path: Path) -> None:
    storage = AnnotationStorage(
        database_path=tmp_path / "runtime" / "annopilot.sqlite",
        data_root=tmp_path / "projects",
    )
    with TestClient(create_app(storage, suggestion_reviewer=RejectingSuggestionReviewer())) as client:
        tag = client.post(
            "/api/projects/default/tags",
            json={"name": "Engagement Cue", "description": "Potential cue needing human review.", "examples": ["Alpha"]},
        ).json()["tag"]
        imported = client.post(
            "/api/projects/default/import-txt",
            files={"file": ("llm-boundary.txt", "Alpha beta. Alpha gamma.", "text/plain")},
        ).json()
        document_id = imported["document_id"]
        run = client.post(
            f"/api/projects/default/documents/{document_id}/suggestions/run",
            json={"limit_per_sentence": 5, "min_confidence": 0.98},
        ).json()
        suggestions = [suggestion for suggestion in run["suggestions"] if suggestion["tag_id"] == tag["id"]]
        assert len(suggestions) >= 2
        llm_rejected_suggestion = suggestions[0]
        target_suggestion = suggestions[1]

        review_response = client.post(f"/api/projects/default/suggestions/{llm_rejected_suggestion['id']}/llm-review")
        assert review_response.status_code == 200
        assert review_response.json()["recommendation"] == "reject"

        context = storage.get_suggestion_review_context("default", target_suggestion["id"])

    feedback = context["boundary_feedback"]
    assert feedback["schema_version"] == "annopilot.boundary_feedback.v1"
    assert feedback["target_tag_id"] == tag["id"]
    assert feedback["negative_example_count"] == 1
    assert feedback["hard_example_count"] == 1
    assert feedback["negative_examples"][0]["suggestion_id"] == llm_rejected_suggestion["id"]
    assert feedback["negative_examples"][0]["status"] == "pending"
    assert feedback["negative_examples"][0]["human_decision"] is None
    assert feedback["negative_examples"][0]["latest_review"]["recommendation"] == "reject"
    assert feedback["negative_examples"][0]["hard_example_reasons"] == ["llm_rejected_pending_suggestion"]
    assert feedback["hard_examples"][0]["suggestion_id"] == llm_rejected_suggestion["id"]


def test_suggestion_review_context_includes_pending_llm_uncertain_as_hard_example_only(tmp_path: Path) -> None:
    storage = AnnotationStorage(
        database_path=tmp_path / "runtime" / "annopilot.sqlite",
        data_root=tmp_path / "projects",
    )
    with TestClient(create_app(storage, suggestion_reviewer=UncertainSuggestionReviewer())) as client:
        tag = client.post(
            "/api/projects/default/tags",
            json={"name": "Engagement Cue", "description": "Potential cue needing boundary calibration.", "examples": ["Alpha"]},
        ).json()["tag"]
        imported = client.post(
            "/api/projects/default/import-txt",
            files={"file": ("llm-uncertain-boundary.txt", "Alpha beta. Alpha gamma.", "text/plain")},
        ).json()
        document_id = imported["document_id"]
        run = client.post(
            f"/api/projects/default/documents/{document_id}/suggestions/run",
            json={"limit_per_sentence": 5, "min_confidence": 0.98},
        ).json()
        suggestions = [suggestion for suggestion in run["suggestions"] if suggestion["tag_id"] == tag["id"]]
        assert len(suggestions) >= 2
        uncertain_suggestion = suggestions[0]
        target_suggestion = suggestions[1]

        review_response = client.post(f"/api/projects/default/suggestions/{uncertain_suggestion['id']}/llm-review")
        assert review_response.status_code == 200
        assert review_response.json()["recommendation"] == "uncertain"

        context = storage.get_suggestion_review_context("default", target_suggestion["id"])

    feedback = context["boundary_feedback"]
    assert feedback["schema_version"] == "annopilot.boundary_feedback.v1"
    assert feedback["target_tag_id"] == tag["id"]
    assert feedback["negative_example_count"] == 0
    assert feedback["hard_example_count"] == 1
    assert feedback["hard_examples"][0]["suggestion_id"] == uncertain_suggestion["id"]
    assert feedback["hard_examples"][0]["status"] == "pending"
    assert feedback["hard_examples"][0]["human_decision"] is None
    assert feedback["hard_examples"][0]["latest_review"]["recommendation"] == "uncertain"
    assert feedback["hard_examples"][0]["hard_example_reasons"] == ["llm_uncertain"]


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
        assert sum(suggestion_payload["source_counts"].values()) == len(suggestions)
        assert set(suggestion_payload["source_counts"]).issubset({"lexical_exact", "lexical_contains", "char_ngram"})
        assert sum(suggestion_payload["confidence_counts"].values()) == len(suggestions)
        assert suggestion_payload["confidence_counts"] == {"high": len(suggestions)}
        assert all(suggestion["run_id"] == suggestion_payload["run_id"] for suggestion in suggestions)
        assert all(suggestion["evidence_text"] for suggestion in suggestions)
        assert all(suggestion["match_key"] for suggestion in suggestions)
        assert all(suggestion["evidence_match_key"] for suggestion in suggestions)
        assert any(suggestion["match_key"] == suggestion["evidence_match_key"] for suggestion in suggestions)
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
            "schema_version": "annopilot.match_normalization.v2",
            "steps": ["strip", "collapse_whitespace", "casefold", "remove_cjk_inner_whitespace"],
            "preserves_source_text": True,
        }
        assert runs[0]["config"]["retrieval"] == "offset_gap_span_text|casefold_whitespace_cjk_inner_space_normalized|lexical_exact|lexical_contains|char_ngram"
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
        assert runs[0]["source_counts"] == suggestion_payload["source_counts"]
        assert runs[0]["confidence_counts"] == suggestion_payload["confidence_counts"]
        assert sum(runs[0]["source_counts"].values()) == len(suggestions)
        assert set(runs[0]["source_counts"]).issubset({"lexical_exact", "lexical_contains", "char_ngram"})

        generated_summary = client.get(f"/api/projects/default/documents/{document_id}/summary").json()
        assert generated_summary["metrics"]["suggestion_count"] == len(suggestions)
        assert generated_summary["metrics"]["suggestion_status_counts"] == {
            "pending": len(suggestions),
            "accepted": 0,
            "rejected": 0,
        }
        assert generated_summary["metrics"]["suggestion_source_counts"] == suggestion_payload["source_counts"]
        assert generated_summary["metrics"]["suggestion_confidence_counts"] == suggestion_payload["confidence_counts"]
        assert generated_summary["metrics"]["suggestion_review_counts"] == {"accept": 0, "reject": 0, "uncertain": 0}
        assert generated_summary["metrics"]["reviewed_suggestion_count"] == 0

        consistency_response = client.get(f"/api/projects/default/documents/{document_id}/export.goldsmith.consistency-scores.jsonl")
        assert consistency_response.status_code == 200
        assert consistency_response.headers["content-disposition"] == f'attachment; filename="{document_id}.goldsmith.consistency-scores.jsonl"'
        consistency_lines = [json.loads(line) for line in consistency_response.text.splitlines()]
        assert consistency_lines
        assert consistency_lines[0]["schema_version"] == "annopilot.goldsmith_consistency_scores.v1"
        assert consistency_lines[0]["record_type"] == "consistency_score"
        assert consistency_lines[0]["diagnostic_scope"] == "visible_pending_suggestions"
        assert consistency_lines[0]["scoring_mode"] == "character_rag_llm_review_proxy"
        assert 0 <= consistency_lines[0]["score"] <= 1
        assert 0 <= consistency_lines[0]["agreement"] <= 1
        assert 0 <= consistency_lines[0]["pairwise_span_f1"] <= 1
        assert consistency_lines[0]["average_model_confidence"] == consistency_lines[0]["avg_confidence"]
        assert 0 <= consistency_lines[0]["uncertainty_score"] <= 1
        assert consistency_lines[0]["rosetta_route"] in {"high", "medium", "low"}
        assert consistency_lines[0]["review_route"] in {"high_confidence_sample", "light_review", "expert_review"}
        assert consistency_lines[0]["candidate_count"] == len(consistency_lines[0]["candidate_scores"])
        assert 0 <= consistency_lines[0]["candidate_scores"][0]["pairwise_span_f1"] <= 1
        consistency_candidate_ids = {candidate["suggestion_id"] for line in consistency_lines for candidate in line["candidate_scores"]}
        assert consistency_candidate_ids.issubset({suggestion["id"] for suggestion in suggestions})
        assert consistency_lines[0]["meta"]["rosetta_reference"] == "consistency_scores.jsonl"

        candidate_runs_response = client.get(f"/api/projects/default/documents/{document_id}/export.goldsmith.candidate-runs.jsonl")
        assert candidate_runs_response.status_code == 200
        assert candidate_runs_response.headers["content-disposition"] == f'attachment; filename="{document_id}.goldsmith.candidate-runs.jsonl"'
        candidate_runs = [json.loads(line) for line in candidate_runs_response.text.splitlines()]
        assert candidate_runs
        assert candidate_runs[0]["schema_version"] == "rosetta.prodigy_candidate.v1"
        assert candidate_runs[0]["record_type"] == "prodigy_candidate"
        assert candidate_runs[0]["sample_id"]
        assert candidate_runs[0]["candidate_id"] in {suggestion["id"] for suggestion in suggestions}
        assert candidate_runs[0]["answer"] is None
        assert candidate_runs[0]["spans"][0]["text"] == candidate_runs[0]["text"][candidate_runs[0]["spans"][0]["start"] : candidate_runs[0]["spans"][0]["end"]]
        assert candidate_runs[0]["spans"][0]["label"]
        assert "[" in candidate_runs[0]["runtime_annotation"]["annotation_markup"]
        assert candidate_runs[0]["model_confidence"] >= 0.98
        assert candidate_runs[0]["meta"]["rosetta_reference"] == "candidate_runs.jsonl"
        assert candidate_runs[0]["meta"]["rosetta_route"] in {"high", "medium", "low"}
        assert 0 <= candidate_runs[0]["meta"]["uncertainty_score"] <= 1
        assert candidate_runs[0]["meta"]["candidate_score"]["suggestion_id"] == candidate_runs[0]["candidate_id"]
        assert candidate_runs[0]["meta"]["candidate_score"]["span_f1_to_consensus"] >= 0
        assert candidate_runs[0]["meta"]["consistency"]["diagnostic_scope"] == "visible_pending_suggestions"
        assert candidate_runs[0]["meta"]["consistency"]["scoring_mode"] == "character_rag_llm_review_proxy"
        assert 0 <= candidate_runs[0]["meta"]["consistency"]["pairwise_span_f1"] <= 1
        assert 0 <= candidate_runs[0]["meta"]["consistency"]["uncertainty_score"] <= 1
        assert candidate_runs[0]["meta"]["consistency"]["rosetta_route"] in {"high", "medium", "low"}
        assert candidate_runs[0]["meta"]["consistency"]["candidate_count"] >= 1
        assert candidate_runs[0]["meta"]["consistency"]["review_route"] in {"high_confidence_sample", "light_review", "expert_review"}

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
        assert provenance["run"]["source_counts"] == runs[0]["source_counts"]
        assert provenance["source_counts"] == runs[0]["source_counts"]
        assert provenance["run"]["confidence_counts"] == runs[0]["confidence_counts"]
        assert provenance["confidence_counts"] == runs[0]["confidence_counts"]
        assert provenance["run"]["config"]["examples_by_tag"] == runs[0]["config"]["examples_by_tag"]
        assert provenance["run"]["config"]["examples_match_keys_by_tag"] == runs[0]["config"]["examples_match_keys_by_tag"]
        assert provenance["status_counts"]["accepted"] == 1
        assert provenance["status_counts"]["rejected"] == 1
        assert provenance["status_counts"]["pending"] == len(suggestions) - 2
        assert len(provenance["suggestions"]) == len(suggestions)
        assert provenance["suggestions"][0]["sentence_index"] == 0
        assert provenance["suggestions"][0]["evidence_text"]
        assert provenance["suggestions"][0]["match_key"]
        assert provenance["suggestions"][0]["evidence_match_key"]
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
        assert document["metrics"]["suggestion_count"] == len(suggestions) - 2
        assert document["metrics"]["suggestion_status_counts"] == {
            "pending": len(suggestions) - 2,
            "accepted": 1,
            "rejected": 1,
        }
        assert sum(document["metrics"]["suggestion_source_counts"].values()) == len(suggestions) - 2
        assert sum(document["metrics"]["suggestion_confidence_counts"].values()) == len(suggestions) - 2
        assert document["metrics"]["suggestion_review_counts"] == {"accept": 0, "reject": 0, "uncertain": 0}
        assert document["metrics"]["reviewed_suggestion_count"] == 0
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
        assert annotated_prodigy_lines[0]["answer"] == "accept"
        assert annotated_prodigy_lines[0]["meta"]["answer"] == "pending"
        prodigy_sources = [source for line in prodigy_lines for source in line["meta"]["annotation_sources"]]
        assert prodigy_sources[0]["annotation_id"] == accepted_annotation["id"]
        assert prodigy_sources[0]["source"] == "accepted_suggestion"
        assert prodigy_sources[0]["source_suggestion_id"] == suggestions[0]["id"]

        manifest = client.get(f"/api/projects/default/documents/{document_id}/export.manifest.json").json()
        assert manifest["source_run_ids"] == [suggestion_payload["run_id"]]
        assert manifest["metrics"]["suggestion_count"] == len(suggestions) - 2
        assert manifest["metrics"]["suggestion_status_counts"] == {
            "pending": len(suggestions) - 2,
            "accepted": 1,
            "rejected": 1,
        }
        assert sum(manifest["metrics"]["suggestion_source_counts"].values()) == len(suggestions) - 2
        assert sum(manifest["metrics"]["suggestion_confidence_counts"].values()) == len(suggestions) - 2
        assert manifest["metrics"]["suggestion_review_counts"] == {"accept": 0, "reject": 0, "uncertain": 0}
        assert manifest["metrics"]["reviewed_suggestion_count"] == 0
        assert manifest["prodigy_readiness"]["ready"] is False
        assert "pending_suggestions" in manifest["prodigy_readiness"]["blockers"]
        assert manifest["prodigy_readiness"]["pending_suggestion_count"] == len(suggestions) - 2
        assert manifest["prodigy_readiness"]["formats"]["ner_manual"] == "prodigy.ner_manual.compat.v1"
        assert manifest["annotation_source_counts"] == {"accepted_suggestion": 1}
        assert manifest["event_audit"]["event_types"]["suggestions.generated"] == 1
        assert manifest["event_audit"]["actor_type_counts"]["system"] >= 2
        assert manifest["event_audit"]["actor_id_counts"]["annopilot-character-rag"] >= 2
        assert manifest["runs"][0]["config"]["min_confidence"] == 0.98
        assert manifest["runs"][0]["source_counts"] == suggestion_payload["source_counts"]
        assert manifest["runs"][0]["confidence_counts"] == suggestion_payload["confidence_counts"]
        assert manifest["run_provenance_artifacts"][suggestion_payload["run_id"]]["schema_version"] == "annopilot.run_provenance.v1"
        assert manifest["run_provenance_artifacts"][suggestion_payload["run_id"]]["filename"].endswith(".provenance.json")
        assert manifest["run_provenance_artifacts"][suggestion_payload["run_id"]]["content_sha256"] == provenance["content_sha256"]
        assert manifest["artifacts"]["goldsmith_consistency_scores_jsonl"]["schema_version"] == "annopilot.goldsmith_consistency_scores.v1"
        assert manifest["artifacts"]["goldsmith_candidate_runs_jsonl"]["schema_version"] == "rosetta.prodigy_candidate.v1"
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
        assert generated_event["source_counts"] == suggestion_payload["source_counts"]
        assert generated_event["confidence_counts"] == suggestion_payload["confidence_counts"]
        assert generated_event["config"]["limit_per_sentence"] == 2
        assert generated_event["config"]["min_confidence"] == 0.98
        assert generated_event["config"]["tag_schema_sha256"] == runs[0]["config"]["tag_schema_sha256"]
        assert generated_event["config"]["examples_sha256"] == runs[0]["config"]["examples_sha256"]
        assert generated_event["config"]["negative_examples_sha256"] == runs[0]["config"]["negative_examples_sha256"]
        assert generated_event["suggestions"][0]["id"] == suggestions[0]["id"]
        assert generated_event["suggestions"][0]["status"] == "pending"
        assert generated_event["suggestions"][0]["evidence_text"] == suggestions[0]["evidence_text"]
        assert generated_event["suggestions"][0]["match_key"] == suggestions[0]["match_key"]
        assert generated_event["suggestions"][0]["evidence_match_key"] == suggestions[0]["evidence_match_key"]
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
        assert suggestion_response.json()["confidence_counts"]["high"] >= 1
        assert suggestion_response.json()["confidence_counts"].get("medium", 0) >= 1
        assert any(suggestion["sentence_id"] == exact_sentence_id and suggestion["confidence"] == 0.98 for suggestion in suggestions)
        assert any(
            suggestion["sentence_id"] == uncertain_sentence_id
            and suggestion["tag_id"] == concept_tag["id"]
            and suggestion["confidence"] < 0.98
            for suggestion in suggestions
        )

        by_position = client.get(f"/api/projects/default/documents/{document_id}/review-queue?order=position").json()
        assert [item["id"] for item in by_position["items"]] == [exact_sentence_id, uncertain_sentence_id]

        by_random = client.get(f"/api/projects/default/documents/{document_id}/review-queue?order=random").json()
        repeat_random = client.get(f"/api/projects/default/documents/{document_id}/review-queue?order=random").json()
        assert [item["id"] for item in by_random["items"]] == [item["id"] for item in repeat_random["items"]]
        assert {item["id"] for item in by_random["items"]} == {exact_sentence_id, uncertain_sentence_id}
        assert all(item["review_route"] == "random" for item in by_random["items"])

        by_uncertainty = client.get(f"/api/projects/default/documents/{document_id}/review-queue?order=uncertain").json()
        assert [item["id"] for item in by_uncertainty["items"]] == [uncertain_sentence_id, exact_sentence_id]
        assert by_uncertainty["items"][0]["priority_score"] < by_uncertainty["items"][1]["priority_score"]

        invalid_order = client.get(f"/api/projects/default/documents/{document_id}/review-queue?order=shuffle")
        assert invalid_order.status_code == 400


def test_review_queue_can_prioritize_goldsmith_risk_density(tmp_path: Path) -> None:
    storage = AnnotationStorage(
        database_path=tmp_path / "runtime" / "annopilot.sqlite",
        data_root=tmp_path / "projects",
    )
    with TestClient(create_app(storage)) as client:
        imported = client.post(
            "/api/projects/default/import-txt",
            files={"file": ("risk.txt", "Alpha beta gamma. Delta epsilon zeta.", "text/plain")},
        ).json()
        document_id = imported["document_id"]
        tag = client.post(
            "/api/projects/default/tags",
            json={"name": "风险", "description": "需要复核的候选 span。"},
        ).json()["tag"]
        page = client.get(f"/api/projects/default/documents/{document_id}/sentences?offset=0&limit=2").json()
        first_sentence = page["sentences"][0]
        second_sentence = page["sentences"][1]

        with storage.connect() as conn:
            now = "2026-01-01T00:00:00Z"
            controlled_suggestions = [
                ("sg-low-single", first_sentence, first_sentence["tokens"][0], 0.55),
                ("sg-medium-a", second_sentence, second_sentence["tokens"][0], 0.74),
                ("sg-medium-b", second_sentence, second_sentence["tokens"][1], 0.75),
            ]
            for suggestion_id, sentence, token, confidence in controlled_suggestions:
                conn.execute(
                    """
                    INSERT INTO annotation_suggestions (
                      id, sentence_id, tag_id, start_token_index, end_token_index,
                      start_char, end_char, text, confidence, source, status, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?)
                    """,
                    (
                        suggestion_id,
                        sentence["id"],
                        tag["id"],
                        token["token_index"],
                        token["token_index"],
                        token["start_char"],
                        token["end_char"],
                        token["text"],
                        confidence,
                        "test",
                        now,
                    ),
                )

        by_position = client.get(f"/api/projects/default/documents/{document_id}/review-queue?order=position").json()
        assert [item["id"] for item in by_position["items"]] == [first_sentence["id"], second_sentence["id"]]

        by_uncertainty = client.get(f"/api/projects/default/documents/{document_id}/review-queue?order=uncertain").json()
        assert [item["id"] for item in by_uncertainty["items"]] == [first_sentence["id"], second_sentence["id"]]

        by_goldsmith = client.get(f"/api/projects/default/documents/{document_id}/review-queue?order=goldsmith").json()
        assert [item["id"] for item in by_goldsmith["items"]] == [second_sentence["id"], first_sentence["id"]]
        assert by_goldsmith["items"][0]["suggestion_count"] == 2
        assert by_goldsmith["items"][0]["min_confidence"] == 0.74
        assert by_goldsmith["items"][0]["priority_score"] == 0.74
        assert round(by_goldsmith["items"][0]["risk_score"], 2) == 0.52
        assert by_goldsmith["items"][0]["risk_score"] > by_goldsmith["items"][1]["risk_score"]


def test_review_queue_goldsmith_prioritizes_candidate_disagreement(tmp_path: Path) -> None:
    storage = AnnotationStorage(
        database_path=tmp_path / "runtime" / "annopilot.sqlite",
        data_root=tmp_path / "projects",
    )
    with TestClient(create_app(storage)) as client:
        imported = client.post(
            "/api/projects/default/import-txt",
            files={"file": ("candidate-conflict.txt", "Alpha beta. Gamma delta.", "text/plain")},
        ).json()
        document_id = imported["document_id"]
        first_tag = client.post(
            "/api/projects/default/tags",
            json={"name": "Entertain", "description": "Engagement entertain cue."},
        ).json()["tag"]
        second_tag = client.post(
            "/api/projects/default/tags",
            json={"name": "Disclaim", "description": "Engagement disclaim cue."},
        ).json()["tag"]
        page = client.get(f"/api/projects/default/documents/{document_id}/sentences?offset=0&limit=2").json()
        first_sentence = page["sentences"][0]
        second_sentence = page["sentences"][1]

        with storage.connect() as conn:
            now = "2026-01-01T00:00:00Z"

            def insert_suggestion(
                suggestion_id: str,
                sentence: dict,
                tag_id: str,
                start_token_offset: int,
                end_token_offset: int,
                confidence: float,
            ) -> None:
                start_token = sentence["tokens"][start_token_offset]
                end_token = sentence["tokens"][end_token_offset]
                text = sentence["text"][start_token["start_char"] - sentence["start_char"] : end_token["end_char"] - sentence["start_char"]]
                conn.execute(
                    """
                    INSERT INTO annotation_suggestions (
                      id, sentence_id, tag_id, start_token_index, end_token_index,
                      start_char, end_char, text, confidence, source, status, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?)
                    """,
                    (
                        suggestion_id,
                        sentence["id"],
                        tag_id,
                        start_token["token_index"],
                        end_token["token_index"],
                        start_token["start_char"],
                        end_token["end_char"],
                        text,
                        confidence,
                        "test",
                        now,
                    ),
                )

            insert_suggestion("sg-conflict-short", first_sentence, first_tag["id"], 0, 0, 0.98)
            insert_suggestion("sg-conflict-wide", first_sentence, second_tag["id"], 0, 1, 0.98)
            insert_suggestion("sg-nonoverlap-a", second_sentence, first_tag["id"], 0, 0, 0.80)
            insert_suggestion("sg-nonoverlap-b", second_sentence, second_tag["id"], 1, 1, 0.81)

        by_goldsmith = client.get(f"/api/projects/default/documents/{document_id}/review-queue?order=goldsmith&limit=2").json()
        assert [item["id"] for item in by_goldsmith["items"]] == [first_sentence["id"], second_sentence["id"]]
        assert by_goldsmith["items"][0]["candidate_disagreement_score"] == 1.0
        assert by_goldsmith["items"][1]["candidate_disagreement_score"] == 0.0
        assert round(by_goldsmith["items"][0]["lexical_risk_score"], 2) == 0.04
        assert round(by_goldsmith["items"][0]["risk_score"], 2) == 1.04
        assert round(by_goldsmith["items"][1]["risk_score"], 2) == 0.40
        assert by_goldsmith["items"][0]["action_hint"].startswith("Conflicting candidates")
        assert by_goldsmith["items"][0]["review_guidance"]["domain"] == "appraisal_engagement"
        assert by_goldsmith["items"][0]["review_guidance"]["primary_action"] == "compare_candidates"
        assert by_goldsmith["items"][0]["review_guidance"]["action_hint"] == by_goldsmith["items"][0]["action_hint"]
        assert "candidate_conflict" in by_goldsmith["items"][0]["review_guidance"]["risk_reason_codes"]
        assert by_goldsmith["items"][0]["review_guidance"]["boundary_checks"]

        review_queue_export = client.get(
            f"/api/projects/default/documents/{document_id}/export.goldsmith.review-queue.jsonl?order=goldsmith&limit=2"
        )
        assert review_queue_export.status_code == 200
        review_queue_lines = [json.loads(line) for line in review_queue_export.text.splitlines()]
        assert review_queue_lines[0]["sentence_id"] == first_sentence["id"]
        assert review_queue_lines[0]["candidate_disagreement_score"] == 1.0
        assert review_queue_lines[0]["action_hint"] == by_goldsmith["items"][0]["action_hint"]
        assert review_queue_lines[0]["review_guidance"]["primary_action"] == "compare_candidates"


def test_review_queue_goldsmith_uses_llm_review_risk_signal(tmp_path: Path) -> None:
    storage = AnnotationStorage(
        database_path=tmp_path / "runtime" / "annopilot.sqlite",
        data_root=tmp_path / "projects",
    )
    with TestClient(create_app(storage)) as client:
        imported = client.post(
            "/api/projects/default/import-txt",
            files={"file": ("llm-risk.txt", "Stable cue. Dense lexical risk.", "text/plain")},
        ).json()
        document_id = imported["document_id"]
        tag = client.post(
            "/api/projects/default/tags",
            json={"name": "复核", "description": "需要优先复核的候选。"},
        ).json()["tag"]
        page = client.get(f"/api/projects/default/documents/{document_id}/sentences?offset=0&limit=2").json()
        first_sentence = page["sentences"][0]
        second_sentence = page["sentences"][1]

        with storage.connect() as conn:
            now = "2026-01-01T00:00:00Z"
            controlled_suggestions = [
                ("sg-llm-reject", first_sentence, first_sentence["tokens"][0], 0.98, "reject"),
                ("sg-dense-a", second_sentence, second_sentence["tokens"][0], 0.74, None),
                ("sg-dense-b", second_sentence, second_sentence["tokens"][1], 0.75, None),
            ]
            for suggestion_id, sentence, token, confidence, recommendation in controlled_suggestions:
                conn.execute(
                    """
                    INSERT INTO annotation_suggestions (
                      id, sentence_id, tag_id, start_token_index, end_token_index,
                      start_char, end_char, text, confidence, source, status, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?)
                    """,
                    (
                        suggestion_id,
                        sentence["id"],
                        tag["id"],
                        token["token_index"],
                        token["token_index"],
                        token["start_char"],
                        token["end_char"],
                        token["text"],
                        confidence,
                        "test",
                        now,
                    ),
                )
                if recommendation:
                    conn.execute(
                        """
                        INSERT INTO annotation_suggestion_reviews (
                          id, suggestion_id, model, recommendation, confidence, rationale, context_sha256, created_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            f"rev-{suggestion_id}",
                            suggestion_id,
                            "fake-gpt5.5",
                            recommendation,
                            0.89,
                            "LLM marked this high-confidence lexical cue as boundary-risky.",
                            hashlib.sha256(suggestion_id.encode("utf-8")).hexdigest(),
                            now,
                        ),
                    )

        by_goldsmith = client.get(f"/api/projects/default/documents/{document_id}/review-queue?order=goldsmith").json()
        assert [item["id"] for item in by_goldsmith["items"]] == [first_sentence["id"], second_sentence["id"]]
        assert round(by_goldsmith["items"][0]["lexical_risk_score"], 2) == 0.02
        assert by_goldsmith["items"][0]["llm_review_risk_score"] == 1.0
        assert by_goldsmith["items"][0]["risk_reason_codes"] == ["llm_reject"]
        assert round(by_goldsmith["items"][0]["risk_score"], 2) == 1.02
        assert round(by_goldsmith["items"][1]["risk_score"], 2) == 0.52
        assert by_goldsmith["items"][0]["first_suggestion"]["id"] == "sg-llm-reject"
        assert by_goldsmith["items"][0]["first_suggestion"]["latest_review"]["recommendation"] == "reject"

        by_hybrid = client.get(f"/api/projects/default/documents/{document_id}/review-queue?order=hybrid&limit=5").json()
        assert by_hybrid["items"][0]["id"] == first_sentence["id"]
        assert by_hybrid["items"][0]["review_route"] == "risk"

        review_queue_export = client.get(
            f"/api/projects/default/documents/{document_id}/export.goldsmith.review-queue.jsonl?order=goldsmith&limit=5"
        )
        assert review_queue_export.status_code == 200
        review_queue_lines = [json.loads(line) for line in review_queue_export.text.splitlines()]
        assert review_queue_lines[0]["sentence_id"] == first_sentence["id"]
        assert round(review_queue_lines[0]["lexical_risk_score"], 2) == 0.02
        assert review_queue_lines[0]["llm_review_risk_score"] == 1.0
        assert review_queue_lines[0]["risk_reason_codes"] == ["llm_reject"]
        assert round(review_queue_lines[0]["risk_score"], 2) == 1.02
        assert review_queue_lines[0]["first_suggestion"]["latest_review"]["recommendation"] == "reject"

        boundary_feedback_response = client.get(
            f"/api/projects/default/documents/{document_id}/export.goldsmith.boundary-feedback.jsonl"
        )
        assert boundary_feedback_response.status_code == 200
        boundary_feedback = [json.loads(line) for line in boundary_feedback_response.text.splitlines()]
        assert len(boundary_feedback) == 1
        assert boundary_feedback[0]["schema_version"] == "annopilot.goldsmith_boundary_feedback.v1"
        assert boundary_feedback[0]["record_type"] == "boundary_feedback"
        assert boundary_feedback[0]["source_type"] == "llm_reviewed_pending_suggestion"
        assert boundary_feedback[0]["feedback_polarity"] == "negative"
        assert boundary_feedback[0]["suggestion_id"] == "sg-llm-reject"
        assert boundary_feedback[0]["human_decision"] is None
        assert boundary_feedback[0]["latest_review"]["recommendation"] == "reject"
        assert boundary_feedback[0]["hard_example_reasons"] == ["llm_rejected_pending_suggestion"]
        assert boundary_feedback[0]["risk_reason_codes"] == ["llm_reject"]
        assert "boundary feedback" in boundary_feedback[0]["failure_note"]

        manifest = client.get(f"/api/projects/default/documents/{document_id}/export.manifest.json").json()
        boundary_artifact = manifest["artifacts"]["goldsmith_boundary_feedback_jsonl"]
        assert boundary_artifact["schema_version"] == "annopilot.goldsmith_boundary_feedback.v1"
        assert boundary_artifact["line_count"] == 1


def test_review_queue_goldsmith_uses_judge_review_risk_signal(tmp_path: Path) -> None:
    storage = AnnotationStorage(
        database_path=tmp_path / "runtime" / "annopilot.sqlite",
        data_root=tmp_path / "projects",
    )
    with TestClient(create_app(storage)) as client:
        imported = client.post(
            "/api/projects/default/import-txt",
            files={"file": ("judge-risk.txt", "Stable cue. Dense lexical risk.", "text/plain")},
        ).json()
        document_id = imported["document_id"]
        tag = client.post(
            "/api/projects/default/tags",
            json={"name": "Engagement", "description": "Appraisal engagement cue."},
        ).json()["tag"]
        page = client.get(f"/api/projects/default/documents/{document_id}/sentences?offset=0&limit=2").json()
        first_sentence = page["sentences"][0]
        second_sentence = page["sentences"][1]

        with storage.connect() as conn:
            now = "2026-01-01T00:00:00Z"
            controlled_suggestions = [
                ("sg-judge-risk", first_sentence, first_sentence["tokens"][0], 0.99),
                ("sg-dense-a", second_sentence, second_sentence["tokens"][0], 0.74),
                ("sg-dense-b", second_sentence, second_sentence["tokens"][1], 0.75),
            ]
            for suggestion_id, sentence, token, confidence in controlled_suggestions:
                conn.execute(
                    """
                    INSERT INTO annotation_suggestions (
                      id, sentence_id, tag_id, start_token_index, end_token_index,
                      start_char, end_char, text, confidence, source, status, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?)
                    """,
                    (
                        suggestion_id,
                        sentence["id"],
                        tag["id"],
                        token["token_index"],
                        token["token_index"],
                        token["start_char"],
                        token["end_char"],
                        token["text"],
                        confidence,
                        "test",
                        now,
                    ),
                )
            conn.execute(
                """
                INSERT INTO annotation_suggestion_reviews (
                  id, suggestion_id, model, recommendation, confidence, rationale, context_sha256, judge_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "rev-sg-judge-risk",
                    "sg-judge-risk",
                    "fake-gpt5.5",
                    "accept",
                    0.92,
                    "Candidate is plausible but judge found likely boundary and under-annotation risk.",
                    hashlib.sha256(b"sg-judge-risk").hexdigest(),
                    json.dumps(
                        {
                            "format_score": 1.0,
                            "concept_fit_score": 0.91,
                            "boundary_score": 0.2,
                            "relation_score": 1.0,
                            "missed_span_risk": 0.82,
                            "extra_span_risk": 0.1,
                            "overall_score": 0.88,
                            "needs_review": True,
                            "error_types": ["boundary_too_wide"],
                            "risk_flags": ["possible_under_annotation"],
                        },
                        ensure_ascii=False,
                    ),
                    now,
                ),
            )

        by_goldsmith = client.get(f"/api/projects/default/documents/{document_id}/review-queue?order=goldsmith").json()
        assert [item["id"] for item in by_goldsmith["items"]] == [first_sentence["id"], second_sentence["id"]]
        assert round(by_goldsmith["items"][0]["lexical_risk_score"], 2) == 0.01
        assert by_goldsmith["items"][0]["llm_review_risk_score"] == 0.0
        assert by_goldsmith["items"][0]["judge_review_risk_score"] == 0.82
        assert by_goldsmith["items"][0]["risk_reason_codes"] == [
            "judge_needs_review",
            "judge_boundary",
            "judge_missing_span",
        ]
        assert by_goldsmith["items"][1]["risk_reason_codes"] == ["low_confidence", "dense_candidates"]
        assert round(by_goldsmith["items"][0]["risk_score"], 2) == 0.83
        assert round(by_goldsmith["items"][1]["risk_score"], 2) == 0.52
        assert by_goldsmith["items"][0]["first_suggestion"]["id"] == "sg-judge-risk"
        assert by_goldsmith["items"][0]["first_suggestion"]["latest_review"]["judge"]["needs_review"] is True

        review_queue_export = client.get(
            f"/api/projects/default/documents/{document_id}/export.goldsmith.review-queue.jsonl?order=goldsmith&limit=5"
        )
        assert review_queue_export.status_code == 200
        review_queue_lines = [json.loads(line) for line in review_queue_export.text.splitlines()]
        assert review_queue_lines[0]["sentence_id"] == first_sentence["id"]
        assert review_queue_lines[0]["judge_review_risk_score"] == 0.82
        assert review_queue_lines[0]["risk_reason_codes"] == [
            "judge_needs_review",
            "judge_boundary",
            "judge_missing_span",
        ]
        assert round(review_queue_lines[0]["risk_score"], 2) == 0.83


def test_review_queue_hybrid_reserves_high_confidence_calibration_sample(tmp_path: Path) -> None:
    storage = AnnotationStorage(
        database_path=tmp_path / "runtime" / "annopilot.sqlite",
        data_root=tmp_path / "projects",
    )
    with TestClient(create_app(storage)) as client:
        imported = client.post(
            "/api/projects/default/import-txt",
            files={
                "file": (
                    "hybrid.txt",
                    "Alpha beta. Delta epsilon zeta. Medium risk phrase. Stable cue. Another risk. Last risk.",
                    "text/plain",
                )
            },
        ).json()
        document_id = imported["document_id"]
        tag = client.post(
            "/api/projects/default/tags",
            json={"name": "复核", "description": "混合复核候选。"},
        ).json()["tag"]
        page = client.get(f"/api/projects/default/documents/{document_id}/sentences?offset=0&limit=6").json()
        sentences = page["sentences"]

        with storage.connect() as conn:
            now = "2026-01-01T00:00:00Z"

            def insert_suggestion(sentence_index: int, token_index: int, confidence: float) -> None:
                sentence = sentences[sentence_index]
                token = sentence["tokens"][token_index]
                conn.execute(
                    """
                    INSERT INTO annotation_suggestions (
                      id, sentence_id, tag_id, start_token_index, end_token_index,
                      start_char, end_char, text, confidence, source, status, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?)
                    """,
                    (
                        f"sg-{sentence_index}-{token_index}-{confidence}",
                        sentence["id"],
                        tag["id"],
                        token["token_index"],
                        token["token_index"],
                        token["start_char"],
                        token["end_char"],
                        token["text"],
                        confidence,
                        "test",
                        now,
                    ),
                )

            insert_suggestion(0, 0, 0.55)
            insert_suggestion(1, 0, 0.74)
            insert_suggestion(1, 1, 0.75)
            insert_suggestion(2, 0, 0.86)
            insert_suggestion(2, 1, 0.87)
            insert_suggestion(2, 2, 0.88)
            insert_suggestion(3, 0, 0.98)
            insert_suggestion(4, 0, 0.60)
            insert_suggestion(5, 0, 0.61)

        by_goldsmith = client.get(f"/api/projects/default/documents/{document_id}/review-queue?order=goldsmith&limit=5").json()
        assert [item["id"] for item in by_goldsmith["items"]] == [
            sentences[1]["id"],
            sentences[0]["id"],
            sentences[2]["id"],
            sentences[4]["id"],
            sentences[5]["id"],
        ]

        by_hybrid = client.get(f"/api/projects/default/documents/{document_id}/review-queue?order=hybrid&limit=5").json()
        assert [item["id"] for item in by_hybrid["items"]] == [
            sentences[1]["id"],
            sentences[0]["id"],
            sentences[2]["id"],
            sentences[4]["id"],
            sentences[3]["id"],
        ]
        assert by_hybrid["items"][-1]["review_route"] == "calibration"
        assert by_hybrid["items"][-1]["min_confidence"] == 0.98


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
        assert accepted_payload["completed"] == 0
        assert accepted_payload["completed_sentence_ids"] == []
        assert set(accepted_payload["accepted_suggestion_ids"]) == {suggestion["id"] for suggestion in suggestions}

        document = client.get(f"/api/projects/default/documents/{document_id}").json()
        annotations = [annotation for sentence in document["sentences"] for annotation in sentence["annotations"]]
        assert len(annotations) == len(suggestions)
        assert all(annotation["source"] == "accepted_suggestion" for annotation in annotations)
        assert document["metrics"]["suggestion_count"] == 0

        prodigy_lines = [json.loads(line) for line in client.get(f"/api/projects/default/documents/{document_id}/export.prodigy.jsonl").text.splitlines()]
        annotated_prodigy_lines = [line for line in prodigy_lines if line["spans"]]
        assert annotated_prodigy_lines
        assert all(line["answer"] == "accept" for line in annotated_prodigy_lines)
        assert all(line["meta"]["answer"] == "pending" for line in annotated_prodigy_lines)

        events = [json.loads(line) for line in client.get("/api/projects/default/events.jsonl").text.splitlines()]
        assert sum(1 for event in events if event["type"] == "annotation.created") == len(suggestions)
        assert sum(1 for event in events if event["type"] == "suggestion.accepted") == len(suggestions)
        assert client.get("/api/projects/default/audit").json()["non_replayable_event_count"] == 0

        manifest = client.get(f"/api/projects/default/documents/{document_id}/export.manifest.json").json()
        assert manifest["annotation_source_counts"] == {"accepted_suggestion": len(suggestions)}
        assert manifest["source_run_ids"] == [suggestion_payload["run_id"]]


def test_auto_accept_can_complete_clean_accepted_sentences(tmp_path: Path) -> None:
    storage = AnnotationStorage(
        database_path=tmp_path / "runtime" / "annopilot.sqlite",
        data_root=tmp_path / "projects",
    )
    with TestClient(create_app(storage)) as client:
        labels = seed_pos_span_labels(client)
        response = client.post(
            "/api/projects/default/import-txt",
            files={"file": ("story.txt", "小猫跑。小狗跳。", "text/plain")},
        )
        document_id = response.json()["document_id"]
        document = client.get(f"/api/projects/default/documents/{document_id}").json()
        first_sentence = document["sentences"][0]
        second_sentence = document["sentences"][1]
        tag_id = labels["名词"]["id"]

        def suggestion_values(suggestion_id: str, sentence: dict, token_index: int, confidence: float) -> tuple:
            token = sentence["tokens"][token_index]
            return (
                suggestion_id,
                None,
                sentence["id"],
                tag_id,
                token["token_index"],
                token["token_index"],
                token["start_char"],
                token["end_char"],
                token["text"],
                confidence,
                "test",
                "2026-08-14T00:00:00+00:00",
            )

        with storage.connect() as conn:
            conn.executemany(
                """
                INSERT INTO annotation_suggestions (
                    id, run_id, sentence_id, tag_id, start_token_index, end_token_index,
                    start_char, end_char, text, confidence, source, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    suggestion_values("sg_clean_high", first_sentence, 0, 0.99),
                    suggestion_values("sg_mixed_high", second_sentence, 0, 0.99),
                    suggestion_values("sg_mixed_low", second_sentence, 1, 0.5),
                ],
            )

        auto_accept = client.post(
            f"/api/projects/default/documents/{document_id}/suggestions/auto-accept",
            json={"min_confidence": 0.98, "complete_sentences": True},
        )
        assert auto_accept.status_code == 200
        payload = auto_accept.json()
        assert payload["accepted"] == 2
        assert payload["skipped"] == 0
        assert payload["completed"] == 1
        assert payload["completed_sentence_ids"] == [first_sentence["id"]]

        updated_document = client.get(f"/api/projects/default/documents/{document_id}").json()
        assert updated_document["metrics"]["completed_count"] == 1
        assert updated_document["metrics"]["suggestion_count"] == 1
        assert updated_document["sentences"][0]["completed"] is True
        assert updated_document["sentences"][0]["answer"] == "accept"
        assert updated_document["sentences"][1]["completed"] is False
        assert updated_document["sentences"][1]["answer"] == "pending"
        assert [suggestion["id"] for suggestion in updated_document["sentences"][1]["suggestions"]] == ["sg_mixed_low"]

        prodigy_lines = [
            json.loads(line)
            for line in client.get(f"/api/projects/default/documents/{document_id}/export.prodigy.jsonl").text.splitlines()
        ]
        assert prodigy_lines[0]["meta"]["answer"] == "accept"
        assert prodigy_lines[1]["meta"]["answer"] == "pending"

        events = [json.loads(line) for line in client.get("/api/projects/default/events.jsonl").text.splitlines()]
        completed_event = next(event for event in events if event["type"] == "sentence.completed")
        assert completed_event["source"] == "auto_accept_suggestions"
        assert completed_event["actor_type"] == "system"
        assert completed_event["actor_id"] == "annopilot-character-rag"
        assert completed_event["accepted_suggestion_ids"] == ["sg_clean_high"]
        assert client.get("/api/projects/default/audit").json()["non_replayable_event_count"] == 0


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
        assert sum(payload["source_counts"].values()) == payload["suggestions_created"]
        assert sum(payload["confidence_counts"].values()) == payload["suggestions_created"]
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


def test_llm_rejected_pending_suggestions_survive_rerun_as_negative_examples(tmp_path: Path) -> None:
    storage = AnnotationStorage(
        database_path=tmp_path / "runtime" / "annopilot.sqlite",
        data_root=tmp_path / "projects",
    )
    with TestClient(create_app(storage, suggestion_reviewer=RejectingSuggestionReviewer())) as client:
        tag = client.post(
            "/api/projects/default/tags",
            json={"name": "Engagement Cue", "description": "Potential cue needing human review.", "examples": ["Alpha"]},
        ).json()["tag"]
        imported = client.post(
            "/api/projects/default/import-txt",
            files={"file": ("llm-negative.txt", "Alpha beta.", "text/plain")},
        ).json()
        document_id = imported["document_id"]
        first_run = client.post(
            f"/api/projects/default/documents/{document_id}/suggestions/run",
            json={"limit_per_sentence": 2, "min_confidence": 0.98},
        ).json()
        assert first_run["suggestions_created"] == 1
        suggestion = first_run["suggestions"][0]
        assert suggestion["tag_id"] == tag["id"]

        review_response = client.post(f"/api/projects/default/suggestions/{suggestion['id']}/llm-review")
        assert review_response.status_code == 200
        assert review_response.json()["recommendation"] == "reject"

        second_run = client.post(
            f"/api/projects/default/documents/{document_id}/suggestions/run",
            json={"limit_per_sentence": 2, "min_confidence": 0.98},
        ).json()
        assert second_run["suggestions_created"] == 0
        assert second_run["suggestions"] == []

        document = client.get(f"/api/projects/default/documents/{document_id}").json()
        live_suggestions = [suggestion for sentence in document["sentences"] for suggestion in sentence["suggestions"]]
        assert [item["id"] for item in live_suggestions] == [suggestion["id"]]
        assert live_suggestions[0]["latest_review"]["recommendation"] == "reject"
        assert document["metrics"]["suggestion_count"] == 1
        assert document["metrics"]["reviewed_suggestion_count"] == 1

        runs = client.get(f"/api/projects/default/runs?document_id={document_id}&limit=2").json()["runs"]
        latest_config = runs[0]["config"]
        assert latest_config["pending_suggestion_clear_policy"] == "clear_unreviewed_pending_preserve_llm_reviewed"
        assert latest_config["negative_example_policy"] == "human_rejected_or_latest_llm_reject"
        assert latest_config["negative_example_count"] == 1
        assert latest_config["negative_example_source_counts"] == {"llm_rejected": 1}
        assert latest_config["negative_examples_by_tag"] == {tag["id"]: [suggestion["text"]]}

        generated_event = next(
            event
            for event in [json.loads(line) for line in client.get("/api/projects/default/events.jsonl").text.splitlines()]
            if event["type"] == "suggestions.generated" and event["run_id"] == second_run["run_id"]
        )
        assert generated_event["cleared_pending_suggestion_ids"] == []
        assert generated_event["config"]["negative_example_source_counts"] == {"llm_rejected": 1}


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
        assert suggestion_response.json()["suggestions"]
        queue = client.get(f"/api/projects/default/documents/{document_id}/review-queue?order=goldsmith").json()
        suggestion_id = queue["items"][0]["first_suggestion"]["id"]
        expected_context_sha256 = storage._payload_sha256(storage.get_suggestion_review_context("default", suggestion_id))

        review_response = client.post(f"/api/projects/default/suggestions/{suggestion_id}/llm-review")
        assert review_response.status_code == 200
        review = review_response.json()
        assert review["model"] == "fake-gpt5.5"
        assert review["recommendation"] == "accept"
        assert review["confidence"] == 0.91
        assert review["context_sha256"] == expected_context_sha256
        assert review["judge"]["overall_score"] == 0.92
        assert review["judge"]["boundary_score"] == 0.87
        assert review["judge"]["risk_flags"] == ["borderline_concept"]
        assert len(review["context_sha256"]) == 64

        with storage.connect() as conn:
            stored = conn.execute(
                "SELECT recommendation, rationale, context_sha256, judge_json FROM annotation_suggestion_reviews WHERE suggestion_id = ?",
                (suggestion_id,),
            ).fetchone()
        assert stored["recommendation"] == "accept"
        assert "匹配" in stored["rationale"]
        assert stored["context_sha256"] == expected_context_sha256
        assert json.loads(stored["judge_json"])["concept_fit_score"] == 0.94

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
        assert suggestion["latest_review"]["judge"]["overall_score"] == 0.92
        assert document["metrics"]["accuracy"] is None
        assert document["metrics"]["accuracy_label"] == "Waiting for reviewed accept/reject data"
        assert document["metrics"]["calibration_count"] == 0
        assert document["metrics"]["calibration_disagreement_count"] == 0
        assert document["metrics"]["calibration_error_rate"] is None
        assert document["metrics"]["suggestion_review_counts"] == {"accept": 1, "reject": 0, "uncertain": 0}
        assert document["metrics"]["reviewed_suggestion_count"] == 1

        export_response = client.get(f"/api/projects/default/documents/{document_id}/export.jsonl")
        exported = [json.loads(line) for line in export_response.text.splitlines()]
        exported_suggestions = [suggestion for line in exported for suggestion in line["suggestions"]]
        assert any(item["latest_review"] and item["latest_review"]["recommendation"] == "accept" for item in exported_suggestions)
        assert any(item["latest_review"] and item["latest_review"]["context_sha256"] == expected_context_sha256 for item in exported_suggestions)
        assert any(item["latest_review"] and item["latest_review"]["judge"]["boundary_score"] == 0.87 for item in exported_suggestions)
        assert all("evidence_text" in item for item in exported_suggestions)
        assert all("match_key" in item and item["match_key"] for item in exported_suggestions)
        assert all("evidence_match_key" in item and item["evidence_match_key"] for item in exported_suggestions)
        assert all("context_before" in item and "context_after" in item for item in exported_suggestions)

        review_queue_export = client.get(
            f"/api/projects/default/documents/{document_id}/export.goldsmith.review-queue.jsonl?order=goldsmith"
        )
        assert review_queue_export.status_code == 200
        review_queue_lines = [json.loads(line) for line in review_queue_export.text.splitlines()]
        assert review_queue_lines
        assert review_queue_lines[0]["schema_version"] == "annopilot.goldsmith_review_queue.v1"
        assert review_queue_lines[0]["record_type"] == "human_review_queue_item"
        assert review_queue_lines[0]["queue_order"] == "goldsmith"
        assert review_queue_lines[0]["first_suggestion"]["id"] == suggestion_id
        assert review_queue_lines[0]["first_suggestion"]["latest_review"]["recommendation"] == "accept"
        assert review_queue_lines[0]["first_suggestion"]["latest_review"]["judge"]["overall_score"] == 0.92

        accept_response = client.post(f"/api/projects/default/suggestions/{suggestion_id}/accept")
        assert accept_response.status_code == 200
        reviewed_document = client.get(f"/api/projects/default/documents/{document_id}").json()
        assert reviewed_document["metrics"]["accuracy"] == 1.0
        assert reviewed_document["metrics"]["accuracy_label"] == "LLM review agreement (1/1)"
        assert reviewed_document["metrics"]["calibration_count"] == 1
        assert reviewed_document["metrics"]["calibration_disagreement_count"] == 0
        assert reviewed_document["metrics"]["calibration_error_rate"] == 0.0
        assert reviewed_document["metrics"]["suggestion_review_counts"] == {"accept": 1, "reject": 0, "uncertain": 0}
        assert reviewed_document["metrics"]["reviewed_suggestion_count"] == 1

        choices_export = client.get(f"/api/projects/default/documents/{document_id}/export.goldsmith.human-choices.jsonl")
        assert choices_export.status_code == 200
        choices = [json.loads(line) for line in choices_export.text.splitlines()]
        assert len(choices) == 1
        assert choices[0]["schema_version"] == "annopilot.goldsmith_human_choices.v1"
        assert choices[0]["record_type"] == "human_choice"
        assert choices[0]["suggestion_id"] == suggestion_id
        assert choices[0]["human_decision"] == "accept"
        assert choices[0]["latest_review"]["recommendation"] == "accept"
        assert choices[0]["latest_review"]["judge"]["concept_fit_score"] == 0.94
        assert choices[0]["disagreement"] is False
        assert choices[0]["span"]["text"] == choices[0]["suggestion"]["text"]

        manifest = client.get(f"/api/projects/default/documents/{document_id}/export.manifest.json").json()
        assert manifest["metrics"]["suggestion_review_counts"] == {"accept": 1, "reject": 0, "uncertain": 0}
        assert manifest["metrics"]["reviewed_suggestion_count"] == 1
        assert manifest["metrics"]["calibration_error_rate"] == 0.0
        assert manifest["artifacts"]["goldsmith_review_queue_jsonl"]["schema_version"] == "annopilot.goldsmith_review_queue.v1"
        assert manifest["artifacts"]["goldsmith_human_choices_jsonl"]["schema_version"] == "annopilot.goldsmith_human_choices.v1"
        assert manifest["artifacts"]["goldsmith_human_choices_jsonl"]["line_count"] == 1

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
        assert review_event["judge"]["overall_score"] == 0.92

        rebuild_result = rebuild_project_from_events(
            project_id="default",
            event_path=tmp_path / "projects" / "default" / "events.jsonl",
            database_path=tmp_path / "rebuilt" / "annopilot.sqlite",
            data_root=tmp_path / "rebuilt-projects",
            force=True,
        )
        assert rebuild_result.ok
        rebuilt_storage = AnnotationStorage(
            database_path=tmp_path / "rebuilt" / "annopilot.sqlite",
            data_root=tmp_path / "rebuilt-projects",
        )
        with rebuilt_storage.connect() as conn:
            rebuilt_review = conn.execute(
                "SELECT judge_json FROM annotation_suggestion_reviews WHERE suggestion_id = ?",
                (suggestion_id,),
            ).fetchone()
        assert json.loads(rebuilt_review["judge_json"])["overall_score"] == 0.92


def test_llm_review_calibration_disagreement_is_measured(tmp_path: Path) -> None:
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

        review_response = client.post(f"/api/projects/default/suggestions/{suggestion_id}/llm-review")
        assert review_response.status_code == 200
        assert review_response.json()["recommendation"] == "accept"

        reject_response = client.post(f"/api/projects/default/suggestions/{suggestion_id}/reject")
        assert reject_response.status_code == 200

        document = client.get(f"/api/projects/default/documents/{document_id}").json()
        assert document["metrics"]["accuracy"] == 0.0
        assert document["metrics"]["accuracy_label"] == "LLM review agreement (0/1)"
        assert document["metrics"]["calibration_count"] == 1
        assert document["metrics"]["calibration_disagreement_count"] == 1
        assert document["metrics"]["calibration_error_rate"] == 1.0

        hard_examples_response = client.get(f"/api/projects/default/documents/{document_id}/export.goldsmith.hard-examples.jsonl")
        assert hard_examples_response.status_code == 200
        hard_examples = [json.loads(line) for line in hard_examples_response.text.splitlines()]
        assert len(hard_examples) == 1
        assert hard_examples[0]["schema_version"] == "annopilot.goldsmith_hard_examples.v1"
        assert hard_examples[0]["record_type"] == "hard_example"
        assert hard_examples[0]["suggestion_id"] == suggestion_id
        assert hard_examples[0]["human_decision"] == "reject"
        assert hard_examples[0]["disagreement"] is True
        assert hard_examples[0]["hard_example_reasons"] == ["llm_human_disagreement", "human_rejected_suggestion"]
        assert hard_examples[0]["risk_reason_codes"] == ["judge_risk"]
        assert "negative example" in hard_examples[0]["failure_note"]

        boundary_feedback_response = client.get(f"/api/projects/default/documents/{document_id}/export.goldsmith.boundary-feedback.jsonl")
        assert boundary_feedback_response.status_code == 200
        boundary_feedback = [json.loads(line) for line in boundary_feedback_response.text.splitlines()]
        assert len(boundary_feedback) == 1
        assert boundary_feedback[0]["schema_version"] == "annopilot.goldsmith_boundary_feedback.v1"
        assert boundary_feedback[0]["source_type"] == "human_choice"
        assert boundary_feedback[0]["feedback_polarity"] == "negative"
        assert boundary_feedback[0]["suggestion_id"] == suggestion_id
        assert boundary_feedback[0]["human_decision"] == "reject"
        assert boundary_feedback[0]["latest_review"]["recommendation"] == "accept"
        assert boundary_feedback[0]["hard_example_reasons"] == ["llm_human_disagreement", "human_rejected_suggestion"]
        assert boundary_feedback[0]["risk_reason_codes"] == ["judge_risk"]

        manifest = client.get(f"/api/projects/default/documents/{document_id}/export.manifest.json").json()
        assert manifest["artifacts"]["goldsmith_hard_examples_jsonl"]["schema_version"] == "annopilot.goldsmith_hard_examples.v1"
        assert manifest["artifacts"]["goldsmith_hard_examples_jsonl"]["line_count"] == 1
        assert manifest["artifacts"]["goldsmith_boundary_feedback_jsonl"]["schema_version"] == "annopilot.goldsmith_boundary_feedback.v1"
        assert manifest["artifacts"]["goldsmith_boundary_feedback_jsonl"]["line_count"] == 1


def test_review_efficiency_curves_measure_error_discovery_by_queue_order(tmp_path: Path) -> None:
    storage = AnnotationStorage(
        database_path=tmp_path / "runtime" / "annopilot.sqlite",
        data_root=tmp_path / "projects",
    )
    with TestClient(create_app(storage)) as client:
        tag = client.post("/api/projects/default/tags", json={"name": "Engagement", "examples": ["Alpha"]}).json()["tag"]
        imported = client.post(
            "/api/projects/default/import-txt",
            files={
                "file": (
                    "efficiency.txt",
                    "Alpha one. Bravo two. Charlie three. Delta four. Echo five. Foxtrot six.",
                    "text/plain",
                )
            },
        ).json()
        document_id = imported["document_id"]
        page = client.get(f"/api/projects/default/documents/{document_id}/sentences?offset=0&limit=6").json()
        assert len(page["sentences"]) == 6

        now = storage._now()
        specs = [
            ("sug_efficiency_aaa", 0, 0.98, "accept", "accepted"),
            ("sug_efficiency_bbb", 1, 0.96, "accept", "accepted"),
            ("sug_efficiency_ccc", 2, 0.95, "reject", "rejected"),
            ("sug_efficiency_ddd", 3, 0.70, "accept", "rejected"),
            ("sug_efficiency_eee", 4, 0.75, "reject", "accepted"),
            ("sug_efficiency_fff", 5, 0.80, "accept", "rejected"),
        ]
        with storage.connect() as conn:
            conn.execute(
                """
                INSERT INTO annotation_runs (id, project_id, document_id, recipe, config_json, input_count, suggestion_count, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                ("run_efficiency", "default", document_id, "controlled", "{}", len(specs), len(specs), now),
            )
            for suggestion_id, sentence_offset, confidence, recommendation, status in specs:
                sentence = page["sentences"][sentence_offset]
                token = sentence["tokens"][0]
                conn.execute(
                    """
                    INSERT INTO annotation_suggestions (
                      id, run_id, sentence_id, tag_id, start_token_index, end_token_index,
                      start_char, end_char, text, confidence, source, evidence_text, match_key, evidence_match_key,
                      context_before, context_after, status, created_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        suggestion_id,
                        "run_efficiency",
                        sentence["id"],
                        tag["id"],
                        token["token_index"],
                        token["token_index"],
                        token["start_char"],
                        token["end_char"],
                        token["text"],
                        confidence,
                        "lexical_exact",
                        token["text"],
                        token["text"].casefold(),
                        token["text"].casefold(),
                        "",
                        "",
                        status,
                        now,
                    ),
                )
                conn.execute(
                    """
                    INSERT INTO annotation_suggestion_reviews (
                      id, suggestion_id, model, recommendation, confidence, rationale, context_sha256, created_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        f"rev_{suggestion_id}",
                        suggestion_id,
                        "fake-gpt5.5",
                        recommendation,
                        0.91,
                        "controlled calibration fixture",
                        hashlib.sha256(suggestion_id.encode("utf-8")).hexdigest(),
                        now,
                    ),
                )

        metrics = client.get(f"/api/projects/default/documents/{document_id}/summary").json()["metrics"]
        assert metrics["calibration_count"] == 6
        assert metrics["calibration_disagreement_count"] == 3
        assert metrics["calibration_error_rate"] == 0.5

        curves = metrics["review_efficiency_curves"]
        assert set(curves) == {"position", "random", "uncertain", "goldsmith", "hybrid"}
        assert curves["random"]["first_disagreement_rank"] == 4
        assert curves["random"]["early_disagreement_count"] == 2
        assert curves["goldsmith"]["first_disagreement_rank"] == 1
        assert curves["goldsmith"]["early_disagreement_count"] == 3
        assert curves["goldsmith"]["reason_counts"] == {"low_confidence": 1}
        assert curves["goldsmith"]["disagreement_reason_counts"] == {"low_confidence": 1}
        assert curves["hybrid"]["early_disagreement_count"] == 3
        assert [point["suggestion_id"] for point in curves["goldsmith"]["points"][:3]] == [
            "sug_efficiency_ddd",
            "sug_efficiency_eee",
            "sug_efficiency_fff",
        ]
        assert curves["goldsmith"]["points"][0]["risk_reason_codes"] == ["low_confidence"]
        assert curves["goldsmith"]["points"][1]["cumulative_disagreements"] == 2

        risk_reasons_response = client.get(f"/api/projects/default/documents/{document_id}/export.goldsmith.risk-reasons.jsonl")
        assert risk_reasons_response.status_code == 200
        assert risk_reasons_response.headers["content-disposition"] == f'attachment; filename="{document_id}.goldsmith.risk-reasons.jsonl"'
        risk_reasons = [json.loads(line) for line in risk_reasons_response.text.splitlines()]
        assert risk_reasons[0]["schema_version"] == "annopilot.goldsmith_risk_reasons.v1"
        assert risk_reasons[0]["record_type"] == "risk_reason_summary"
        assert risk_reasons[0]["reason_code"] == "low_confidence"
        assert risk_reasons[0]["calibrated_count"] == 1
        assert risk_reasons[0]["disagreement_count"] == 1
        assert risk_reasons[0]["total_count"] >= 1
        assert risk_reasons[0]["meta"]["curve_order"] == "goldsmith"

        manifest = client.get(f"/api/projects/default/documents/{document_id}/export.manifest.json").json()
        assert manifest["metrics"]["review_efficiency_curves"]["goldsmith"]["early_disagreement_count"] == 3
        assert manifest["metrics"]["review_efficiency_curves"]["goldsmith"]["disagreement_reason_counts"] == {"low_confidence": 1}
        assert manifest["artifacts"]["goldsmith_risk_reasons_jsonl"]["schema_version"] == "annopilot.goldsmith_risk_reasons.v1"
        assert manifest["artifacts"]["goldsmith_risk_reasons_jsonl"]["filename"] == f"{document_id}.goldsmith.risk-reasons.jsonl"
        assert manifest["artifacts"]["goldsmith_risk_reasons_jsonl"]["line_count"] == len(risk_reasons)


def test_review_efficiency_goldsmith_uses_candidate_disagreement_risk(tmp_path: Path) -> None:
    storage = AnnotationStorage(
        database_path=tmp_path / "runtime" / "annopilot.sqlite",
        data_root=tmp_path / "projects",
    )
    with TestClient(create_app(storage)) as client:
        first_tag = client.post("/api/projects/default/tags", json={"name": "Entertain"}).json()["tag"]
        second_tag = client.post("/api/projects/default/tags", json={"name": "Disclaim"}).json()["tag"]
        imported = client.post(
            "/api/projects/default/import-txt",
            files={"file": ("curve-conflict.txt", "Alpha beta. Gamma delta.", "text/plain")},
        ).json()
        document_id = imported["document_id"]
        page = client.get(f"/api/projects/default/documents/{document_id}/sentences?offset=0&limit=2").json()
        first_sentence = page["sentences"][0]
        second_sentence = page["sentences"][1]
        now = storage._now()

        with storage.connect() as conn:
            conn.execute(
                """
                INSERT INTO annotation_runs (id, project_id, document_id, recipe, config_json, input_count, suggestion_count, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                ("run_curve_conflict", "default", document_id, "controlled", "{}", 3, 3, now),
            )

            def insert_reviewed_suggestion(
                suggestion_id: str,
                sentence: dict,
                tag_id: str,
                start_offset: int,
                end_offset: int,
                confidence: float,
                recommendation: str,
                status: str,
            ) -> None:
                start_token = sentence["tokens"][start_offset]
                end_token = sentence["tokens"][end_offset]
                text = sentence["text"][start_token["start_char"] - sentence["start_char"] : end_token["end_char"] - sentence["start_char"]]
                conn.execute(
                    """
                    INSERT INTO annotation_suggestions (
                      id, run_id, sentence_id, tag_id, start_token_index, end_token_index,
                      start_char, end_char, text, confidence, source, evidence_text, match_key, evidence_match_key,
                      context_before, context_after, status, created_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        suggestion_id,
                        "run_curve_conflict",
                        sentence["id"],
                        tag_id,
                        start_token["token_index"],
                        end_token["token_index"],
                        start_token["start_char"],
                        end_token["end_char"],
                        text,
                        confidence,
                        "lexical_exact",
                        text,
                        text.casefold(),
                        text.casefold(),
                        "",
                        "",
                        status,
                        now,
                    ),
                )
                conn.execute(
                    """
                    INSERT INTO annotation_suggestion_reviews (
                      id, suggestion_id, model, recommendation, confidence, rationale, context_sha256, created_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        f"rev_{suggestion_id}",
                        suggestion_id,
                        "fake-gpt5.5",
                        recommendation,
                        0.91,
                        "controlled candidate disagreement fixture",
                        hashlib.sha256(suggestion_id.encode("utf-8")).hexdigest(),
                        now,
                    ),
                )

            insert_reviewed_suggestion("sug_curve_conflict_aaa", first_sentence, first_tag["id"], 0, 0, 0.98, "accept", "rejected")
            insert_reviewed_suggestion("sug_curve_conflict_zzz", first_sentence, second_tag["id"], 0, 1, 0.98, "accept", "accepted")
            insert_reviewed_suggestion("sug_curve_lexical_low", second_sentence, first_tag["id"], 0, 0, 0.70, "accept", "rejected")

        curves = client.get(f"/api/projects/default/documents/{document_id}/summary").json()["metrics"]["review_efficiency_curves"]
        assert curves["goldsmith"]["points"][0]["suggestion_id"] == "sug_curve_conflict_aaa"
        assert curves["goldsmith"]["first_disagreement_rank"] == 1
        assert curves["position"]["points"][0]["suggestion_id"] == "sug_curve_conflict_aaa"
        assert curves["uncertain"]["points"][0]["suggestion_id"] == "sug_curve_lexical_low"


def test_review_efficiency_goldsmith_uses_judge_review_risk(tmp_path: Path) -> None:
    storage = AnnotationStorage(
        database_path=tmp_path / "runtime" / "annopilot.sqlite",
        data_root=tmp_path / "projects",
    )
    with TestClient(create_app(storage)) as client:
        tag = client.post("/api/projects/default/tags", json={"name": "Engagement"}).json()["tag"]
        imported = client.post(
            "/api/projects/default/import-txt",
            files={"file": ("curve-judge-risk.txt", "Stable cue. Baseline cue.", "text/plain")},
        ).json()
        document_id = imported["document_id"]
        page = client.get(f"/api/projects/default/documents/{document_id}/sentences?offset=0&limit=2").json()
        first_sentence = page["sentences"][0]
        second_sentence = page["sentences"][1]
        now = storage._now()

        with storage.connect() as conn:
            def insert_reviewed_suggestion(
                suggestion_id: str,
                sentence: dict,
                confidence: float,
                recommendation: str,
                status: str,
                judge=None,
            ) -> None:
                token = sentence["tokens"][0]
                conn.execute(
                    """
                    INSERT INTO annotation_suggestions (
                      id, sentence_id, tag_id, start_token_index, end_token_index,
                      start_char, end_char, text, confidence, source, status, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        suggestion_id,
                        sentence["id"],
                        tag["id"],
                        token["token_index"],
                        token["token_index"],
                        token["start_char"],
                        token["end_char"],
                        token["text"],
                        confidence,
                        "lexical_exact",
                        status,
                        now,
                    ),
                )
                conn.execute(
                    """
                    INSERT INTO annotation_suggestion_reviews (
                      id, suggestion_id, model, recommendation, confidence, rationale, context_sha256, judge_json, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        f"rev_{suggestion_id}",
                        suggestion_id,
                        "fake-gpt5.5",
                        recommendation,
                        0.91,
                        "controlled judge risk fixture",
                        hashlib.sha256(suggestion_id.encode("utf-8")).hexdigest(),
                        json.dumps(judge, ensure_ascii=False) if judge else None,
                        now,
                    ),
                )

            insert_reviewed_suggestion(
                "sug_curve_judge_risk",
                first_sentence,
                0.99,
                "accept",
                "rejected",
                {
                    "boundary_score": 0.2,
                    "missed_span_risk": 0.82,
                    "overall_score": 0.88,
                    "needs_review": True,
                    "error_types": ["boundary_too_wide"],
                    "risk_flags": ["possible_under_annotation"],
                },
            )
            insert_reviewed_suggestion("sug_curve_low_confidence", second_sentence, 0.6, "accept", "accepted")

        curves = client.get(f"/api/projects/default/documents/{document_id}/summary").json()["metrics"]["review_efficiency_curves"]
        assert curves["goldsmith"]["points"][0]["suggestion_id"] == "sug_curve_judge_risk"
        assert curves["goldsmith"]["points"][0]["risk_reason_codes"] == [
            "judge_needs_review",
            "judge_boundary",
            "judge_missing_span",
        ]
        assert curves["goldsmith"]["disagreement_reason_counts"] == {
            "judge_boundary": 1,
            "judge_missing_span": 1,
            "judge_needs_review": 1,
        }
        assert curves["goldsmith"]["first_disagreement_rank"] == 1
        assert curves["uncertain"]["points"][0]["suggestion_id"] == "sug_curve_low_confidence"


def test_sentence_llm_review_suggestions_reviews_current_queue(tmp_path: Path) -> None:
    storage = AnnotationStorage(
        database_path=tmp_path / "runtime" / "annopilot.sqlite",
        data_root=tmp_path / "projects",
    )
    with TestClient(create_app(storage, suggestion_reviewer=FakeSuggestionReviewer())) as client:
        seed_pos_span_labels(client)
        response = client.post(
            "/api/projects/default/import-txt",
            files={"file": ("story.txt", "清晨，小猫看见金色的叶子。男孩走来。", "text/plain")},
        )
        document_id = response.json()["document_id"]
        page = client.get(f"/api/projects/default/documents/{document_id}/sentences?offset=0&limit=2").json()
        first_sentence_id = page["sentences"][0]["id"]

        suggestion_response = client.post(
            f"/api/projects/default/documents/{document_id}/sentences/{first_sentence_id}/suggestions/run",
            json={"limit_per_sentence": 3, "min_confidence": 0.98},
        )
        suggestions = suggestion_response.json()["suggestions"]
        assert len(suggestions) >= 1

        review_response = client.post(f"/api/projects/default/sentences/{first_sentence_id}/suggestions/llm-review")
        assert review_response.status_code == 200
        reviewed = review_response.json()
        assert reviewed["reviewed"] == len(suggestions)
        assert reviewed["reviewed_suggestion_ids"] == [suggestion["id"] for suggestion in suggestions]
        assert all(review["recommendation"] == "accept" for review in reviewed["reviews"])
        assert all(review["context_sha256"] for review in reviewed["reviews"])

        document = client.get(f"/api/projects/default/documents/{document_id}").json()
        reviewed_suggestions = [suggestion for sentence in document["sentences"] for suggestion in sentence["suggestions"] if suggestion["latest_review"]]
        assert len(reviewed_suggestions) == len(suggestions)
        assert document["metrics"]["suggestion_review_counts"] == {
            "accept": len(suggestions),
            "reject": 0,
            "uncertain": 0,
        }
        assert document["metrics"]["reviewed_suggestion_count"] == len(suggestions)

        manifest = client.get(f"/api/projects/default/documents/{document_id}/export.manifest.json").json()
        assert manifest["metrics"]["suggestion_review_counts"]["accept"] == len(suggestions)
        assert manifest["metrics"]["reviewed_suggestion_count"] == len(suggestions)

        events = [json.loads(line) for line in client.get("/api/projects/default/events.jsonl").text.splitlines()]
        review_events = [event for event in events if event["type"] == "suggestion.llm_reviewed"]
        assert len(review_events) == len(suggestions)
        assert {event["suggestion_id"] for event in review_events} == {suggestion["id"] for suggestion in suggestions}
        assert all(event["actor_type"] == "llm" for event in review_events)


def test_accept_suggestion_rolls_back_when_event_enqueue_fails(tmp_path: Path) -> None:
    storage = AnnotationStorage(
        database_path=tmp_path / "runtime" / "annopilot.sqlite",
        data_root=tmp_path / "projects",
    )
    with TestClient(create_app(storage)) as client:
        seed_pos_span_labels(client)
        response = client.post(
            "/api/projects/default/import-txt",
            files={"file": ("story.txt", "清晨，小猫看见金色的叶子。", "text/plain")},
        )
        document_id = response.json()["document_id"]
        suggestions = client.post(
            f"/api/projects/default/documents/{document_id}/suggestions/run",
            json={"limit_per_sentence": 2, "min_confidence": 0.98},
        ).json()["suggestions"]
        suggestion_id = suggestions[0]["id"]

        with storage.connect() as conn:
            before_annotations = conn.execute("SELECT COUNT(*) AS count FROM annotations").fetchone()["count"]
            before_events = conn.execute("SELECT COUNT(*) AS count FROM event_outbox").fetchone()["count"]

        original_enqueue = storage.suggestion_decisions.enqueue_event

        def fail_on_accept_event(conn, project_id, payload):
            if payload.get("type") == "suggestion.accepted":
                raise RuntimeError("forced outbox failure")
            return original_enqueue(conn, project_id, payload)

        storage.suggestion_decisions.enqueue_event = fail_on_accept_event
        try:
            with pytest.raises(RuntimeError, match="forced outbox failure"):
                storage.accept_suggestion("default", suggestion_id)
        finally:
            storage.suggestion_decisions.enqueue_event = original_enqueue

        with storage.connect() as conn:
            annotation_count = conn.execute("SELECT COUNT(*) AS count FROM annotations").fetchone()["count"]
            event_count = conn.execute("SELECT COUNT(*) AS count FROM event_outbox").fetchone()["count"]
            suggestion = conn.execute(
                "SELECT status FROM annotation_suggestions WHERE id = ?",
                (suggestion_id,),
            ).fetchone()

        assert annotation_count == before_annotations
        assert event_count == before_events
        assert suggestion["status"] == "pending"
        events = [json.loads(line) for line in client.get("/api/projects/default/events.jsonl").text.splitlines()]
        assert not any(event.get("suggestion_id") == suggestion_id and event["type"] == "suggestion.accepted" for event in events)
        assert not any(event.get("source_suggestion_id") == suggestion_id and event["type"] == "annotation.created" for event in events)


def test_apply_sentence_llm_review_recommendations_updates_suggestions(tmp_path: Path) -> None:
    storage = AnnotationStorage(
        database_path=tmp_path / "runtime" / "annopilot.sqlite",
        data_root=tmp_path / "projects",
    )
    with TestClient(create_app(storage, suggestion_reviewer=CyclingSuggestionReviewer())) as client:
        seed_pos_span_labels(client)
        response = client.post(
            "/api/projects/default/import-txt",
            files={"file": ("story.txt", "清晨，小猫看见金色的叶子。", "text/plain")},
        )
        document_id = response.json()["document_id"]
        page = client.get(f"/api/projects/default/documents/{document_id}/sentences?offset=0&limit=1").json()
        sentence_id = page["sentences"][0]["id"]

        suggestion_payload = client.post(
            f"/api/projects/default/documents/{document_id}/sentences/{sentence_id}/suggestions/run",
            json={"limit_per_sentence": 4, "min_confidence": 0.98},
        ).json()
        suggestions = suggestion_payload["suggestions"]
        assert len(suggestions) >= 3

        reviewed = client.post(f"/api/projects/default/sentences/{sentence_id}/suggestions/llm-review").json()
        recommendations = {review["suggestion_id"]: review["recommendation"] for review in reviewed["reviews"]}
        expected_accept_ids = [suggestion["id"] for suggestion in suggestions if recommendations[suggestion["id"]] == "accept"]
        expected_reject_ids = [suggestion["id"] for suggestion in suggestions if recommendations[suggestion["id"]] == "reject"]
        expected_kept_ids = [suggestion["id"] for suggestion in suggestions if recommendations[suggestion["id"]] == "uncertain"]
        assert expected_accept_ids
        assert expected_reject_ids
        assert expected_kept_ids

        apply_response = client.post(f"/api/projects/default/sentences/{sentence_id}/suggestions/apply-llm-review")
        assert apply_response.status_code == 200
        applied = apply_response.json()
        assert applied["accepted"] == len(expected_accept_ids)
        assert applied["rejected"] == len(expected_reject_ids)
        assert applied["skipped"] == 0
        assert applied["kept"] == len(expected_kept_ids)
        assert applied["accepted_suggestion_ids"] == expected_accept_ids
        assert applied["rejected_suggestion_ids"] == expected_reject_ids
        assert applied["affected_sentence_ids"] == [sentence_id]
        assert len(applied["annotations"]) == len(expected_accept_ids)

        document = client.get(f"/api/projects/default/documents/{document_id}").json()
        assert document["metrics"]["suggestion_count"] == len(expected_kept_ids)
        assert document["metrics"]["suggestion_status_counts"] == {
            "pending": len(expected_kept_ids),
            "accepted": len(expected_accept_ids),
            "rejected": len(expected_reject_ids),
        }
        assert document["metrics"]["suggestion_review_counts"] == {
            "accept": len(expected_accept_ids),
            "reject": len(expected_reject_ids),
            "uncertain": len(expected_kept_ids),
        }
        pending_ids = {suggestion["id"] for sentence in document["sentences"] for suggestion in sentence["suggestions"]}
        assert pending_ids == set(expected_kept_ids)

        events = [json.loads(line) for line in client.get("/api/projects/default/events.jsonl").text.splitlines()]
        assert sum(1 for event in events if event["type"] == "annotation.created") == len(expected_accept_ids)
        assert {event["suggestion_id"] for event in events if event["type"] == "suggestion.accepted"} == set(expected_accept_ids)
        assert {event["suggestion_id"] for event in events if event["type"] == "suggestion.rejected"} == set(expected_reject_ids)
        assert client.get("/api/projects/default/audit").json()["non_replayable_event_count"] == 0


def test_apply_document_llm_review_recommendations_updates_all_reviewed_suggestions(tmp_path: Path) -> None:
    storage = AnnotationStorage(
        database_path=tmp_path / "runtime" / "annopilot.sqlite",
        data_root=tmp_path / "projects",
    )
    with TestClient(create_app(storage, suggestion_reviewer=CyclingSuggestionReviewer())) as client:
        seed_pos_span_labels(client)
        response = client.post(
            "/api/projects/default/import-txt",
            files={"file": ("story.txt", "清晨，小猫看见金色的叶子。男孩走来，轻轻拾起叶子。", "text/plain")},
        )
        document_id = response.json()["document_id"]

        suggestion_payload = client.post(
            f"/api/projects/default/documents/{document_id}/suggestions/run",
            json={"limit_per_sentence": 4, "min_confidence": 0.98},
        ).json()
        suggestions = suggestion_payload["suggestions"]
        assert len(suggestions) >= 3

        reviews = []
        for sentence_id in sorted({suggestion["sentence_id"] for suggestion in suggestions}):
            review_response = client.post(f"/api/projects/default/sentences/{sentence_id}/suggestions/llm-review")
            assert review_response.status_code == 200
            reviews.extend(review_response.json()["reviews"])
        recommendations = {review["suggestion_id"]: review["recommendation"] for review in reviews}
        expected_accept_ids = [suggestion["id"] for suggestion in suggestions if recommendations[suggestion["id"]] == "accept"]
        expected_reject_ids = [suggestion["id"] for suggestion in suggestions if recommendations[suggestion["id"]] == "reject"]
        expected_kept_ids = [suggestion["id"] for suggestion in suggestions if recommendations[suggestion["id"]] == "uncertain"]
        assert expected_accept_ids
        assert expected_reject_ids
        assert expected_kept_ids

        apply_response = client.post(f"/api/projects/default/documents/{document_id}/suggestions/apply-llm-review")
        assert apply_response.status_code == 200
        applied = apply_response.json()
        assert applied["accepted"] == len(expected_accept_ids)
        assert applied["rejected"] == len(expected_reject_ids)
        assert applied["skipped"] == 0
        assert applied["kept"] == len(expected_kept_ids)
        assert applied["accepted_suggestion_ids"] == expected_accept_ids
        assert applied["rejected_suggestion_ids"] == expected_reject_ids
        assert set(applied["affected_sentence_ids"]) == {
            suggestion["sentence_id"] for suggestion in suggestions if suggestion["id"] in set(expected_accept_ids + expected_reject_ids)
        }

        document = client.get(f"/api/projects/default/documents/{document_id}").json()
        assert document["metrics"]["suggestion_status_counts"] == {
            "pending": len(expected_kept_ids),
            "accepted": len(expected_accept_ids),
            "rejected": len(expected_reject_ids),
        }
        assert document["metrics"]["suggestion_count"] == len(expected_kept_ids)
        pending_ids = {suggestion["id"] for sentence in document["sentences"] for suggestion in sentence["suggestions"]}
        assert pending_ids == set(expected_kept_ids)

        events = [json.loads(line) for line in client.get("/api/projects/default/events.jsonl").text.splitlines()]
        assert {event["suggestion_id"] for event in events if event["type"] == "suggestion.accepted"} == set(expected_accept_ids)
        assert {event["suggestion_id"] for event in events if event["type"] == "suggestion.rejected"} == set(expected_reject_ids)
        assert sum(1 for event in events if event["type"] == "annotation.created") == len(expected_accept_ids)
        assert client.get("/api/projects/default/audit").json()["non_replayable_event_count"] == 0


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
    assert any(
        suggestion["match_key"] and suggestion["evidence_match_key"]
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


def test_project_reset_clears_runtime_data_and_replays_cleanly(tmp_path: Path) -> None:
    storage = AnnotationStorage(
        database_path=tmp_path / "runtime" / "annopilot.sqlite",
        data_root=tmp_path / "projects",
    )
    with TestClient(create_app(storage, suggestion_reviewer=FakeSuggestionReviewer())) as client:
        tag = client.post("/api/projects/default/tags", json={"name": "角色", "examples": ["小猫"]}).json()["tag"]
        response = client.post(
            "/api/projects/default/import-txt",
            files={"file": ("story.txt", "清晨，小猫看见金色的叶子。小猫坐在桥边。", "text/plain")},
        )
        assert response.status_code == 200
        document_id = response.json()["document_id"]
        document = client.get(f"/api/projects/default/documents/{document_id}").json()
        first_sentence = document["sentences"][0]
        assert client.post(
            f"/api/projects/default/sentences/{first_sentence['id']}/annotations",
            json={"tag_id": tag["id"], "start_token_index": 1, "end_token_index": 2},
        ).status_code == 200
        assert client.post(f"/api/projects/default/documents/{document_id}/suggestions/run").status_code == 200
        first_suggestion = client.get(f"/api/projects/default/documents/{document_id}").json()["sentences"][0]["suggestions"][0]
        assert client.post(f"/api/projects/default/suggestions/{first_suggestion['id']}/llm-review").status_code == 200
        assert client.post(
            f"/api/projects/default/documents/{document_id}/session/cursor",
            json={"current_sentence_index": 1},
        ).status_code == 200

        reset_response = client.post("/api/projects/default/reset")
        assert reset_response.status_code == 200
        reset_payload = reset_response.json()
        assert reset_payload["deleted_documents"] == 1
        assert reset_payload["deleted_sentences"] == 2
        assert reset_payload["deleted_annotations"] == 1
        assert reset_payload["deleted_suggestions"] >= 1
        assert reset_payload["deleted_suggestion_reviews"] == 1
        assert reset_payload["deleted_runs"] == 1
        assert reset_payload["deleted_sessions"] == 1

        assert client.get("/api/projects/default/documents").json()["documents"] == []
        assert client.get(f"/api/projects/default/documents/{document_id}/summary").status_code == 404
        assert client.get("/api/projects/default/tags").json()["tags"] == [
            {**tag, "count": 0, "usage_count": 0, "suggestion_count": 0}
        ]
        audit = client.get("/api/projects/default/audit").json()
        assert audit["event_types"]["project.reset"] == 1
        assert audit["non_replayable_event_count"] == 0
        assert audit["rebuild_status"] == "ready"
        preview = client.post("/api/projects/default/rebuild/preview").json()
        assert preview["ok"] is True
        assert preview["documents"] == 0
        assert preview["annotations"] == 0
        assert preview["suggestions"] == 0
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
    assert rebuild_result.documents == 0
    assert rebuild_result.annotations == 0
    assert rebuild_result.suggestions == 0
    assert rebuild_result.runs == 0
    assert rebuild_result.tags == 1


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
