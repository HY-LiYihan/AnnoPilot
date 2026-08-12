from __future__ import annotations

import hashlib
import json
import sqlite3
from typing import Any


REPLAYABLE_EVENT_FIELDS = {
    "project.reset": {"reset_at"},
    "tag.created": {"tag_id", "name", "shortcut", "color"},
    "tag.renamed": {"tag_id", "name"},
    "tag.updated": {"tag_id"},
    "tag.deleted": {"tag_id"},
    "annotations.imported": {"document_id", "filename", "record_count", "source_sha256"},
    "annotation.created": {
        "annotation_id",
        "sentence_id",
        "tag_id",
        "start_token_index",
        "end_token_index",
        "start_char",
        "end_char",
        "text",
    },
    "annotation.deleted": {"annotation_id"},
    "sentence.completed": {"sentence_id", "completed"},
    "suggestion.accepted": {"suggestion_id"},
    "suggestion.rejected": {"suggestion_id"},
}


def event_replay_issue(event: dict[str, Any]) -> str | None:
    if event.get("record_type") != "event" or not event.get("event_id"):
        return "legacy_event"

    event_type = event.get("type")
    if event_type == "document.imported":
        text = event.get("text")
        if not isinstance(text, str):
            return "document_import_missing_text"
        if event.get("text_sha256") != _text_sha256(text):
            return "document_import_checksum_mismatch"
        if event.get("snapshot_version") != "annopilot.import_snapshot.v1":
            return "document_import_missing_snapshot_version"
        if not has_import_snapshot(event):
            return "document_import_missing_sentence_snapshot"
    elif event_type == "suggestions.generated":
        suggestions = event.get("suggestions")
        if not isinstance(suggestions, list) or len(suggestions) != event.get("suggestion_count"):
            return "suggestion_run_missing_snapshot"
        required = {
            "id",
            "run_id",
            "sentence_id",
            "tag_id",
            "start_token_index",
            "end_token_index",
            "text",
            "confidence",
            "source",
            "status",
        }
        if any(not isinstance(suggestion, dict) or not required.issubset(suggestion) for suggestion in suggestions):
            return "suggestion_run_incomplete_snapshot"
    elif event_type == "suggestion.llm_reviewed":
        required = {"suggestion_id", "review_id", "model", "recommendation", "confidence", "rationale"}
        if not required.issubset(event):
            return "llm_review_missing_snapshot"
    elif event_type in REPLAYABLE_EVENT_FIELDS:
        missing = REPLAYABLE_EVENT_FIELDS[event_type] - set(event)
        if missing:
            return f"{event_type}_missing_fields:{','.join(sorted(missing))}"
    else:
        return "unknown_replay_event"

    return None


def has_import_snapshot(event: dict[str, Any]) -> bool:
    sentences = event.get("sentences")
    if not isinstance(sentences, list) or not sentences:
        return False
    sentence_required = {"id", "sentence_index", "text", "start_char", "end_char", "tokens"}
    token_required = {"id", "token_index", "text", "start_char", "end_char"}
    for sentence in sentences:
        if not isinstance(sentence, dict) or not sentence_required.issubset(sentence):
            return False
        tokens = sentence.get("tokens")
        if not isinstance(tokens, list):
            return False
        if any(not isinstance(token, dict) or not token_required.issubset(token) for token in tokens):
            return False
    return True


def apply_replay_event(conn: sqlite3.Connection, project_id: str, event: dict[str, Any]) -> None:
    event_type = event.get("type")
    if event_type == "project.reset":
        clear_project_runtime_rows(conn, project_id)
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


def clear_project_runtime_rows(conn: sqlite3.Connection, project_id: str) -> None:
    conn.execute(
        """
        DELETE FROM annotation_suggestion_reviews
        WHERE suggestion_id IN (
          SELECT sg.id
          FROM annotation_suggestions sg
          JOIN sentences s ON s.id = sg.sentence_id
          JOIN documents d ON d.id = s.document_id
          WHERE d.project_id = ?
        )
        """,
        (project_id,),
    )
    conn.execute(
        """
        DELETE FROM annotation_suggestions
        WHERE sentence_id IN (
          SELECT s.id
          FROM sentences s
          JOIN documents d ON d.id = s.document_id
          WHERE d.project_id = ?
        )
        """,
        (project_id,),
    )
    conn.execute(
        """
        DELETE FROM annotations
        WHERE sentence_id IN (
          SELECT s.id
          FROM sentences s
          JOIN documents d ON d.id = s.document_id
          WHERE d.project_id = ?
        )
        """,
        (project_id,),
    )
    conn.execute(
        """
        DELETE FROM tokens
        WHERE sentence_id IN (
          SELECT s.id
          FROM sentences s
          JOIN documents d ON d.id = s.document_id
          WHERE d.project_id = ?
        )
        """,
        (project_id,),
    )
    conn.execute(
        """
        DELETE FROM sentences
        WHERE document_id IN (SELECT id FROM documents WHERE project_id = ?)
        """,
        (project_id,),
    )
    conn.execute("DELETE FROM annotation_runs WHERE project_id = ?", (project_id,))
    conn.execute("DELETE FROM annotation_sessions WHERE project_id = ?", (project_id,))
    conn.execute("DELETE FROM documents WHERE project_id = ?", (project_id,))


def _apply_document_import(conn: sqlite3.Connection, project_id: str, event: dict[str, Any]) -> None:
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


def _apply_tag_delete(conn: sqlite3.Connection, project_id: str, tag_id: str) -> None:
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


def _apply_suggestions_generated(conn: sqlite3.Connection, project_id: str, event: dict[str, Any]) -> None:
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


def _text_sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
