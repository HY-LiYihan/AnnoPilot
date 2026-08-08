from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .text_processing import normalize_text, split_sentences, tokenize_sentence


DEFAULT_PROJECT_ID = "default"
MAX_TXT_BYTES = 10 * 1024 * 1024

DEFAULT_TAGS = [
    {"id": "environmental_impact", "name": "Environmental Impact", "shortcut": "1", "color": "#0b7565"},
    {"id": "action", "name": "Action", "shortcut": "2", "color": "#326bd8"},
    {"id": "target", "name": "Target", "shortcut": "3", "color": "#c45a2e"},
    {"id": "organization", "name": "Organization", "shortcut": "4", "color": "#7a3db8"},
    {"id": "evidence", "name": "Evidence", "shortcut": "5", "color": "#b98600"},
    {"id": "risk_signal", "name": "Risk Signal", "shortcut": "6", "color": "#b43b59"},
]


class StorageError(Exception):
    pass


class NotFoundError(StorageError):
    pass


class ValidationError(StorageError):
    pass


class AnnotationStorage:
    def __init__(self, database_path: Path, data_root: Path):
        self.database_path = database_path
        self.data_root = data_root
        self._event_lock = threading.Lock()

    def initialize(self) -> None:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self.data_root.mkdir(parents=True, exist_ok=True)
        with self.connect() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")
            self._create_schema(conn)
            self._seed_tags(conn, DEFAULT_PROJECT_ID)

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.database_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def import_txt(self, project_id: str, filename: str, data: bytes) -> dict[str, Any]:
        if len(data) > MAX_TXT_BYTES:
            raise ValidationError("TXT file is larger than the 10 MB limit.")
        try:
            text = normalize_text(data.decode("utf-8"))
        except UnicodeDecodeError as exc:
            raise ValidationError("TXT file must be valid UTF-8.") from exc
        if not text.strip():
            raise ValidationError("TXT file is empty.")

        sentences = split_sentences(text)
        if not sentences:
            raise ValidationError("TXT file does not contain annotatable sentences.")

        document_id = self._new_id("doc")
        now = self._now()
        token_count = 0

        with self.connect() as conn:
            conn.execute("BEGIN")
            self._seed_tags(conn, project_id)
            conn.execute(
                """
                INSERT INTO documents (id, project_id, filename, text, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (document_id, project_id, filename, text, now),
            )
            for sentence in sentences:
                sentence_id = self._new_id("sent")
                conn.execute(
                    """
                    INSERT INTO sentences (id, document_id, sentence_index, text, start_char, end_char, completed)
                    VALUES (?, ?, ?, ?, ?, ?, 0)
                    """,
                    (sentence_id, document_id, sentence.index, sentence.text, sentence.start, sentence.end),
                )
                tokens = tokenize_sentence(sentence)
                token_count += len(tokens)
                conn.executemany(
                    """
                    INSERT INTO tokens (id, sentence_id, token_index, text, start_char, end_char)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    [
                        (self._new_id("tok"), sentence_id, token.index, token.text, token.start, token.end)
                        for token in tokens
                    ],
                )
            conn.commit()

        self.append_event(
            project_id,
            {
                "type": "document.imported",
                "document_id": document_id,
                "filename": filename,
                "sentence_count": len(sentences),
                "token_count": token_count,
            },
        )

        return {
            "document_id": document_id,
            "filename": filename,
            "sentence_count": len(sentences),
            "token_count": token_count,
            "tags": self.get_tags(project_id),
        }

    def get_document(self, project_id: str, document_id: str) -> dict[str, Any]:
        with self.connect() as conn:
            document = conn.execute(
                "SELECT id, project_id, filename, created_at FROM documents WHERE id = ? AND project_id = ?",
                (document_id, project_id),
            ).fetchone()
            if document is None:
                raise NotFoundError("Document not found.")

            sentence_rows = conn.execute(
                """
                SELECT id, sentence_index, text, start_char, end_char, completed
                FROM sentences
                WHERE document_id = ?
                ORDER BY sentence_index
                """,
                (document_id,),
            ).fetchall()
            token_rows = conn.execute(
                """
                SELECT t.id, t.sentence_id, t.token_index, t.text, t.start_char, t.end_char
                FROM tokens t
                JOIN sentences s ON s.id = t.sentence_id
                WHERE s.document_id = ?
                ORDER BY s.sentence_index, t.token_index
                """,
                (document_id,),
            ).fetchall()
            annotation_rows = conn.execute(
                """
                SELECT a.id, a.sentence_id, a.tag_id, tags.name AS tag_name, tags.color AS tag_color,
                       a.start_token_index, a.end_token_index, a.start_char, a.end_char, a.text, a.created_at
                FROM annotations a
                JOIN tags ON tags.id = a.tag_id
                JOIN sentences s ON s.id = a.sentence_id
                WHERE s.document_id = ?
                ORDER BY s.sentence_index, a.start_token_index, a.created_at
                """,
                (document_id,),
            ).fetchall()
            tags = self._get_tags(conn, project_id)

        tokens_by_sentence: dict[str, list[dict[str, Any]]] = {}
        for row in token_rows:
            tokens_by_sentence.setdefault(row["sentence_id"], []).append(self._row_dict(row, exclude={"sentence_id"}))

        annotations_by_sentence: dict[str, list[dict[str, Any]]] = {}
        for row in annotation_rows:
            annotations_by_sentence.setdefault(row["sentence_id"], []).append(self._row_dict(row, exclude={"sentence_id"}))

        sentences = []
        completed_count = 0
        for row in sentence_rows:
            completed = bool(row["completed"])
            completed_count += int(completed)
            sentences.append(
                {
                    "id": row["id"],
                    "index": row["sentence_index"],
                    "text": row["text"],
                    "start_char": row["start_char"],
                    "end_char": row["end_char"],
                    "completed": completed,
                    "tokens": tokens_by_sentence.get(row["id"], []),
                    "annotations": annotations_by_sentence.get(row["id"], []),
                }
            )

        annotation_count = sum(len(sentence["annotations"]) for sentence in sentences)
        sentence_count = len(sentences)
        progress = completed_count / sentence_count if sentence_count else 0
        tag_counts = {tag["id"]: 0 for tag in tags}
        for sentence in sentences:
            for annotation in sentence["annotations"]:
                tag_counts[annotation["tag_id"]] = tag_counts.get(annotation["tag_id"], 0) + 1
        for tag in tags:
            tag["count"] = tag_counts.get(tag["id"], 0)

        return {
            "document": {
                "id": document["id"],
                "project_id": document["project_id"],
                "filename": document["filename"],
                "created_at": document["created_at"],
                "sentence_count": sentence_count,
                "token_count": sum(len(sentence["tokens"]) for sentence in sentences),
            },
            "tags": tags,
            "sentences": sentences,
            "metrics": {
                "sentence_count": sentence_count,
                "completed_count": completed_count,
                "progress": progress,
                "annotation_count": annotation_count,
                "accuracy": None,
                "accuracy_label": "Waiting for review data",
            },
        }

    def create_annotation(
        self,
        project_id: str,
        sentence_id: str,
        tag_id: str,
        start_token_index: int,
        end_token_index: int,
    ) -> list[dict[str, Any]]:
        start_index, end_index = sorted((start_token_index, end_token_index))
        annotation_id = self._new_id("ann")
        now = self._now()

        with self.connect() as conn:
            tag = conn.execute("SELECT id FROM tags WHERE project_id = ? AND id = ?", (project_id, tag_id)).fetchone()
            if tag is None:
                raise ValidationError("Unknown tag.")

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
                raise NotFoundError("Sentence not found.")

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
                raise ValidationError("Token range is invalid.")

            start_char = token_rows[0]["start_char"]
            end_char = token_rows[-1]["end_char"]
            selected_text = sentence["document_text"][start_char:end_char]

            conn.execute(
                """
                INSERT INTO annotations (
                    id, sentence_id, tag_id, start_token_index, end_token_index,
                    start_char, end_char, text, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (annotation_id, sentence_id, tag_id, start_index, end_index, start_char, end_char, selected_text, now),
            )

        self.append_event(
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
            },
        )
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
                raise NotFoundError("Annotation not found.")
            conn.execute("DELETE FROM annotations WHERE id = ?", (annotation_id,))

        self.append_event(
            project_id,
            {"type": "annotation.deleted", "annotation_id": annotation_id, "sentence_id": annotation["sentence_id"]},
        )

    def set_sentence_completed(self, project_id: str, sentence_id: str, completed: bool) -> None:
        with self.connect() as conn:
            sentence = conn.execute(
                """
                SELECT s.id
                FROM sentences s
                JOIN documents d ON d.id = s.document_id
                WHERE s.id = ? AND d.project_id = ?
                """,
                (sentence_id, project_id),
            ).fetchone()
            if sentence is None:
                raise NotFoundError("Sentence not found.")
            conn.execute("UPDATE sentences SET completed = ? WHERE id = ?", (int(completed), sentence_id))

        self.append_event(
            project_id,
            {"type": "sentence.completed", "sentence_id": sentence_id, "completed": completed},
        )

    def get_sentence_annotations(self, project_id: str, sentence_id: str) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT a.id, a.tag_id, tags.name AS tag_name, tags.color AS tag_color,
                       a.start_token_index, a.end_token_index, a.start_char, a.end_char, a.text, a.created_at
                FROM annotations a
                JOIN tags ON tags.id = a.tag_id
                JOIN sentences s ON s.id = a.sentence_id
                JOIN documents d ON d.id = s.document_id
                WHERE a.sentence_id = ? AND d.project_id = ?
                ORDER BY a.start_token_index, a.created_at
                """,
                (sentence_id, project_id),
            ).fetchall()
        return [self._row_dict(row) for row in rows]

    def export_document_lines(self, project_id: str, document_id: str) -> list[str]:
        document = self.get_document(project_id, document_id)
        lines = []
        for sentence in document["sentences"]:
            line = {
                "document_id": document_id,
                "sentence_id": sentence["id"],
                "sentence_index": sentence["index"],
                "text": sentence["text"],
                "tokens": sentence["tokens"],
                "annotations": sentence["annotations"],
                "completed": sentence["completed"],
            }
            lines.append(json.dumps(line, ensure_ascii=False) + "\n")
        return lines

    def get_tags(self, project_id: str) -> list[dict[str, Any]]:
        with self.connect() as conn:
            self._seed_tags(conn, project_id)
            return self._get_tags(conn, project_id)

    def append_event(self, project_id: str, payload: dict[str, Any]) -> None:
        event = {"ts": self._now(), "project_id": project_id, **payload}
        project_dir = self.data_root / project_id
        project_dir.mkdir(parents=True, exist_ok=True)
        event_path = project_dir / "events.jsonl"
        with self._event_lock:
            with event_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(event, ensure_ascii=False) + "\n")

    def _create_schema(self, conn: sqlite3.Connection) -> None:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS tags (
              id TEXT NOT NULL,
              project_id TEXT NOT NULL,
              name TEXT NOT NULL,
              shortcut TEXT NOT NULL,
              color TEXT NOT NULL,
              PRIMARY KEY (project_id, id)
            );

            CREATE TABLE IF NOT EXISTS documents (
              id TEXT PRIMARY KEY,
              project_id TEXT NOT NULL,
              filename TEXT NOT NULL,
              text TEXT NOT NULL,
              created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS sentences (
              id TEXT PRIMARY KEY,
              document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
              sentence_index INTEGER NOT NULL,
              text TEXT NOT NULL,
              start_char INTEGER NOT NULL,
              end_char INTEGER NOT NULL,
              completed INTEGER NOT NULL DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS tokens (
              id TEXT PRIMARY KEY,
              sentence_id TEXT NOT NULL REFERENCES sentences(id) ON DELETE CASCADE,
              token_index INTEGER NOT NULL,
              text TEXT NOT NULL,
              start_char INTEGER NOT NULL,
              end_char INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS annotations (
              id TEXT PRIMARY KEY,
              sentence_id TEXT NOT NULL REFERENCES sentences(id) ON DELETE CASCADE,
              tag_id TEXT NOT NULL,
              start_token_index INTEGER NOT NULL,
              end_token_index INTEGER NOT NULL,
              start_char INTEGER NOT NULL,
              end_char INTEGER NOT NULL,
              text TEXT NOT NULL,
              created_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_sentences_document ON sentences(document_id, sentence_index);
            CREATE INDEX IF NOT EXISTS idx_tokens_sentence ON tokens(sentence_id, token_index);
            CREATE INDEX IF NOT EXISTS idx_annotations_sentence ON annotations(sentence_id, start_token_index);
            """
        )

    def _seed_tags(self, conn: sqlite3.Connection, project_id: str) -> None:
        conn.executemany(
            """
            INSERT OR IGNORE INTO tags (id, project_id, name, shortcut, color)
            VALUES (?, ?, ?, ?, ?)
            """,
            [(tag["id"], project_id, tag["name"], tag["shortcut"], tag["color"]) for tag in DEFAULT_TAGS],
        )

    def _get_tags(self, conn: sqlite3.Connection, project_id: str) -> list[dict[str, Any]]:
        rows = conn.execute(
            "SELECT id, name, shortcut, color FROM tags WHERE project_id = ? ORDER BY CAST(shortcut AS INTEGER)",
            (project_id,),
        ).fetchall()
        return [{**self._row_dict(row), "count": 0} for row in rows]

    @staticmethod
    def _row_dict(row: sqlite3.Row, exclude: set[str] | None = None) -> dict[str, Any]:
        excluded = exclude or set()
        return {key: row[key] for key in row.keys() if key not in excluded}

    @staticmethod
    def _new_id(prefix: str) -> str:
        return f"{prefix}_{uuid.uuid4().hex}"

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()
