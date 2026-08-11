from pathlib import Path

from backend.app.storage import AnnotationStorage


def make_storage(tmp_path: Path) -> AnnotationStorage:
    storage = AnnotationStorage(
        database_path=tmp_path / "runtime" / "annopilot.sqlite",
        data_root=tmp_path / "projects",
    )
    storage.initialize()
    return storage


def test_document_query_repository_reads_document_workspace_state(tmp_path: Path) -> None:
    storage = make_storage(tmp_path)
    imported = storage.import_txt("default", "repo-sample.txt", "第一句。第二句。第三句。".encode("utf-8"))
    document_id = imported["document_id"]
    tag = storage.create_tag("default", "实体", "测试标签")

    first_page = storage.document_queries.get_document_sentences("default", document_id, offset=0, limit=1)
    first_sentence = first_page["sentences"][0]
    storage.create_annotation("default", first_sentence["id"], tag["id"], 0, 0)
    storage.set_sentence_completed("default", first_sentence["id"], True, "accept")
    storage.set_session_cursor("default", document_id, 1)

    summary = storage.document_queries.get_document_summary("default", document_id)
    assert summary["document"]["sentence_count"] == 3
    assert summary["metrics"]["completed_count"] == 1
    assert summary["metrics"]["annotation_count"] == 1
    assert summary["metrics"]["progress"] == 1 / 3
    assert summary["queue"][0]["completed"] is True
    assert summary["queue"][0]["answer"] == "accept"
    assert summary["session"]["current_sentence_index"] == 1
    assert next(item for item in summary["tags"] if item["id"] == tag["id"])["count"] == 1

    second_page = storage.document_queries.get_document_sentences("default", document_id, offset=1, limit=1)
    assert second_page["offset"] == 1
    assert second_page["limit"] == 1
    assert second_page["total"] == 3
    assert second_page["has_more"] is True
    assert second_page["sentences"][0]["index"] == 1
    assert second_page["sentences"][0]["tokens"]

    documents = storage.document_queries.list_documents("default", limit=10)["documents"]
    assert documents[0]["id"] == document_id
    assert documents[0]["current_sentence_index"] == 1
    assert documents[0]["completed_count"] == 1
