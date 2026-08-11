from pathlib import Path

from backend.app.storage import AnnotationStorage


def make_storage(tmp_path: Path) -> AnnotationStorage:
    storage = AnnotationStorage(
        database_path=tmp_path / "runtime" / "annopilot.sqlite",
        data_root=tmp_path / "projects",
    )
    storage.initialize()
    return storage


def test_tag_query_repository_reads_tag_usage_and_examples(tmp_path: Path) -> None:
    storage = make_storage(tmp_path)
    place_tag = storage.create_tag("default", "地点", "空间位置", ["桥边", "桥边", "小河"])
    action_tag = storage.create_tag("default", "动作")
    imported = storage.import_txt("default", "tag-repo-sample.txt", "小猫来到桥边。它看见小河。".encode("utf-8"))

    page = storage.document_queries.get_document_sentences("default", imported["document_id"], offset=0, limit=1)
    first_sentence = page["sentences"][0]
    storage.create_annotation("default", first_sentence["id"], place_tag["id"], 0, 0)

    with storage.connect() as conn:
        tags = storage.tag_queries.list_tags(conn, "default")

    assert [tag["id"] for tag in tags] == [place_tag["id"], action_tag["id"]]
    assert tags[0]["name"] == "地点"
    assert tags[0]["description"] == "空间位置"
    assert tags[0]["examples"] == ["桥边", "小河"]
    assert tags[0]["usage_count"] == 1
    assert tags[0]["count"] == 1
    assert tags[1]["usage_count"] == 0
    assert tags[1]["count"] == 0
