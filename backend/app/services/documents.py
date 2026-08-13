from __future__ import annotations

import hashlib
import sqlite3
from collections.abc import Callable
from typing import Any

from ..text_processing import SentenceSpan, normalize_text, split_sentences, tokenize_sentence


class DocumentService:
    """Document import, merge, and reader session mutation workflows."""

    def __init__(
        self,
        connect: Callable[[], sqlite3.Connection],
        *,
        new_id: Callable[[str], str],
        now: Callable[[], str],
        enqueue_event: Callable[[sqlite3.Connection, str, dict[str, Any]], dict[str, Any]],
        flush_event_outbox: Callable[[str], int],
        seed_tags: Callable[[sqlite3.Connection, str], None],
        get_tags: Callable[[str], list[dict[str, Any]]],
        get_document_summary: Callable[[str, str], dict[str, Any]],
        default_session_id: str,
        human_actor_id: str,
        max_txt_bytes: int,
        not_found_error: type[Exception],
        validation_error: type[Exception],
    ) -> None:
        self.connect = connect
        self.new_id = new_id
        self.now = now
        self.enqueue_event = enqueue_event
        self.flush_event_outbox = flush_event_outbox
        self.seed_tags = seed_tags
        self.get_tags = get_tags
        self.get_document_summary = get_document_summary
        self.default_session_id = default_session_id
        self.human_actor_id = human_actor_id
        self.max_txt_bytes = max_txt_bytes
        self.not_found_error = not_found_error
        self.validation_error = validation_error

    def import_txt(self, project_id: str, filename: str, data: bytes) -> dict[str, Any]:
        text = self._decode_txt_payload(data)
        sentences = split_sentences(text)
        if not sentences:
            raise self.validation_error("TXT file does not contain annotatable sentences.")

        document_id = self.new_id("doc")
        now = self.now()
        imported_sentence_records, token_count = self._build_sentence_records(sentences)

        with self.connect() as conn:
            conn.execute("BEGIN")
            self.seed_tags(conn, project_id)
            conn.execute(
                """
                INSERT INTO documents (id, project_id, filename, text, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (document_id, project_id, filename, text, now),
            )
            self._insert_sentence_records(conn, document_id, imported_sentence_records)
            self.enqueue_event(
                conn,
                project_id,
                {
                    "type": "document.imported",
                    "snapshot_version": "annopilot.import_snapshot.v1",
                    "document_id": document_id,
                    "filename": filename,
                    "created_at": now,
                    "text": text,
                    "text_sha256": self._text_sha256(text),
                    "sentence_count": len(sentences),
                    "token_count": token_count,
                    "sentences": imported_sentence_records,
                },
            )
            conn.commit()

        self.flush_event_outbox(project_id)

        return {
            "document_id": document_id,
            "filename": filename,
            "sentence_count": len(sentences),
            "token_count": token_count,
            "tags": self.get_tags(project_id),
        }

    def merge_txt(self, project_id: str, document_id: str, filename: str, data: bytes) -> dict[str, Any]:
        incoming_text = self._decode_txt_payload(data)
        incoming_sentences = split_sentences(incoming_text)
        if not incoming_sentences:
            raise self.validation_error("TXT file does not contain annotatable sentences.")

        now = self.now()
        with self.connect() as conn:
            conn.execute("BEGIN")
            self.seed_tags(conn, project_id)
            document = conn.execute(
                "SELECT id, filename, text, created_at FROM documents WHERE id = ? AND project_id = ?",
                (document_id, project_id),
            ).fetchone()
            if document is None:
                raise self.not_found_error("Document not found.")

            existing_text = document["text"]
            separator = "" if not existing_text else "\n\n"
            merged_text = f"{existing_text}{separator}{incoming_text}"
            char_offset = len(existing_text) + len(separator)
            index_offset = int(
                conn.execute(
                    "SELECT COALESCE(MAX(sentence_index), -1) + 1 AS next_index FROM sentences WHERE document_id = ?",
                    (document_id,),
                ).fetchone()["next_index"]
            )
            appended_sentence_records, appended_token_count = self._build_sentence_records(
                incoming_sentences,
                index_offset=index_offset,
                char_offset=char_offset,
            )

            conn.execute("UPDATE documents SET text = ? WHERE id = ?", (merged_text, document_id))
            self._insert_sentence_records(conn, document_id, appended_sentence_records)
            snapshot = self._document_import_snapshot(conn, project_id, document_id)
            self.enqueue_event(
                conn,
                project_id,
                {
                    **snapshot,
                    "merge_source_filename": filename,
                    "merge_sentence_count": len(appended_sentence_records),
                    "merge_token_count": appended_token_count,
                    "merged_at": now,
                },
            )
            conn.commit()

        self.flush_event_outbox(project_id)
        summary = self.get_document_summary(project_id, document_id)
        return {
            "document_id": document_id,
            "filename": summary["document"]["filename"],
            "sentence_count": summary["document"]["sentence_count"],
            "token_count": summary["document"]["token_count"],
            "tags": summary["tags"],
        }

    def set_session_cursor(self, project_id: str, document_id: str, current_sentence_index: int) -> dict[str, Any]:
        with self.connect() as conn:
            document = conn.execute(
                "SELECT id FROM documents WHERE id = ? AND project_id = ?",
                (document_id, project_id),
            ).fetchone()
            if document is None:
                raise self.not_found_error("Document not found.")
            sentence_count = conn.execute(
                "SELECT COUNT(*) AS count FROM sentences WHERE document_id = ?",
                (document_id,),
            ).fetchone()["count"]
            if current_sentence_index < 0 or current_sentence_index >= int(sentence_count or 0):
                raise self.validation_error("Session cursor is outside the document sentence range.")
            now = self.now()
            conn.execute(
                """
                INSERT INTO annotation_sessions (id, project_id, document_id, actor_id, current_sentence_index, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(project_id, document_id, id) DO UPDATE SET
                  actor_id = excluded.actor_id,
                  current_sentence_index = excluded.current_sentence_index,
                  updated_at = excluded.updated_at
                """,
                (self.default_session_id, project_id, document_id, self.human_actor_id, current_sentence_index, now),
            )
        return {"session": self._get_document_session(project_id, document_id)}

    def _decode_txt_payload(self, data: bytes) -> str:
        if len(data) > self.max_txt_bytes:
            raise self.validation_error("TXT file is larger than the 10 MB limit.")
        try:
            text = normalize_text(data.decode("utf-8"))
        except UnicodeDecodeError as exc:
            raise self.validation_error("TXT file must be valid UTF-8.") from exc
        if not text.strip():
            raise self.validation_error("TXT file is empty.")
        return text

    def _build_sentence_records(
        self,
        sentences: list[SentenceSpan],
        *,
        index_offset: int = 0,
        char_offset: int = 0,
    ) -> tuple[list[dict[str, Any]], int]:
        records: list[dict[str, Any]] = []
        token_count = 0
        for sentence in sentences:
            adjusted_sentence = SentenceSpan(
                index=sentence.index + index_offset,
                text=sentence.text,
                start=sentence.start + char_offset,
                end=sentence.end + char_offset,
            )
            sentence_id = self.new_id("sent")
            token_records = [
                {
                    "id": self.new_id("tok"),
                    "token_index": token.index,
                    "text": token.text,
                    "start_char": token.start,
                    "end_char": token.end,
                }
                for token in tokenize_sentence(adjusted_sentence)
            ]
            token_count += len(token_records)
            records.append(
                {
                    "id": sentence_id,
                    "sentence_index": adjusted_sentence.index,
                    "text": adjusted_sentence.text,
                    "start_char": adjusted_sentence.start,
                    "end_char": adjusted_sentence.end,
                    "tokens": token_records,
                }
            )
        return records, token_count

    @staticmethod
    def _insert_sentence_records(conn: sqlite3.Connection, document_id: str, records: list[dict[str, Any]]) -> None:
        for sentence in records:
            conn.execute(
                """
                INSERT INTO sentences (id, document_id, sentence_index, text, start_char, end_char, completed)
                VALUES (?, ?, ?, ?, ?, ?, 0)
                """,
                (
                    sentence["id"],
                    document_id,
                    sentence["sentence_index"],
                    sentence["text"],
                    sentence["start_char"],
                    sentence["end_char"],
                ),
            )
            conn.executemany(
                """
                INSERT INTO tokens (id, sentence_id, token_index, text, start_char, end_char)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        token["id"],
                        sentence["id"],
                        token["token_index"],
                        token["text"],
                        token["start_char"],
                        token["end_char"],
                    )
                    for token in sentence["tokens"]
                ],
            )

    def _document_import_snapshot(self, conn: sqlite3.Connection, project_id: str, document_id: str) -> dict[str, Any]:
        document = conn.execute(
            "SELECT id, filename, text, created_at FROM documents WHERE id = ? AND project_id = ?",
            (document_id, project_id),
        ).fetchone()
        if document is None:
            raise self.not_found_error("Document not found.")
        sentence_rows = conn.execute(
            """
            SELECT id, sentence_index, text, start_char, end_char
            FROM sentences
            WHERE document_id = ?
            ORDER BY sentence_index
            """,
            (document_id,),
        ).fetchall()
        sentence_ids = [row["id"] for row in sentence_rows]
        tokens_by_sentence: dict[str, list[dict[str, Any]]] = {}
        if sentence_ids:
            placeholders = ",".join("?" for _ in sentence_ids)
            token_rows = conn.execute(
                f"""
                SELECT id, sentence_id, token_index, text, start_char, end_char
                FROM tokens
                WHERE sentence_id IN ({placeholders})
                ORDER BY sentence_id, token_index
                """,
                sentence_ids,
            ).fetchall()
            for token in token_rows:
                tokens_by_sentence.setdefault(token["sentence_id"], []).append(
                    self._row_dict(token, exclude={"sentence_id"})
                )

        sentence_records = [
            {
                "id": row["id"],
                "sentence_index": row["sentence_index"],
                "text": row["text"],
                "start_char": row["start_char"],
                "end_char": row["end_char"],
                "tokens": tokens_by_sentence.get(row["id"], []),
            }
            for row in sentence_rows
        ]
        token_count = sum(len(sentence["tokens"]) for sentence in sentence_records)
        return {
            "type": "document.imported",
            "snapshot_version": "annopilot.import_snapshot.v1",
            "document_id": document["id"],
            "filename": document["filename"],
            "created_at": document["created_at"],
            "text": document["text"],
            "text_sha256": self._text_sha256(document["text"]),
            "sentence_count": len(sentence_records),
            "token_count": token_count,
            "sentences": sentence_records,
        }

    def _get_document_session(self, project_id: str, document_id: str) -> dict[str, Any]:
        with self.connect() as conn:
            return self._get_session(conn, project_id, document_id)

    def _get_session(self, conn: sqlite3.Connection, project_id: str, document_id: str) -> dict[str, Any]:
        row = conn.execute(
            """
            SELECT id, actor_id, current_sentence_index, updated_at
            FROM annotation_sessions
            WHERE project_id = ? AND document_id = ? AND id = ?
            """,
            (project_id, document_id, self.default_session_id),
        ).fetchone()
        if row is None:
            return {
                "id": self.default_session_id,
                "actor_id": self.human_actor_id,
                "current_sentence_index": None,
                "updated_at": None,
            }
        return {
            "id": row["id"],
            "actor_id": row["actor_id"],
            "current_sentence_index": int(row["current_sentence_index"]),
            "updated_at": row["updated_at"],
        }

    @staticmethod
    def _row_dict(row: sqlite3.Row, exclude: set[str] | None = None) -> dict[str, Any]:
        excluded = exclude or set()
        return {key: row[key] for key in row.keys() if key not in excluded}

    @staticmethod
    def _text_sha256(text: str) -> str:
        return hashlib.sha256(text.encode("utf-8")).hexdigest()
