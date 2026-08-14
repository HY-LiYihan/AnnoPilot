from __future__ import annotations

import sqlite3
from collections.abc import Callable
from typing import Any


class AnnotationService:
    """Annotation mutation and sentence completion workflows."""

    def __init__(
        self,
        connect: Callable[[], sqlite3.Connection],
        *,
        new_id: Callable[[str], str],
        now: Callable[[], str],
        enqueue_event: Callable[[sqlite3.Connection, str, dict[str, Any]], dict[str, Any]],
        flush_event_outbox: Callable[[str], int],
        not_found_error: type[Exception],
        validation_error: type[Exception],
    ) -> None:
        self.connect = connect
        self.new_id = new_id
        self.now = now
        self.enqueue_event = enqueue_event
        self.flush_event_outbox = flush_event_outbox
        self.not_found_error = not_found_error
        self.validation_error = validation_error

    def create_annotation(
        self,
        project_id: str,
        sentence_id: str,
        tag_id: str,
        start_token_index: int,
        end_token_index: int,
        source: str = "human",
        source_suggestion_id: str | None = None,
    ) -> list[dict[str, Any]]:
        start_index, end_index = sorted((start_token_index, end_token_index))
        annotation_id = self.new_id("ann")
        now = self.now()
        if source not in {"human", "accepted_suggestion"}:
            raise self.validation_error("Unknown annotation source.")

        with self.connect() as conn:
            tag = conn.execute("SELECT id FROM tags WHERE project_id = ? AND id = ?", (project_id, tag_id)).fetchone()
            if tag is None:
                raise self.validation_error("Unknown tag.")

            sentence = conn.execute(
                """
                SELECT s.id, s.document_id, d.project_id, d.text AS document_text
                FROM sentences s
                JOIN documents d ON d.id = s.document_id
                WHERE s.id = ? AND d.project_id = ?
                """,
                (sentence_id, project_id),
            ).fetchone()
            if sentence is None:
                raise self.not_found_error("Sentence not found.")

            token_rows = conn.execute(
                """
                SELECT token_index, start_char, end_char
                FROM tokens
                WHERE sentence_id = ? AND token_index BETWEEN ? AND ?
                ORDER BY token_index
                """,
                (sentence_id, start_index, end_index),
            ).fetchall()
            expected_count = end_index - start_index + 1
            if len(token_rows) != expected_count:
                raise self.validation_error("Token range is invalid.")

            start_char = token_rows[0]["start_char"]
            end_char = token_rows[-1]["end_char"]
            selected_text = sentence["document_text"][start_char:end_char]

            conn.execute(
                """
                INSERT INTO annotations (
                    id, sentence_id, tag_id, start_token_index, end_token_index,
                    start_char, end_char, text, source, source_suggestion_id, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    annotation_id,
                    sentence_id,
                    tag_id,
                    start_index,
                    end_index,
                    start_char,
                    end_char,
                    selected_text,
                    source,
                    source_suggestion_id,
                    now,
                ),
            )
            self.enqueue_event(
                conn,
                project_id,
                {
                    "type": "annotation.created",
                    "annotation_id": annotation_id,
                    "sentence_id": sentence_id,
                    "tag_id": tag_id,
                    "start_token_index": start_index,
                    "end_token_index": end_index,
                    "start_char": start_char,
                    "end_char": end_char,
                    "text": selected_text,
                    "source": source,
                    "source_suggestion_id": source_suggestion_id,
                    "created_at": now,
                },
            )

        self.flush_event_outbox(project_id)
        return self.get_sentence_annotations(project_id, sentence_id)

    def delete_annotation(self, project_id: str, annotation_id: str) -> None:
        with self.connect() as conn:
            annotation = conn.execute(
                """
                SELECT a.id, a.sentence_id
                FROM annotations a
                JOIN sentences s ON s.id = a.sentence_id
                JOIN documents d ON d.id = s.document_id
                WHERE a.id = ? AND d.project_id = ?
                """,
                (annotation_id, project_id),
            ).fetchone()
            if annotation is None:
                raise self.not_found_error("Annotation not found.")
            conn.execute("DELETE FROM annotations WHERE id = ?", (annotation_id,))
            self.enqueue_event(
                conn,
                project_id,
                {"type": "annotation.deleted", "annotation_id": annotation_id, "sentence_id": annotation["sentence_id"]},
            )

        self.flush_event_outbox(project_id)

    def set_sentence_completed(self, project_id: str, sentence_id: str, completed: bool, answer: str | None = None) -> dict[str, Any]:
        normalized_answer = self._normalize_sentence_answer(completed, answer)
        with self.connect() as conn:
            sentence = conn.execute(
                """
                SELECT s.id, s.completed, s.answer
                FROM sentences s
                JOIN documents d ON d.id = s.document_id
                WHERE s.id = ? AND d.project_id = ?
                """,
                (sentence_id, project_id),
            ).fetchone()
            if sentence is None:
                raise self.not_found_error("Sentence not found.")
            conn.execute("UPDATE sentences SET completed = ?, answer = ? WHERE id = ?", (int(completed), normalized_answer, sentence_id))
            self.enqueue_event(
                conn,
                project_id,
                {
                    "type": "sentence.completed",
                    "sentence_id": sentence_id,
                    "old_completed": bool(sentence["completed"]),
                    "old_answer": sentence["answer"] or ("accept" if sentence["completed"] else "pending"),
                    "completed": completed,
                    "answer": normalized_answer,
                },
            )

        self.flush_event_outbox(project_id)
        return {"completed": completed, "answer": normalized_answer}

    def auto_mark_document_monogloss(self, project_id: str, document_id: str) -> dict[str, Any]:
        now = self.now()
        marked_sentence_ids: list[str] = []
        annotation_ids: list[str] = []

        with self.connect() as conn:
            document = conn.execute(
                "SELECT id, text FROM documents WHERE id = ? AND project_id = ?",
                (document_id, project_id),
            ).fetchone()
            if document is None:
                raise self.not_found_error("Document not found.")

            tag = conn.execute(
                """
                SELECT id, name
                FROM tags
                WHERE project_id = ?
                  AND (
                    id = 'engagement_monogloss'
                    OR lower(id) LIKE '%monogloss%'
                    OR lower(name) LIKE '%monogloss%'
                  )
                ORDER BY CASE WHEN id = 'engagement_monogloss' THEN 0 ELSE 1 END, id
                LIMIT 1
                """,
                (project_id,),
            ).fetchone()
            if tag is None:
                raise self.validation_error("Monogloss label is not available in the current schema.")

            rows = conn.execute(
                """
                SELECT s.id AS sentence_id, s.completed, s.answer,
                       MIN(t.token_index) AS start_token_index,
                       MAX(t.token_index) AS end_token_index,
                       MIN(t.start_char) AS start_char,
                       MAX(t.end_char) AS end_char
                FROM sentences s
                JOIN tokens t ON t.sentence_id = s.id
                WHERE s.document_id = ?
                  AND s.completed = 0
                  AND NOT EXISTS (SELECT 1 FROM annotations a WHERE a.sentence_id = s.id)
                  AND NOT EXISTS (
                    SELECT 1
                    FROM annotation_suggestions sg
                    WHERE sg.sentence_id = s.id AND sg.status = 'pending'
                  )
                GROUP BY s.id, s.completed, s.answer, s.sentence_index
                ORDER BY s.sentence_index
                """,
                (document_id,),
            ).fetchall()

            for row in rows:
                annotation_id = self.new_id("ann")
                start_char = int(row["start_char"])
                end_char = int(row["end_char"])
                selected_text = document["text"][start_char:end_char]
                conn.execute(
                    """
                    INSERT INTO annotations (
                        id, sentence_id, tag_id, start_token_index, end_token_index,
                        start_char, end_char, text, source, source_suggestion_id, created_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'auto_monogloss', NULL, ?)
                    """,
                    (
                        annotation_id,
                        row["sentence_id"],
                        tag["id"],
                        row["start_token_index"],
                        row["end_token_index"],
                        start_char,
                        end_char,
                        selected_text,
                        now,
                    ),
                )
                self.enqueue_event(
                    conn,
                    project_id,
                    {
                        "type": "annotation.created",
                        "annotation_id": annotation_id,
                        "sentence_id": row["sentence_id"],
                        "tag_id": tag["id"],
                        "start_token_index": int(row["start_token_index"]),
                        "end_token_index": int(row["end_token_index"]),
                        "start_char": start_char,
                        "end_char": end_char,
                        "text": selected_text,
                        "source": "auto_monogloss",
                        "source_suggestion_id": None,
                        "created_at": now,
                    },
                )

                old_answer = row["answer"] or ("accept" if row["completed"] else "pending")
                conn.execute("UPDATE sentences SET completed = 1, answer = 'accept' WHERE id = ?", (row["sentence_id"],))
                self.enqueue_event(
                    conn,
                    project_id,
                    {
                        "type": "sentence.completed",
                        "sentence_id": row["sentence_id"],
                        "old_completed": bool(row["completed"]),
                        "old_answer": old_answer,
                        "completed": True,
                        "answer": "accept",
                        "source": "auto_mark_monogloss",
                        "annotation_id": annotation_id,
                    },
                )
                marked_sentence_ids.append(row["sentence_id"])
                annotation_ids.append(annotation_id)

        self.flush_event_outbox(project_id)
        return {
            "marked": len(marked_sentence_ids),
            "tag_id": tag["id"],
            "tag_name": tag["name"],
            "affected_sentence_ids": marked_sentence_ids,
            "annotation_ids": annotation_ids,
        }

    def get_sentence_annotations(self, project_id: str, sentence_id: str) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT a.id, a.tag_id, tags.name AS tag_name, tags.color AS tag_color,
                       a.start_token_index, a.end_token_index, a.start_char, a.end_char, a.text,
                       a.source, a.source_suggestion_id, a.created_at
                FROM annotations a
                JOIN sentences s ON s.id = a.sentence_id
                JOIN documents d ON d.id = s.document_id
                JOIN tags ON tags.id = a.tag_id AND tags.project_id = d.project_id
                WHERE a.sentence_id = ? AND d.project_id = ?
                ORDER BY a.start_token_index, a.created_at
                """,
                (sentence_id, project_id),
            ).fetchall()
        return [self._row_dict(row) for row in rows]

    def _normalize_sentence_answer(self, completed: bool, answer: str | None) -> str:
        if not completed:
            return "pending"
        normalized = (answer or "accept").strip().lower()
        if normalized not in {"accept", "reject", "ignore"}:
            raise self.validation_error("Sentence answer must be accept, reject, or ignore when completed.")
        return normalized

    @staticmethod
    def _row_dict(row: sqlite3.Row, exclude: set[str] | None = None) -> dict[str, Any]:
        excluded = exclude or set()
        return {key: row[key] for key in row.keys() if key not in excluded}
