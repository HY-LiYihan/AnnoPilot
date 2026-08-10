from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .storage import AnnotationStorage


@dataclass
class RebuildIssue:
    line_number: int
    event_id: str | None
    event_type: str | None
    message: str


@dataclass
class RebuildResult:
    project_id: str
    event_count: int = 0
    documents: int = 0
    sentences: int = 0
    tokens: int = 0
    tags: int = 0
    annotations: int = 0
    suggestions: int = 0
    suggestion_reviews: int = 0
    runs: int = 0
    issues: list[RebuildIssue] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.issues

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["ok"] = self.ok
        return payload


def rebuild_project_from_events(
    *,
    project_id: str,
    event_path: Path,
    database_path: Path,
    data_root: Path,
    force: bool = False,
) -> RebuildResult:
    if not event_path.exists():
        raise FileNotFoundError(f"Event log does not exist: {event_path}")
    if database_path.exists() and not force:
        raise FileExistsError(f"Refusing to overwrite existing database: {database_path}")

    _remove_database_files(database_path)
    storage = AnnotationStorage(database_path=database_path, data_root=data_root)
    storage.initialize()

    result = RebuildResult(project_id=project_id)
    with storage.connect() as conn:
        conn.execute("BEGIN")
        for line_number, line in enumerate(event_path.read_text(encoding="utf-8").splitlines(), start=1):
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError as exc:
                result.issues.append(RebuildIssue(line_number, None, None, f"invalid_json: {exc.msg}"))
                continue

            if event.get("project_id") != project_id:
                continue

            result.event_count += 1
            replay_issue = storage._event_replay_issue(event)
            if replay_issue:
                result.issues.append(
                    RebuildIssue(line_number, event.get("event_id"), event.get("type"), replay_issue)
                )
                continue

            try:
                _apply_event(conn, project_id, event)
            except Exception as exc:  # pragma: no cover - defensive context for corrupted logs.
                result.issues.append(
                    RebuildIssue(line_number, event.get("event_id"), event.get("type"), f"apply_failed: {exc}")
                )
        conn.commit()

    _populate_counts(storage, result)
    return result


def _apply_event(conn, project_id: str, event: dict[str, Any]) -> None:
    event_type = event.get("type")
    if event_type == "project.reset":
        AnnotationStorage._clear_project_runtime_rows(conn, project_id)
    elif event_type == "document.imported":
        _apply_document_import(conn, project_id, event)
    elif event_type == "annotations.imported":
        return
    elif event_type == "tag.created":
        conn.execute(
            """
            INSERT OR REPLACE INTO tags (id, project_id, name, description, examples_json, shortcut, color)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event["tag_id"],
                project_id,
                event["name"],
                event.get("description"),
                json.dumps(event.get("examples", []), ensure_ascii=False),
                event["shortcut"],
                event["color"],
            ),
        )
    elif event_type == "tag.renamed":
        conn.execute(
            "UPDATE tags SET name = ? WHERE project_id = ? AND id = ?",
            (event["name"], project_id, event["tag_id"]),
        )
    elif event_type == "tag.updated":
        assignments = []
        values = []
        if "name" in event:
            assignments.append("name = ?")
            values.append(event["name"])
        if "description" in event:
            assignments.append("description = ?")
            values.append(event.get("description"))
        if "examples" in event:
            assignments.append("examples_json = ?")
            values.append(json.dumps(event.get("examples", []), ensure_ascii=False))
        if "shortcut" in event:
            assignments.append("shortcut = ?")
            values.append(event["shortcut"])
        if "color" in event:
            assignments.append("color = ?")
            values.append(event["color"])
        if assignments:
            conn.execute(
                f"UPDATE tags SET {', '.join(assignments)} WHERE project_id = ? AND id = ?",
                (*values, project_id, event["tag_id"]),
            )
    elif event_type == "tag.deleted":
        _apply_tag_delete(conn, project_id, event["tag_id"])
    elif event_type == "annotation.created":
        conn.execute(
            """
            INSERT OR REPLACE INTO annotations (
              id, sentence_id, tag_id, start_token_index, end_token_index,
              start_char, end_char, text, source, source_suggestion_id, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event["annotation_id"],
                event["sentence_id"],
                event["tag_id"],
                event["start_token_index"],
                event["end_token_index"],
                event["start_char"],
                event["end_char"],
                event["text"],
                event.get("source", "human"),
                event.get("source_suggestion_id"),
                event.get("created_at") or event.get("ts"),
            ),
        )
    elif event_type == "annotation.deleted":
        conn.execute("DELETE FROM annotations WHERE id = ?", (event["annotation_id"],))
    elif event_type == "sentence.completed":
        answer = event.get("answer") or ("accept" if event["completed"] else "pending")
        conn.execute(
            "UPDATE sentences SET completed = ?, answer = ? WHERE id = ?",
            (int(event["completed"]), answer, event["sentence_id"]),
        )
    elif event_type == "suggestions.generated":
        _apply_suggestions_generated(conn, project_id, event)
    elif event_type == "suggestion.accepted":
        conn.execute("UPDATE annotation_suggestions SET status = 'accepted' WHERE id = ?", (event["suggestion_id"],))
    elif event_type == "suggestion.rejected":
        conn.execute("UPDATE annotation_suggestions SET status = 'rejected' WHERE id = ?", (event["suggestion_id"],))
    elif event_type == "suggestion.llm_reviewed":
        conn.execute(
            """
            INSERT OR REPLACE INTO annotation_suggestion_reviews (
              id, suggestion_id, model, recommendation, confidence, rationale, context_sha256, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event["review_id"],
                event["suggestion_id"],
                event["model"],
                event["recommendation"],
                event["confidence"],
                event["rationale"],
                event.get("context_sha256"),
                event.get("created_at") or event.get("ts"),
            ),
        )


def _apply_document_import(conn, project_id: str, event: dict[str, Any]) -> None:
    conn.execute(
        """
        INSERT OR REPLACE INTO documents (id, project_id, filename, text, created_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (event["document_id"], project_id, event["filename"], event["text"], event.get("created_at") or event.get("ts")),
    )
    for sentence in event["sentences"]:
        conn.execute(
            """
        INSERT OR REPLACE INTO sentences (id, document_id, sentence_index, text, start_char, end_char, completed, answer)
            VALUES (?, ?, ?, ?, ?, ?, COALESCE((SELECT completed FROM sentences WHERE id = ?), 0), COALESCE((SELECT answer FROM sentences WHERE id = ?), 'pending'))
            """,
            (
                sentence["id"],
                event["document_id"],
                sentence["sentence_index"],
                sentence["text"],
                sentence["start_char"],
                sentence["end_char"],
                sentence["id"],
                sentence["id"],
            ),
        )
        for token in sentence["tokens"]:
            conn.execute(
                """
                INSERT OR REPLACE INTO tokens (id, sentence_id, token_index, text, start_char, end_char)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (token["id"], sentence["id"], token["token_index"], token["text"], token["start_char"], token["end_char"]),
            )


def _apply_tag_delete(conn, project_id: str, tag_id: str) -> None:
    conn.execute(
        """
        DELETE FROM annotations
        WHERE tag_id = ?
          AND sentence_id IN (
            SELECT s.id FROM sentences s JOIN documents d ON d.id = s.document_id WHERE d.project_id = ?
          )
        """,
        (tag_id, project_id),
    )
    conn.execute(
        """
        DELETE FROM annotation_suggestions
        WHERE tag_id = ?
          AND sentence_id IN (
            SELECT s.id FROM sentences s JOIN documents d ON d.id = s.document_id WHERE d.project_id = ?
          )
        """,
        (tag_id, project_id),
    )
    conn.execute("DELETE FROM tags WHERE project_id = ? AND id = ?", (project_id, tag_id))


def _apply_suggestions_generated(conn, project_id: str, event: dict[str, Any]) -> None:
    for suggestion_id in event.get("cleared_pending_suggestion_ids", []):
        conn.execute("DELETE FROM annotation_suggestions WHERE id = ? AND status = 'pending'", (suggestion_id,))
    conn.execute(
        """
        INSERT OR REPLACE INTO annotation_runs (
          id, project_id, document_id, recipe, config_json, input_count, suggestion_count, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            event["run_id"],
            project_id,
            event["document_id"],
            event["recipe"],
            json.dumps(event.get("config", {}), ensure_ascii=False),
            event.get("input_count", 0),
            event["suggestion_count"],
            event.get("created_at") or event.get("ts"),
        ),
    )
    for suggestion in event["suggestions"]:
        conn.execute(
            """
        INSERT OR REPLACE INTO annotation_suggestions (
          id, run_id, sentence_id, tag_id, start_token_index, end_token_index,
          start_char, end_char, text, confidence, source, evidence_text, match_key, evidence_match_key,
          context_before, context_after, status, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                suggestion["id"],
                suggestion["run_id"],
                suggestion["sentence_id"],
                suggestion["tag_id"],
                suggestion["start_token_index"],
                suggestion["end_token_index"],
                suggestion["start_char"],
                suggestion["end_char"],
                suggestion["text"],
                suggestion["confidence"],
                suggestion["source"],
                suggestion.get("evidence_text"),
                suggestion.get("match_key"),
                suggestion.get("evidence_match_key"),
                suggestion.get("context_before"),
                suggestion.get("context_after"),
                suggestion["status"],
                suggestion.get("created_at") or event.get("ts"),
            ),
        )


def _populate_counts(storage: AnnotationStorage, result: RebuildResult) -> None:
    with storage.connect() as conn:
        result.documents = conn.execute("SELECT COUNT(*) AS count FROM documents WHERE project_id = ?", (result.project_id,)).fetchone()["count"]
        result.sentences = conn.execute(
            "SELECT COUNT(*) AS count FROM sentences s JOIN documents d ON d.id = s.document_id WHERE d.project_id = ?",
            (result.project_id,),
        ).fetchone()["count"]
        result.tokens = conn.execute(
            "SELECT COUNT(*) AS count FROM tokens t JOIN sentences s ON s.id = t.sentence_id JOIN documents d ON d.id = s.document_id WHERE d.project_id = ?",
            (result.project_id,),
        ).fetchone()["count"]
        result.tags = conn.execute("SELECT COUNT(*) AS count FROM tags WHERE project_id = ?", (result.project_id,)).fetchone()["count"]
        result.annotations = conn.execute(
            "SELECT COUNT(*) AS count FROM annotations a JOIN sentences s ON s.id = a.sentence_id JOIN documents d ON d.id = s.document_id WHERE d.project_id = ?",
            (result.project_id,),
        ).fetchone()["count"]
        result.suggestions = conn.execute(
            "SELECT COUNT(*) AS count FROM annotation_suggestions sg JOIN sentences s ON s.id = sg.sentence_id JOIN documents d ON d.id = s.document_id WHERE d.project_id = ?",
            (result.project_id,),
        ).fetchone()["count"]
        result.suggestion_reviews = conn.execute(
            "SELECT COUNT(*) AS count FROM annotation_suggestion_reviews rev JOIN annotation_suggestions sg ON sg.id = rev.suggestion_id JOIN sentences s ON s.id = sg.sentence_id JOIN documents d ON d.id = s.document_id WHERE d.project_id = ?",
            (result.project_id,),
        ).fetchone()["count"]
        result.runs = conn.execute("SELECT COUNT(*) AS count FROM annotation_runs WHERE project_id = ?", (result.project_id,)).fetchone()["count"]


def _remove_database_files(database_path: Path) -> None:
    for path in (database_path, database_path.with_name(database_path.name + "-wal"), database_path.with_name(database_path.name + "-shm")):
        if path.exists():
            path.unlink()


def main() -> None:
    parser = argparse.ArgumentParser(description="Rebuild an AnnoPilot SQLite database from project events.jsonl.")
    parser.add_argument("--project", default="default")
    parser.add_argument("--events", required=True, type=Path)
    parser.add_argument("--database", required=True, type=Path)
    parser.add_argument("--data-root", required=True, type=Path)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    result = rebuild_project_from_events(
        project_id=args.project,
        event_path=args.events,
        database_path=args.database,
        data_root=args.data_root,
        force=args.force,
    )
    print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
    if not result.ok:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
