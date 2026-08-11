from __future__ import annotations

import json
import hashlib
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from .db.connection import configure_connection, connect_database
from .db.migrations import migrate_database
from .events import EventOutbox
from .rag import (
    CHARACTER_RAG_RETRIEVAL,
    build_examples,
    build_match_keys_by_tag,
    build_negative_examples,
    generate_candidate_spans,
    match_normalization_config,
)
from .repositories import DocumentQueryRepository, TagQueryRepository
from .services import SuggestionDecisionService
from .text_processing import SentenceSpan, normalize_text, split_sentences, tokenize_sentence


DEFAULT_PROJECT_ID = "default"
MAX_TXT_BYTES = 10 * 1024 * 1024
MAX_JSONL_BYTES = 10 * 1024 * 1024
SUGGESTION_CONTEXT_CHARS = 48
HUMAN_ACTOR_ID = "annopilot-human"
CHARACTER_RAG_ACTOR_ID = "annopilot-character-rag"
DEFAULT_SESSION_ID = "annopilot-human"
EVENT_SCHEMA_VERSION = "annopilot.event.v1"
TASK_SCHEMA_VERSION = "annopilot.task.v1"
EXPORT_MANIFEST_SCHEMA_VERSION = "annopilot.export_manifest.v1"
PRODIGY_EXPORT_SCHEMA_VERSION = "prodigy.ner_manual.compat.v1"
PRODIGY_SPANS_EXPORT_SCHEMA_VERSION = "prodigy.spans_manual.compat.v1"
TAG_SCHEMA_VERSION = "annopilot.tag_schema.v1"
RUN_PROVENANCE_SCHEMA_VERSION = "annopilot.run_provenance.v1"
HIGH_CONFIDENCE_THRESHOLD = 0.9
MEDIUM_CONFIDENCE_THRESHOLD = 0.75
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

DEFAULT_TAGS: list[dict[str, Any]] = []

LEGACY_SEEDED_TAGS = [
    {
        "id": "noun",
        "name": "名词",
        "shortcut": "1",
        "color": "#0b7565",
        "description": "人、物、地点、抽象概念等实体或对象。",
        "examples": ["小猫", "柳树", "小河", "石桥", "叶子", "太阳", "男孩", "书包", "爪子", "水流", "桥边"],
    },
    {
        "id": "verb",
        "name": "动词",
        "shortcut": "2",
        "color": "#326bd8",
        "description": "动作、变化、状态或行为。",
        "examples": ["发芽", "走来", "看见", "伸出", "碰", "漂走", "坐", "看着", "升起来", "经过", "笑", "说", "抬起", "回答"],
    },
    {
        "id": "adjective",
        "name": "形容词",
        "shortcut": "3",
        "color": "#c45a2e",
        "description": "性质、状态、颜色、程度等修饰性词语。",
        "examples": ["金色", "安静", "轻轻", "慢慢"],
    },
]

TAG_COLORS = ["#0b7565", "#326bd8", "#c45a2e", "#7a3db8", "#b98600", "#b43b59", "#4f6f82", "#8a5f2f"]


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
        self.document_queries = DocumentQueryRepository(
            self.connect,
            not_found_error=NotFoundError,
            validation_error=ValidationError,
            default_tags=DEFAULT_TAGS,
        )
        self.tag_queries = TagQueryRepository(default_tags=DEFAULT_TAGS)
        self.event_outbox = EventOutbox(
            self.connect,
            self.data_root,
            event_lock=self._event_lock,
            new_id=self._new_id,
            now=self._now,
            event_schema_version=EVENT_SCHEMA_VERSION,
            human_actor_id=HUMAN_ACTOR_ID,
            system_actor_id=CHARACTER_RAG_ACTOR_ID,
        )
        self.suggestion_decisions = SuggestionDecisionService(
            self.connect,
            new_id=self._new_id,
            now=self._now,
            enqueue_event=self._enqueue_event,
            flush_event_outbox=self.flush_event_outbox,
            get_sentence_annotations=self.get_sentence_annotations,
            ranges_overlap=self._ranges_overlap,
            not_found_error=NotFoundError,
            validation_error=ValidationError,
        )

    def initialize(self) -> None:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self.data_root.mkdir(parents=True, exist_ok=True)
        with self.connect() as conn:
            configure_connection(conn, enable_wal=True)
            migrate_database(conn)
            self._backfill_default_tag_descriptions(conn)
            self._backfill_default_tag_examples(conn)
            self._seed_tags(conn, DEFAULT_PROJECT_ID)

    def connect(self) -> sqlite3.Connection:
        return connect_database(self.database_path)

    def import_txt(self, project_id: str, filename: str, data: bytes) -> dict[str, Any]:
        text = self._decode_txt_payload(data)
        sentences = split_sentences(text)
        if not sentences:
            raise ValidationError("TXT file does not contain annotatable sentences.")

        document_id = self._new_id("doc")
        now = self._now()
        imported_sentence_records, token_count = self._build_sentence_records(sentences)

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
            self._insert_sentence_records(conn, document_id, imported_sentence_records)
            self._enqueue_event(
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
            raise ValidationError("TXT file does not contain annotatable sentences.")

        now = self._now()
        with self.connect() as conn:
            conn.execute("BEGIN")
            self._seed_tags(conn, project_id)
            document = conn.execute(
                "SELECT id, filename, text, created_at FROM documents WHERE id = ? AND project_id = ?",
                (document_id, project_id),
            ).fetchone()
            if document is None:
                raise NotFoundError("Document not found.")

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
            self._enqueue_event(
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

    def list_documents(self, project_id: str, limit: int = 50) -> dict[str, Any]:
        return self.document_queries.list_documents(project_id, limit=limit)

    def get_document(self, project_id: str, document_id: str) -> dict[str, Any]:
        return self.document_queries.get_document(project_id, document_id)

    def get_document_summary(self, project_id: str, document_id: str) -> dict[str, Any]:
        return self.document_queries.get_document_summary(project_id, document_id)

    def set_session_cursor(self, project_id: str, document_id: str, current_sentence_index: int) -> dict[str, Any]:
        with self.connect() as conn:
            document = conn.execute(
                "SELECT id FROM documents WHERE id = ? AND project_id = ?",
                (document_id, project_id),
            ).fetchone()
            if document is None:
                raise NotFoundError("Document not found.")
            sentence_count = conn.execute(
                "SELECT COUNT(*) AS count FROM sentences WHERE document_id = ?",
                (document_id,),
            ).fetchone()["count"]
            if current_sentence_index < 0 or current_sentence_index >= int(sentence_count or 0):
                raise ValidationError("Session cursor is outside the document sentence range.")
            now = self._now()
            conn.execute(
                """
                INSERT INTO annotation_sessions (id, project_id, document_id, actor_id, current_sentence_index, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(project_id, document_id, id) DO UPDATE SET
                  actor_id = excluded.actor_id,
                  current_sentence_index = excluded.current_sentence_index,
                  updated_at = excluded.updated_at
                """,
                (DEFAULT_SESSION_ID, project_id, document_id, HUMAN_ACTOR_ID, current_sentence_index, now),
            )
        return {"session": self._get_document_session(project_id, document_id)}

    def get_document_sentences(self, project_id: str, document_id: str, offset: int = 0, limit: int = 50) -> dict[str, Any]:
        return self.document_queries.get_document_sentences(project_id, document_id, offset=offset, limit=limit)

    def get_review_queue(self, project_id: str, document_id: str, limit: int = 20, order: str = "position") -> dict[str, Any]:
        return self.document_queries.get_review_queue(project_id, document_id, limit=limit, order=order)

    def list_sentence_review_suggestion_ids(self, project_id: str, sentence_id: str, limit: int = 20) -> list[str]:
        safe_limit = max(1, min(int(limit), 100))
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

            rows = conn.execute(
                """
                SELECT sg.id
                FROM annotation_suggestions sg
                WHERE sg.sentence_id = ? AND sg.status = 'pending'
                  AND NOT EXISTS (
                    SELECT 1
                    FROM annotations a
                    WHERE a.sentence_id = sg.sentence_id
                      AND a.start_token_index <= sg.end_token_index
                      AND a.end_token_index >= sg.start_token_index
                  )
                ORDER BY sg.start_token_index, sg.confidence DESC, sg.id
                LIMIT ?
                """,
                (sentence_id, safe_limit),
            ).fetchall()
        return [row["id"] for row in rows]

    def create_annotation(
        self,
        project_id: str,
        sentence_id: str,
        tag_id: str,
        start_token_index: int,
        end_token_index: int,
        source: str = "human",
        source_suggestion_id: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        start_index, end_index = sorted((start_token_index, end_token_index))
        annotation_id = self._new_id("ann")
        now = self._now()
        if source not in {"human", "accepted_suggestion"}:
            raise ValidationError("Unknown annotation source.")

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
            self._enqueue_event(
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
                raise NotFoundError("Annotation not found.")
            conn.execute("DELETE FROM annotations WHERE id = ?", (annotation_id,))
            self._enqueue_event(
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
                raise NotFoundError("Sentence not found.")
            conn.execute("UPDATE sentences SET completed = ?, answer = ? WHERE id = ?", (int(completed), normalized_answer, sentence_id))
            self._enqueue_event(
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

    def export_document_lines(self, project_id: str, document_id: str) -> list[str]:
        document = self.get_document(project_id, document_id)
        lines = []
        for sentence in document["sentences"]:
            spans = [self._export_span(annotation, source=annotation.get("source", "human")) for annotation in sentence["annotations"]]
            suggestions = [self._export_suggestion(suggestion) for suggestion in sentence["suggestions"]]
            line = {
                "schema_version": TASK_SCHEMA_VERSION,
                "record_type": "annotation_task",
                "project_id": project_id,
                "document_id": document_id,
                "sentence_id": sentence["id"],
                "sentence_index": sentence["index"],
                "text": sentence["text"],
                "document": {
                    "id": document["document"]["id"],
                    "filename": document["document"]["filename"],
                    "created_at": document["document"]["created_at"],
                },
                "tokens": [self._export_token(token) for token in sentence["tokens"]],
                "spans": spans,
                "annotations": sentence["annotations"],
                "suggestions": suggestions,
                "answer": sentence.get("answer", "accept" if sentence["completed"] else "pending"),
                "completed": sentence["completed"],
                "_view_id": "spans_manual",
                "_session_id": self._export_prodigy_session_id(project_id, document_id, sentence["annotations"]),
                "_annotator_id": self._export_prodigy_annotator_id(sentence["annotations"]),
                "_input_hash": self._stable_hash({"text": sentence["text"]}),
                "_task_hash": self._stable_hash(
                    {
                        "document_id": document_id,
                        "sentence_id": sentence["id"],
                        "text": sentence["text"],
                        "spans": spans,
                        "suggestions": suggestions,
                    }
                ),
                "meta": {
                    "storage": "sqlite_runtime_jsonl_export",
                    "span_count": len(sentence["annotations"]),
                    "suggestion_count": len(sentence["suggestions"]),
                    "session_id": self._export_prodigy_session_id(project_id, document_id, sentence["annotations"]),
                    "annotator_id": self._export_prodigy_annotator_id(sentence["annotations"]),
                },
            }
            lines.append(json.dumps(line, ensure_ascii=False) + "\n")
        return lines

    def export_prodigy_document_lines(self, project_id: str, document_id: str) -> list[str]:
        return self._export_prodigy_document_lines(project_id, document_id, view_id="ner_manual")

    def export_prodigy_spans_document_lines(self, project_id: str, document_id: str) -> list[str]:
        return self._export_prodigy_document_lines(project_id, document_id, view_id="spans_manual")

    def _export_prodigy_document_lines(self, project_id: str, document_id: str, view_id: str) -> list[str]:
        document = self.get_document(project_id, document_id)
        lines = []
        document_meta = document["document"]
        for sentence in document["sentences"]:
            spans = [self._export_prodigy_span(annotation, sentence["start_char"]) for annotation in sentence["annotations"]]
            line = {
                "text": sentence["text"],
                "tokens": [
                    self._export_prodigy_token(token, sentence["text"], sentence["start_char"])
                    for token in sentence["tokens"]
                ],
                "spans": spans,
                "answer": self._export_prodigy_answer(sentence),
                "_view_id": view_id,
                "_session_id": self._export_prodigy_session_id(project_id, document_id, sentence["annotations"]),
                "_annotator_id": self._export_prodigy_annotator_id(sentence["annotations"]),
                "_input_hash": self._stable_hash({"text": sentence["text"]}),
                "_task_hash": self._stable_hash(
                    {
                        "document_id": document_id,
                        "sentence_id": sentence["id"],
                        "text": sentence["text"],
                        "spans": spans,
                    }
                ),
                "meta": {
                    "source": "annopilot",
                    "project_id": project_id,
                    "document_id": document_id,
                    "sentence_id": sentence["id"],
                    "sentence_index": sentence["index"],
                    "filename": document_meta["filename"],
                    "completed": sentence["completed"],
                    "answer": sentence.get("answer", "accept" if sentence["completed"] else "pending"),
                    "suggestion_count": len(sentence["suggestions"]),
                    "annotation_sources": [
                        self._export_prodigy_annotation_source(annotation)
                        for annotation in sentence["annotations"]
                    ],
                },
            }
            lines.append(json.dumps(line, ensure_ascii=False) + "\n")
        return lines

    def export_manifest(self, project_id: str, document_id: str) -> dict[str, Any]:
        document = self.get_document(project_id, document_id)
        task_lines = self.export_document_lines(project_id, document_id)
        prodigy_lines = self.export_prodigy_document_lines(project_id, document_id)
        prodigy_spans_lines = self.export_prodigy_spans_document_lines(project_id, document_id)
        event_lines = self.export_event_lines(project_id)
        audit_summary = self.audit_project(project_id)
        tag_schema_payload = self.export_tag_schema(project_id)
        tag_schema_line = json.dumps(tag_schema_payload, ensure_ascii=False, sort_keys=True) + "\n"
        runs = self.list_runs(project_id, document_id=document_id, limit=50)
        annotation_imports = self.list_annotation_imports(project_id, document_id=document_id, limit=50)["imports"]
        run_provenance_artifacts: dict[str, dict[str, Any]] = {}
        for run in runs:
            payload = self.export_run_provenance(project_id, run["id"])
            run_provenance_artifacts[run["id"]] = self._artifact_summary(
                filename=f"{run['id']}.provenance.json",
                schema_version=RUN_PROVENANCE_SCHEMA_VERSION,
                lines=[json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n"],
                content_sha256=payload["content_sha256"],
            )
        source_counts: dict[str, int] = {}
        for sentence in document["sentences"]:
            for annotation in sentence["annotations"]:
                source = annotation.get("source", "human")
                source_counts[source] = source_counts.get(source, 0) + 1

        manifest = {
            "schema_version": EXPORT_MANIFEST_SCHEMA_VERSION,
            "record_type": "export_manifest",
            "generated_at": self._now(),
            "project_id": project_id,
            "document": document["document"],
            "metrics": document["metrics"],
            "tag_count": len(document["tags"]),
            "annotation_source_counts": dict(sorted(source_counts.items())),
            "source_run_ids": [run["id"] for run in runs],
            "runs": runs,
            "annotation_imports": annotation_imports,
            "event_audit": self._manifest_event_audit(audit_summary),
            "run_provenance_artifacts": run_provenance_artifacts,
            "artifacts": {
                "tasks_jsonl": self._artifact_summary(
                    filename=f"{document_id}.jsonl",
                    schema_version=TASK_SCHEMA_VERSION,
                    lines=task_lines,
                ),
                "prodigy_jsonl": self._artifact_summary(
                    filename=f"{document_id}.prodigy.jsonl",
                    schema_version=PRODIGY_EXPORT_SCHEMA_VERSION,
                    lines=prodigy_lines,
                ),
                "prodigy_spans_jsonl": self._artifact_summary(
                    filename=f"{document_id}.prodigy.spans.jsonl",
                    schema_version=PRODIGY_SPANS_EXPORT_SCHEMA_VERSION,
                    lines=prodigy_spans_lines,
                ),
                "events_jsonl": self._artifact_summary(
                    filename=f"{project_id}-events.jsonl",
                    schema_version=EVENT_SCHEMA_VERSION,
                    lines=event_lines,
                ),
                "tag_schema_json": self._artifact_summary(
                    filename=f"{project_id}-tag-schema.json",
                    schema_version=TAG_SCHEMA_VERSION,
                    lines=[tag_schema_line],
                    content_sha256=tag_schema_payload["content_sha256"],
                ),
            },
        }
        manifest["content_sha256"] = self._payload_sha256(self._manifest_content_payload(manifest))
        return manifest

    @staticmethod
    def _manifest_event_audit(audit_summary: dict[str, Any]) -> dict[str, Any]:
        return {
            "project_id": audit_summary["project_id"],
            "event_count": audit_summary["event_count"],
            "pending_outbox_count": audit_summary["pending_outbox_count"],
            "invalid_event_count": audit_summary["invalid_event_count"],
            "legacy_event_count": audit_summary.get("legacy_event_count", 0),
            "non_replayable_event_count": audit_summary.get("non_replayable_event_count", 0),
            "replay_issue_counts": audit_summary.get("replay_issue_counts", {}),
            "schema_versions": audit_summary["schema_versions"],
            "event_types": audit_summary["event_types"],
            "actor_type_counts": audit_summary.get("actor_type_counts", {}),
            "actor_id_counts": audit_summary.get("actor_id_counts", {}),
            "last_event_type": audit_summary["last_event_type"],
            "last_event_ts": audit_summary["last_event_ts"],
            "rebuild_status": audit_summary["rebuild_status"],
        }

    @staticmethod
    def _manifest_content_payload(manifest: dict[str, Any]) -> dict[str, Any]:
        payload = json.loads(
            json.dumps(
                {key: value for key, value in manifest.items() if key not in {"generated_at", "content_sha256"}},
                ensure_ascii=False,
            )
        )
        for group_name in ("artifacts", "run_provenance_artifacts"):
            group = payload.get(group_name)
            if not isinstance(group, dict):
                continue
            for artifact in group.values():
                if isinstance(artifact, dict) and artifact.get("content_sha256"):
                    artifact.pop("sha256", None)
        return payload

    def export_tag_schema(self, project_id: str) -> dict[str, Any]:
        tags = self.get_tags(project_id)
        payload = self._tag_schema_payload(project_id, tags)
        return {
            **payload,
            "generated_at": self._now(),
            "content_sha256": self._payload_sha256(self._tag_schema_content_payload(tags)),
        }

    def import_tag_schema(self, project_id: str, schema: dict[str, Any]) -> dict[str, Any]:
        incoming_tags = self._validate_tag_schema_import(schema)
        source_hash = schema.get("content_sha256")
        content_hash = self._payload_sha256(self._tag_schema_content_payload(incoming_tags))
        if source_hash and source_hash != content_hash:
            raise ValidationError("Tag schema content_sha256 does not match tags payload.")

        created = 0
        updated = 0
        skipped = 0
        with self.connect() as conn:
            self._seed_tags(conn, project_id)
            existing = self._get_tags(conn, project_id)
            existing_by_id = {tag["id"]: tag for tag in existing}
            existing_by_name = {tag["name"].casefold(): tag for tag in existing}
            used_shortcuts = {tag["shortcut"] for tag in existing}

            for incoming in incoming_tags:
                target = existing_by_id.get(incoming["id"]) or existing_by_name.get(incoming["name"].casefold())
                if target:
                    name_owner = existing_by_name.get(incoming["name"].casefold())
                    if name_owner and name_owner["id"] != target["id"]:
                        raise ValidationError(f"Tag name already exists: {incoming['name']}")
                    used_without_current = used_shortcuts - {target["shortcut"]}
                    next_shortcut = self._unique_shortcut(incoming.get("shortcut"), used_without_current)
                    changed = (
                        target["name"] != incoming["name"]
                        or target.get("description") != incoming.get("description")
                        or target.get("examples", []) != incoming.get("examples", [])
                        or target["shortcut"] != next_shortcut
                        or target["color"] != incoming["color"]
                    )
                    if not changed:
                        skipped += 1
                        continue

                    conn.execute(
                        """
                        UPDATE tags
                        SET name = ?, description = ?, examples_json = ?, shortcut = ?, color = ?
                        WHERE project_id = ? AND id = ?
                        """,
                        (
                            incoming["name"],
                            incoming.get("description"),
                            json.dumps(incoming.get("examples", []), ensure_ascii=False),
                            next_shortcut,
                            incoming["color"],
                            project_id,
                            target["id"],
                        ),
                    )
                    self._enqueue_event(
                        conn,
                        project_id,
                        {
                            "type": "tag.updated",
                            "tag_id": target["id"],
                            "old_name": target["name"],
                            "name": incoming["name"],
                            "old_description": target.get("description"),
                            "description": incoming.get("description"),
                            "old_examples": target.get("examples", []),
                            "examples": incoming.get("examples", []),
                            "old_shortcut": target["shortcut"],
                            "shortcut": next_shortcut,
                            "old_color": target["color"],
                            "color": incoming["color"],
                        },
                    )
                    used_shortcuts = used_without_current | {next_shortcut}
                    updated += 1
                else:
                    next_shortcut = self._unique_shortcut(incoming.get("shortcut"), used_shortcuts)
                    conn.execute(
                        """
                        INSERT INTO tags (id, project_id, name, description, examples_json, shortcut, color)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            incoming["id"],
                            project_id,
                            incoming["name"],
                            incoming.get("description"),
                            json.dumps(incoming.get("examples", []), ensure_ascii=False),
                            next_shortcut,
                            incoming["color"],
                        ),
                    )
                    self._enqueue_event(
                        conn,
                        project_id,
                        {
                            "type": "tag.created",
                            "tag_id": incoming["id"],
                            "name": incoming["name"],
                            "description": incoming.get("description"),
                            "examples": incoming.get("examples", []),
                            "shortcut": next_shortcut,
                            "color": incoming["color"],
                        },
                    )
                    used_shortcuts.add(next_shortcut)
                    created += 1

                existing = self._get_tags(conn, project_id)
                existing_by_id = {tag["id"]: tag for tag in existing}
                existing_by_name = {tag["name"].casefold(): tag for tag in existing}

            tags = self._get_tags(conn, project_id)

        self.flush_event_outbox(project_id)
        return {"created": created, "updated": updated, "skipped": skipped, "content_sha256": content_hash, "tags": tags}

    def import_annotations_jsonl(self, project_id: str, document_id: str, filename: str, data: bytes) -> dict[str, Any]:
        if len(data) > MAX_JSONL_BYTES:
            raise ValidationError("JSONL file is larger than the 10 MB limit.")
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValidationError("JSONL file must be valid UTF-8.") from exc
        records = self._parse_annotation_jsonl(text)
        if not records:
            raise ValidationError("JSONL file does not contain annotation records.")

        source_sha256 = hashlib.sha256(data).hexdigest()
        matched_count = 0
        skipped_count = 0
        created_tag_count = 0
        created_annotation_count = 0
        deleted_annotation_count = 0
        completed_sentence_count = 0
        source_record_results: list[dict[str, Any]] = []
        now = self._now()

        with self.connect() as conn:
            document = conn.execute(
                "SELECT id, text FROM documents WHERE id = ? AND project_id = ?",
                (document_id, project_id),
            ).fetchone()
            if document is None:
                raise NotFoundError("Document not found.")
            self._seed_tags(conn, project_id)

            sentence_rows = conn.execute(
                """
                SELECT id, sentence_index, text, start_char, end_char, completed, answer
                FROM sentences
                WHERE document_id = ?
                ORDER BY sentence_index
                """,
                (document_id,),
            ).fetchall()
            sentence_ids = [row["id"] for row in sentence_rows]
            placeholders = ", ".join("?" for _ in sentence_ids)
            token_rows = conn.execute(
                f"""
                SELECT id, sentence_id, token_index, text, start_char, end_char
                FROM tokens
                WHERE sentence_id IN ({placeholders})
                ORDER BY sentence_id, token_index
                """,
                sentence_ids,
            ).fetchall()
            tokens_by_sentence: dict[str, list[dict[str, Any]]] = {}
            for token in token_rows:
                tokens_by_sentence.setdefault(token["sentence_id"], []).append(self._row_dict(token))

            sentences = [self._row_dict(row) for row in sentence_rows]
            sentence_by_id = {sentence["id"]: sentence for sentence in sentences}
            sentence_by_index = {sentence["sentence_index"]: sentence for sentence in sentences}
            sentences_by_text: dict[str, list[dict[str, Any]]] = {}
            for sentence in sentences:
                sentences_by_text.setdefault(sentence["text"], []).append(sentence)

            tags = self._get_tags(conn, project_id)
            tags_by_id = {tag["id"]: tag for tag in tags}
            tags_by_name = {tag["name"].casefold(): tag for tag in tags}
            used_shortcuts = {tag["shortcut"] for tag in tags}

            def ensure_import_tag(label: str) -> dict[str, Any]:
                nonlocal created_tag_count, tags, used_shortcuts
                normalized_label = label.strip()
                if not normalized_label:
                    raise ValidationError("Imported span label is required.")
                existing_tag = tags_by_id.get(normalized_label) or tags_by_name.get(normalized_label.casefold())
                if existing_tag:
                    return existing_tag
                tag_id = self._new_id("tag")
                shortcut = self._unique_shortcut(None, used_shortcuts)
                color = TAG_COLORS[len(tags) % len(TAG_COLORS)]
                tag = {
                    "id": tag_id,
                    "name": normalized_label,
                    "description": "Imported from Prodigy/AnnoPilot JSONL.",
                    "examples": [],
                    "shortcut": shortcut,
                    "color": color,
                    "count": 0,
                    "usage_count": 0,
                    "suggestion_count": 0,
                }
                conn.execute(
                    """
                    INSERT INTO tags (id, project_id, name, description, examples_json, shortcut, color)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (tag_id, project_id, tag["name"], tag["description"], "[]", shortcut, color),
                )
                self._enqueue_event(
                    conn,
                    project_id,
                    {
                        "type": "tag.created",
                        "tag_id": tag_id,
                        "name": tag["name"],
                        "description": tag["description"],
                        "examples": [],
                        "shortcut": shortcut,
                        "color": color,
                    },
                )
                tags.append(tag)
                tags_by_id[tag_id] = tag
                tags_by_name[tag["name"].casefold()] = tag
                used_shortcuts.add(shortcut)
                created_tag_count += 1
                return tag

            for line_number, record in records:
                record_result: dict[str, Any] = {
                    "line_number": line_number,
                    "record_sha256": self._payload_sha256(record),
                }
                source_metadata = self._import_record_source_metadata(record)
                if source_metadata:
                    record_result["source_metadata"] = source_metadata

                sentence = self._match_import_sentence(record, sentence_by_id, sentence_by_index, sentences_by_text)
                if sentence is None:
                    skipped_count += 1
                    record_result.update({"status": "skipped", "reason": "no_sentence_match"})
                    source_record_results.append(record_result)
                    continue

                record_result.update(
                    {
                        "status": "matched",
                        "sentence_id": sentence["id"],
                        "sentence_index": sentence["sentence_index"],
                    }
                )

                spans = record.get("spans") or []
                if not isinstance(spans, list):
                    skipped_count += 1
                    record_result.update({"status": "skipped", "reason": "invalid_spans", "raw_span_count": None})
                    source_record_results.append(record_result)
                    continue
                answer = self._normalize_import_answer(record.get("answer"), has_spans=bool(spans))
                tokens = tokens_by_sentence.get(sentence["id"], [])
                try:
                    annotation_specs = [
                        self._build_import_annotation_spec(span, sentence, tokens, document["text"])
                        for span in spans
                    ] if answer == "accept" else []
                except ValidationError as exc:
                    skipped_count += 1
                    record_result.update(
                        {
                            "status": "skipped",
                            "reason": "invalid_span",
                            "message": str(exc),
                            "answer": answer,
                            "raw_span_count": len(spans),
                        }
                    )
                    source_record_results.append(record_result)
                    continue

                existing_annotations = conn.execute(
                    "SELECT id, sentence_id FROM annotations WHERE sentence_id = ? ORDER BY start_token_index, created_at",
                    (sentence["id"],),
                ).fetchall()
                deleted_for_record_count = len(existing_annotations)
                for annotation in existing_annotations:
                    conn.execute("DELETE FROM annotations WHERE id = ?", (annotation["id"],))
                    self._enqueue_event(
                        conn,
                        project_id,
                        {"type": "annotation.deleted", "annotation_id": annotation["id"], "sentence_id": annotation["sentence_id"]},
                    )
                    deleted_annotation_count += 1

                created_for_record_ids: list[str] = []
                if answer == "accept":
                    for spec in annotation_specs:
                        tag = ensure_import_tag(spec["label"])
                        annotation_id = self._new_id("ann")
                        created_for_record_ids.append(annotation_id)
                        conn.execute(
                            """
                            INSERT INTO annotations (
                                id, sentence_id, tag_id, start_token_index, end_token_index,
                                start_char, end_char, text, source, source_suggestion_id, created_at
                            )
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'prodigy_import', NULL, ?)
                            """,
                            (
                                annotation_id,
                                sentence["id"],
                                tag["id"],
                                spec["start_token_index"],
                                spec["end_token_index"],
                                spec["start_char"],
                                spec["end_char"],
                                spec["text"],
                                now,
                            ),
                        )
                        self._enqueue_event(
                            conn,
                            project_id,
                            {
                                "type": "annotation.created",
                                "annotation_id": annotation_id,
                                "sentence_id": sentence["id"],
                                "tag_id": tag["id"],
                                "start_token_index": spec["start_token_index"],
                                "end_token_index": spec["end_token_index"],
                                "start_char": spec["start_char"],
                                "end_char": spec["end_char"],
                                "text": spec["text"],
                                "source": "prodigy_import",
                                "source_suggestion_id": None,
                                "created_at": now,
                            },
                        )
                        created_annotation_count += 1

                completed = answer in {"accept", "reject", "ignore"}
                previous_answer = sentence["answer"] or ("accept" if sentence["completed"] else "pending")
                conn.execute("UPDATE sentences SET completed = ?, answer = ? WHERE id = ?", (int(completed), answer, sentence["id"]))
                self._enqueue_event(
                    conn,
                    project_id,
                    {
                        "type": "sentence.completed",
                        "sentence_id": sentence["id"],
                        "old_completed": bool(sentence["completed"]),
                        "old_answer": previous_answer,
                        "completed": completed,
                        "answer": answer,
                    },
                )
                sentence["completed"] = int(completed)
                sentence["answer"] = answer
                if completed:
                    completed_sentence_count += 1
                matched_count += 1
                record_result.update(
                    {
                        "answer": answer,
                        "completed": completed,
                        "raw_span_count": len(spans),
                        "created_annotation_count": len(created_for_record_ids),
                        "created_annotation_ids": created_for_record_ids,
                        "deleted_annotation_count": deleted_for_record_count,
                    }
                )
                source_record_results.append(record_result)

            self._enqueue_event(
                conn,
                project_id,
                {
                    "type": "annotations.imported",
                    "document_id": document_id,
                    "filename": filename,
                    "record_count": len(records),
                    "matched_count": matched_count,
                    "skipped_count": skipped_count,
                    "created_tag_count": created_tag_count,
                    "created_annotation_count": created_annotation_count,
                    "deleted_annotation_count": deleted_annotation_count,
                    "completed_sentence_count": completed_sentence_count,
                    "source_sha256": source_sha256,
                    "source_record_results": source_record_results,
                },
            )

        self.flush_event_outbox(project_id)
        return {
            "document_id": document_id,
            "filename": filename,
            "record_count": len(records),
            "matched_count": matched_count,
            "skipped_count": skipped_count,
            "created_tag_count": created_tag_count,
            "created_annotation_count": created_annotation_count,
            "deleted_annotation_count": deleted_annotation_count,
            "completed_sentence_count": completed_sentence_count,
            "source_sha256": source_sha256,
            "tags": self.get_tags(project_id),
        }

    def reset_project(self, project_id: str) -> dict[str, Any]:
        self.flush_event_outbox(project_id)
        reset_at = self._now()
        with self.connect() as conn:
            conn.execute("BEGIN")
            self._seed_tags(conn, project_id)
            counts = self._count_project_runtime_rows(conn, project_id)
            self._clear_project_runtime_rows(conn, project_id)
            self._enqueue_event(
                conn,
                project_id,
                {
                    "type": "project.reset",
                    "reset_at": reset_at,
                    **counts,
                },
            )
            conn.commit()

        self.flush_event_outbox(project_id)
        return {"project_id": project_id, "reset_at": reset_at, **counts}

    def export_event_lines(self, project_id: str) -> list[str]:
        self.flush_event_outbox(project_id)
        event_path = self.data_root / project_id / "events.jsonl"
        if not event_path.exists():
            return []
        return event_path.read_text(encoding="utf-8").splitlines(keepends=True)

    def audit_project(self, project_id: str) -> dict[str, Any]:
        self.flush_event_outbox(project_id)
        with self.connect() as conn:
            pending_outbox_count = conn.execute(
                "SELECT COUNT(*) AS count FROM event_outbox WHERE project_id = ? AND flushed_at IS NULL",
                (project_id,),
            ).fetchone()["count"]

        event_count = 0
        invalid_event_count = 0
        legacy_event_count = 0
        non_replayable_event_count = 0
        replay_issue_counts: dict[str, int] = {}
        replay_issues: list[dict[str, Any]] = []
        schema_versions: set[str] = set()
        event_types: dict[str, int] = {}
        actor_type_counts: dict[str, int] = {}
        actor_id_counts: dict[str, int] = {}
        last_event_type: Optional[str] = None
        last_event_ts: Optional[str] = None

        for line_number, line in enumerate(self.export_event_lines(project_id), start=1):
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                invalid_event_count += 1
                self._record_replay_issue(
                    replay_issue_counts,
                    replay_issues,
                    line_number=line_number,
                    event_id=None,
                    event_type=None,
                    message="invalid_json",
                )
                continue
            event_count += 1
            event_type = str(event.get("type", "unknown"))
            if event.get("record_type") != "event" or not event.get("event_id"):
                if event_type != "unknown":
                    legacy_event_count += 1
                    schema_versions.add("legacy")
                else:
                    invalid_event_count += 1
            if event.get("schema_version"):
                schema_versions.add(str(event["schema_version"]))
            actor_type = str(event.get("actor_type") or "unknown")
            actor_id = str(event.get("actor_id") or "unknown")
            actor_type_counts[actor_type] = actor_type_counts.get(actor_type, 0) + 1
            actor_id_counts[actor_id] = actor_id_counts.get(actor_id, 0) + 1
            replay_issue = self._event_replay_issue(event)
            if replay_issue:
                non_replayable_event_count += 1
                self._record_replay_issue(
                    replay_issue_counts,
                    replay_issues,
                    line_number=line_number,
                    event_id=event.get("event_id"),
                    event_type=event_type,
                    message=replay_issue,
                )
            event_types[event_type] = event_types.get(event_type, 0) + 1
            last_event_type = event_type
            last_event_ts = str(event.get("ts", "")) or None

        rebuild_status = (
            "ready"
            if pending_outbox_count == 0 and invalid_event_count == 0 and legacy_event_count == 0 and non_replayable_event_count == 0
            else "needs_attention"
        )
        return {
            "project_id": project_id,
            "event_count": event_count,
            "pending_outbox_count": pending_outbox_count,
            "invalid_event_count": invalid_event_count,
            "legacy_event_count": legacy_event_count,
            "non_replayable_event_count": non_replayable_event_count,
            "replay_issue_counts": dict(sorted(replay_issue_counts.items())),
            "replay_issues": replay_issues,
            "schema_versions": sorted(schema_versions),
            "event_types": dict(sorted(event_types.items())),
            "actor_type_counts": dict(sorted(actor_type_counts.items())),
            "actor_id_counts": dict(sorted(actor_id_counts.items())),
            "last_event_type": last_event_type,
            "last_event_ts": last_event_ts,
            "rebuild_status": rebuild_status,
        }

    def list_annotation_imports(
        self,
        project_id: str,
        document_id: Optional[str] = None,
        limit: int = 5,
    ) -> dict[str, Any]:
        safe_limit = max(1, min(limit, 50))
        imports: list[dict[str, Any]] = []
        for line in self.export_event_lines(project_id):
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if event.get("type") != "annotations.imported":
                continue
            if document_id and event.get("document_id") != document_id:
                continue
            if not event.get("document_id") or not event.get("filename"):
                continue
            imports.append(
                {
                    "event_id": event.get("event_id"),
                    "document_id": event["document_id"],
                    "filename": event["filename"],
                    "record_count": self._event_int(event.get("record_count")),
                    "matched_count": self._event_int(event.get("matched_count")),
                    "skipped_count": self._event_int(event.get("skipped_count")),
                    "created_tag_count": self._event_int(event.get("created_tag_count")),
                    "created_annotation_count": self._event_int(event.get("created_annotation_count")),
                    "deleted_annotation_count": self._event_int(event.get("deleted_annotation_count")),
                    "completed_sentence_count": self._event_int(event.get("completed_sentence_count")),
                    "source_sha256": str(event.get("source_sha256", "")),
                    "source_record_results": event.get("source_record_results") if isinstance(event.get("source_record_results"), list) else [],
                    "actor_id": event.get("actor_id"),
                    "ts": event.get("ts"),
                }
            )
        return {"imports": list(reversed(imports))[:safe_limit]}

    def list_runs(self, project_id: str, document_id: Optional[str] = None, limit: int = 10) -> list[dict[str, Any]]:
        safe_limit = max(1, min(limit, 50))
        where_clause = "runs.project_id = ?"
        params: list[Any] = [project_id]
        if document_id:
            where_clause += " AND runs.document_id = ?"
            params.append(document_id)
        params.append(safe_limit)
        with self.connect() as conn:
            rows = conn.execute(
                f"""
                SELECT runs.id, runs.project_id, runs.document_id, documents.filename,
                       runs.recipe, runs.config_json, runs.input_count, runs.suggestion_count, runs.created_at,
                       COALESCE(SUM(CASE WHEN suggestions.status = 'pending' THEN 1 ELSE 0 END), 0) AS pending_count,
                       COALESCE(SUM(CASE WHEN suggestions.status = 'accepted' THEN 1 ELSE 0 END), 0) AS accepted_count,
                       COALESCE(SUM(CASE WHEN suggestions.status = 'rejected' THEN 1 ELSE 0 END), 0) AS rejected_count
                FROM annotation_runs runs
                JOIN documents ON documents.id = runs.document_id
                LEFT JOIN annotation_suggestions suggestions ON suggestions.run_id = runs.id
                WHERE {where_clause}
                GROUP BY runs.id, runs.project_id, runs.document_id, documents.filename,
                         runs.recipe, runs.config_json, runs.input_count, runs.suggestion_count, runs.created_at
                ORDER BY runs.created_at DESC, runs.id DESC
                LIMIT ?
                """,
                tuple(params),
            ).fetchall()
        run_ids = [row["id"] for row in rows]
        source_counts_by_run = self._run_source_counts(project_id, run_ids)
        confidence_counts_by_run = self._run_confidence_counts(project_id, run_ids)
        return [
            {
                "id": row["id"],
                "project_id": row["project_id"],
                "document_id": row["document_id"],
                "filename": row["filename"],
                "recipe": row["recipe"],
                "config": json.loads(row["config_json"]),
                "input_count": row["input_count"],
                "suggestion_count": row["suggestion_count"],
                "pending_count": row["pending_count"],
                "accepted_count": row["accepted_count"],
                "rejected_count": row["rejected_count"],
                "acceptance_rate": self._acceptance_rate(row["accepted_count"], row["rejected_count"]),
                "source_counts": source_counts_by_run.get(row["id"], {}),
                "confidence_counts": confidence_counts_by_run.get(row["id"], {}),
                "created_at": row["created_at"],
            }
            for row in rows
        ]

    def _run_source_counts(self, project_id: str, run_ids: list[str]) -> dict[str, dict[str, int]]:
        if not run_ids:
            return {}
        placeholders = ", ".join("?" for _ in run_ids)
        with self.connect() as conn:
            rows = conn.execute(
                f"""
                SELECT sg.run_id, sg.source, COUNT(*) AS count
                FROM annotation_suggestions sg
                JOIN sentences s ON s.id = sg.sentence_id
                JOIN documents d ON d.id = s.document_id
                WHERE d.project_id = ? AND sg.run_id IN ({placeholders})
                GROUP BY sg.run_id, sg.source
                ORDER BY sg.run_id, sg.source
                """,
                (project_id, *run_ids),
            ).fetchall()
        counts: dict[str, dict[str, int]] = {}
        for row in rows:
            counts.setdefault(row["run_id"], {})[row["source"]] = int(row["count"])
        return counts

    def _run_confidence_counts(self, project_id: str, run_ids: list[str]) -> dict[str, dict[str, int]]:
        if not run_ids:
            return {}
        placeholders = ", ".join("?" for _ in run_ids)
        with self.connect() as conn:
            rows = conn.execute(
                f"""
                SELECT sg.run_id,
                       CASE
                         WHEN sg.confidence >= ? THEN 'high'
                         WHEN sg.confidence >= ? THEN 'medium'
                         ELSE 'low'
                       END AS bucket,
                       COUNT(*) AS count
                FROM annotation_suggestions sg
                JOIN sentences s ON s.id = sg.sentence_id
                JOIN documents d ON d.id = s.document_id
                WHERE d.project_id = ? AND sg.run_id IN ({placeholders})
                GROUP BY sg.run_id, bucket
                ORDER BY sg.run_id, bucket
                """,
                (HIGH_CONFIDENCE_THRESHOLD, MEDIUM_CONFIDENCE_THRESHOLD, project_id, *run_ids),
            ).fetchall()
        counts: dict[str, dict[str, int]] = {}
        for row in rows:
            counts.setdefault(row["run_id"], {})[row["bucket"]] = int(row["count"])
        return counts

    @staticmethod
    def _suggestion_source_counts(suggestions: list[dict[str, Any]]) -> dict[str, int]:
        counts: dict[str, int] = {}
        for suggestion in suggestions:
            source = str(suggestion.get("source") or "unknown")
            counts[source] = counts.get(source, 0) + 1
        return dict(sorted(counts.items()))

    @classmethod
    def _suggestion_confidence_counts(cls, suggestions: list[dict[str, Any]]) -> dict[str, int]:
        counts: dict[str, int] = {}
        for suggestion in suggestions:
            bucket = cls._confidence_bucket(float(suggestion.get("confidence") or 0.0))
            counts[bucket] = counts.get(bucket, 0) + 1
        return dict(sorted(counts.items()))

    @staticmethod
    def _confidence_bucket(confidence: float) -> str:
        if confidence >= HIGH_CONFIDENCE_THRESHOLD:
            return "high"
        if confidence >= MEDIUM_CONFIDENCE_THRESHOLD:
            return "medium"
        return "low"

    def export_run_provenance(self, project_id: str, run_id: str) -> dict[str, Any]:
        with self.connect() as conn:
            run_row = conn.execute(
                """
                SELECT runs.id, runs.project_id, runs.document_id, documents.filename,
                       runs.recipe, runs.config_json, runs.input_count, runs.suggestion_count, runs.created_at,
                       COALESCE(SUM(CASE WHEN suggestions.status = 'pending' THEN 1 ELSE 0 END), 0) AS pending_count,
                       COALESCE(SUM(CASE WHEN suggestions.status = 'accepted' THEN 1 ELSE 0 END), 0) AS accepted_count,
                       COALESCE(SUM(CASE WHEN suggestions.status = 'rejected' THEN 1 ELSE 0 END), 0) AS rejected_count
                FROM annotation_runs runs
                JOIN documents ON documents.id = runs.document_id
                LEFT JOIN annotation_suggestions suggestions ON suggestions.run_id = runs.id
                WHERE runs.project_id = ? AND runs.id = ?
                GROUP BY runs.id, runs.project_id, runs.document_id, documents.filename,
                         runs.recipe, runs.config_json, runs.input_count, runs.suggestion_count, runs.created_at
                """,
                (project_id, run_id),
            ).fetchone()
            if run_row is None:
                raise NotFoundError("Run not found.")

            suggestion_rows = conn.execute(
                """
                SELECT sg.id, sg.run_id, sg.sentence_id, s.sentence_index, sg.tag_id,
                       tags.name AS tag_name, tags.color AS tag_color,
                       sg.start_token_index, sg.end_token_index, sg.start_char, sg.end_char,
                       sg.text, sg.confidence, sg.source, sg.evidence_text, sg.match_key, sg.evidence_match_key, sg.context_before, sg.context_after, sg.status, sg.created_at,
                       rev.model AS review_model, rev.recommendation AS review_recommendation,
                       rev.confidence AS review_confidence, rev.rationale AS review_rationale,
                       rev.context_sha256 AS review_context_sha256,
                       rev.created_at AS review_created_at
                FROM annotation_suggestions sg
                JOIN sentences s ON s.id = sg.sentence_id
                JOIN documents d ON d.id = s.document_id
                JOIN tags ON tags.id = sg.tag_id AND tags.project_id = d.project_id
                LEFT JOIN annotation_suggestion_reviews rev ON rev.id = (
                    SELECT latest.id
                    FROM annotation_suggestion_reviews latest
                    WHERE latest.suggestion_id = sg.id
                    ORDER BY latest.created_at DESC, latest.id DESC
                    LIMIT 1
                )
                WHERE d.project_id = ? AND sg.run_id = ?
                ORDER BY s.sentence_index, sg.start_token_index, sg.id
                """,
                (project_id, run_id),
            ).fetchall()

        suggestion_decision_events = self._suggestion_decision_events(project_id, [row["id"] for row in suggestion_rows])
        suggestions = [
            self._run_provenance_suggestion(row, suggestion_decision_events.get(row["id"]))
            for row in suggestion_rows
        ]
        status_counts = {"pending": 0, "accepted": 0, "rejected": 0}
        review_counts: dict[str, int] = {}
        source_counts: dict[str, int] = {}
        confidence_counts: dict[str, int] = {}
        for suggestion in suggestions:
            status = suggestion["status"]
            status_counts[status] = status_counts.get(status, 0) + 1
            source = suggestion["source"]
            source_counts[source] = source_counts.get(source, 0) + 1
            confidence_bucket = self._confidence_bucket(float(suggestion["confidence"]))
            confidence_counts[confidence_bucket] = confidence_counts.get(confidence_bucket, 0) + 1
            latest_review = suggestion.get("latest_review")
            if latest_review:
                recommendation = latest_review["recommendation"]
                review_counts[recommendation] = review_counts.get(recommendation, 0) + 1

        run = {
            "id": run_row["id"],
            "project_id": run_row["project_id"],
            "document_id": run_row["document_id"],
            "filename": run_row["filename"],
            "recipe": run_row["recipe"],
            "config": json.loads(run_row["config_json"]),
            "input_count": run_row["input_count"],
            "suggestion_count": run_row["suggestion_count"],
            "pending_count": run_row["pending_count"],
            "accepted_count": run_row["accepted_count"],
            "rejected_count": run_row["rejected_count"],
            "acceptance_rate": self._acceptance_rate(run_row["accepted_count"], run_row["rejected_count"]),
            "source_counts": dict(sorted(source_counts.items())),
            "confidence_counts": dict(sorted(confidence_counts.items())),
            "created_at": run_row["created_at"],
        }
        content_payload = {
            "schema_version": RUN_PROVENANCE_SCHEMA_VERSION,
            "record_type": "run_provenance",
            "project_id": project_id,
            "run": run,
            "status_counts": dict(sorted(status_counts.items())),
            "source_counts": dict(sorted(source_counts.items())),
            "confidence_counts": dict(sorted(confidence_counts.items())),
            "review_counts": dict(sorted(review_counts.items())),
            "suggestions": suggestions,
        }
        return {
            **content_payload,
            "generated_at": self._now(),
            "content_sha256": self._payload_sha256(content_payload),
        }

    def generate_suggestions(
        self,
        project_id: str,
        document_id: str,
        limit_per_sentence: int = 6,
        min_confidence: float = 0.0,
        sentence_id: Optional[str] = None,
    ) -> dict[str, Any]:
        now = self._now()
        run_id = self._new_id("run")
        confidence_floor = max(0.0, min(float(min_confidence), 1.0))
        suggestion_ids: list[str] = []
        suggestion_records: list[dict[str, Any]] = []
        cleared_pending_suggestion_ids: list[str] = []
        with self.connect() as conn:
            document = conn.execute(
                "SELECT id FROM documents WHERE id = ? AND project_id = ?",
                (document_id, project_id),
            ).fetchone()
            if document is None:
                raise NotFoundError("Document not found.")
            if sentence_id is not None:
                sentence = conn.execute(
                    "SELECT id FROM sentences WHERE id = ? AND document_id = ?",
                    (sentence_id, document_id),
                ).fetchone()
                if sentence is None:
                    raise NotFoundError("Sentence not found.")

            tags = self._get_tags(conn, project_id)
            if not tags:
                raise ValidationError("At least one tag is required before generating suggestions.")
            tag_schema_sha256 = self._payload_sha256(self._tag_schema_content_payload(tags))

            project_annotations = conn.execute(
                """
                SELECT a.tag_id, a.text
                FROM annotations a
                JOIN sentences s ON s.id = a.sentence_id
                JOIN documents d ON d.id = s.document_id
                WHERE d.project_id = ?
                """,
                (project_id,),
            ).fetchall()
            examples = build_examples(tags, [self._row_dict(row) for row in project_annotations])
            example_count = sum(len(values) for values in examples.values())
            examples_sha256 = self._payload_sha256(examples)
            examples_match_keys = build_match_keys_by_tag(examples)
            examples_match_key_count = sum(len(values) for values in examples_match_keys.values())
            examples_match_keys_sha256 = self._payload_sha256(examples_match_keys)

            project_rejected_suggestions = conn.execute(
                """
                SELECT sg.tag_id, sg.text
                FROM annotation_suggestions sg
                JOIN sentences s ON s.id = sg.sentence_id
                JOIN documents d ON d.id = s.document_id
                WHERE d.project_id = ? AND sg.status = 'rejected'
                """,
                (project_id,),
            ).fetchall()
            negative_examples = build_negative_examples(tags, [self._row_dict(row) for row in project_rejected_suggestions])
            negative_example_count = sum(len(values) for values in negative_examples.values())
            negative_examples_sha256 = self._payload_sha256(negative_examples)
            negative_examples_match_keys = build_match_keys_by_tag(negative_examples)
            negative_examples_match_key_count = sum(len(values) for values in negative_examples_match_keys.values())
            negative_examples_match_keys_sha256 = self._payload_sha256(negative_examples_match_keys)
            run_config = {
                "limit_per_sentence": limit_per_sentence,
                "min_confidence": confidence_floor,
                "tag_count": len(tags),
                "tag_schema_version": TAG_SCHEMA_VERSION,
                "tag_schema_sha256": tag_schema_sha256,
                "match_normalization": match_normalization_config(),
                "example_count": example_count,
                "examples_sha256": examples_sha256,
                "examples_by_tag": examples,
                "examples_match_key_count": examples_match_key_count,
                "examples_match_keys_sha256": examples_match_keys_sha256,
                "examples_match_keys_by_tag": examples_match_keys,
                "negative_example_count": negative_example_count,
                "negative_examples_sha256": negative_examples_sha256,
                "negative_examples_by_tag": negative_examples,
                "negative_examples_match_key_count": negative_examples_match_key_count,
                "negative_examples_match_keys_sha256": negative_examples_match_keys_sha256,
                "negative_examples_match_keys_by_tag": negative_examples_match_keys,
                "retrieval": CHARACTER_RAG_RETRIEVAL,
                "scope": "sentence" if sentence_id else "document",
                "sentence_id": sentence_id,
            }

            sentence_filter = "s.document_id = ?"
            scoped_params: tuple[Any, ...] = (document_id,)
            pending_filter = "sentence_id IN (SELECT id FROM sentences WHERE document_id = ?)"
            pending_params: tuple[Any, ...] = (document_id,)
            if sentence_id is not None:
                sentence_filter += " AND s.id = ?"
                scoped_params = (document_id, sentence_id)
                pending_filter = "sentence_id = ?"
                pending_params = (sentence_id,)

            sentence_rows = conn.execute(
                f"""
                SELECT id, sentence_index, text, start_char, end_char
                FROM sentences s
                WHERE {sentence_filter}
                ORDER BY sentence_index
                """,
                scoped_params,
            ).fetchall()
            token_rows = conn.execute(
                f"""
                SELECT t.id, t.sentence_id, t.token_index, t.text, t.start_char, t.end_char
                FROM tokens t
                JOIN sentences s ON s.id = t.sentence_id
                WHERE {sentence_filter}
                ORDER BY s.sentence_index, t.token_index
                """,
                scoped_params,
            ).fetchall()
            annotation_rows = conn.execute(
                f"""
                SELECT a.sentence_id, a.start_token_index, a.end_token_index
                FROM annotations a
                JOIN sentences s ON s.id = a.sentence_id
                WHERE {sentence_filter}
                """,
                scoped_params,
            ).fetchall()
            rejected_rows = conn.execute(
                f"""
                SELECT sg.sentence_id, sg.start_token_index, sg.end_token_index
                FROM annotation_suggestions sg
                JOIN sentences s ON s.id = sg.sentence_id
                WHERE {sentence_filter} AND sg.status = 'rejected'
                """,
                scoped_params,
            ).fetchall()

            cleared_pending_suggestion_ids = [
                row["id"]
                for row in conn.execute(
                    """
                    SELECT id
                    FROM annotation_suggestions
                    WHERE status = 'pending'
                      AND {pending_filter}
                    ORDER BY created_at, id
                    """.format(pending_filter=pending_filter),
                    pending_params,
                ).fetchall()
            ]

            conn.execute(
                f"""
                DELETE FROM annotation_suggestions
                WHERE status = 'pending'
                  AND {pending_filter}
                """,
                pending_params,
            )

            conn.execute(
                """
                INSERT INTO annotation_runs (
                  id, project_id, document_id, recipe, config_json, input_count, suggestion_count, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, 0, ?)
                """,
                (
                    run_id,
                    project_id,
                    document_id,
                    "character_rag",
                    json.dumps(run_config, ensure_ascii=False),
                    len(sentence_rows),
                    now,
                ),
            )

            tokens_by_sentence: dict[str, list[dict[str, Any]]] = {}
            for row in token_rows:
                tokens_by_sentence.setdefault(row["sentence_id"], []).append(self._row_dict(row, exclude={"sentence_id"}))

            blocked_by_sentence: dict[str, list[tuple[int, int]]] = {}
            for row in list(annotation_rows) + list(rejected_rows):
                blocked_by_sentence.setdefault(row["sentence_id"], []).append((row["start_token_index"], row["end_token_index"]))

            for sentence in sentence_rows:
                candidates = generate_candidate_spans(
                    tokens_by_sentence.get(sentence["id"], []),
                    examples,
                    blocked_by_sentence.get(sentence["id"], []),
                    limit_per_sentence,
                    confidence_floor,
                    negative_examples,
                )
                for candidate in candidates:
                    suggestion_id = self._new_id("sug")
                    suggestion_ids.append(suggestion_id)
                    context = self._suggestion_context(
                        sentence["text"],
                        sentence["start_char"],
                        candidate.start_char,
                        candidate.end_char,
                    )
                    suggestion_record = {
                        "id": suggestion_id,
                        "run_id": run_id,
                        "sentence_id": sentence["id"],
                        "tag_id": candidate.tag_id,
                        "start_token_index": candidate.start_token_index,
                        "end_token_index": candidate.end_token_index,
                        "start_char": candidate.start_char,
                        "end_char": candidate.end_char,
                        "text": candidate.text,
                        "confidence": candidate.confidence,
                        "source": candidate.source,
                        "evidence_text": candidate.evidence_text,
                        "match_key": candidate.match_key,
                        "evidence_match_key": candidate.evidence_match_key,
                        "context_before": context["context_before"],
                        "context_after": context["context_after"],
                        "status": "pending",
                        "created_at": now,
                    }
                    suggestion_records.append(suggestion_record)
                    conn.execute(
                        """
                        INSERT INTO annotation_suggestions (
                          id, run_id, sentence_id, tag_id, start_token_index, end_token_index,
                          start_char, end_char, text, confidence, source, evidence_text, match_key, evidence_match_key,
                          context_before, context_after, status, created_at
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?)
                        """,
                        (
                            suggestion_record["id"],
                            suggestion_record["run_id"],
                            suggestion_record["sentence_id"],
                            suggestion_record["tag_id"],
                            suggestion_record["start_token_index"],
                            suggestion_record["end_token_index"],
                            suggestion_record["start_char"],
                            suggestion_record["end_char"],
                            suggestion_record["text"],
                            suggestion_record["confidence"],
                            suggestion_record["source"],
                            suggestion_record["evidence_text"],
                            suggestion_record["match_key"],
                            suggestion_record["evidence_match_key"],
                            suggestion_record["context_before"],
                            suggestion_record["context_after"],
                            now,
                        ),
                    )

            source_counts = self._suggestion_source_counts(suggestion_records)
            confidence_counts = self._suggestion_confidence_counts(suggestion_records)
            conn.execute("UPDATE annotation_runs SET suggestion_count = ? WHERE id = ?", (len(suggestion_ids), run_id))

            self._enqueue_event(
                conn,
                project_id,
                {
                    "type": "suggestions.generated",
                    "document_id": document_id,
                    "sentence_id": sentence_id,
                    "run_id": run_id,
                    "recipe": "character_rag",
                    "input_count": len(sentence_rows),
                    "suggestion_count": len(suggestion_ids),
                    "source_counts": source_counts,
                    "confidence_counts": confidence_counts,
                    "config": run_config,
                    "cleared_pending_suggestion_ids": cleared_pending_suggestion_ids,
                    "suggestions": suggestion_records,
                },
            )

        self.flush_event_outbox(project_id)
        return {
            "run_id": run_id,
            "suggestions_created": len(suggestion_ids),
            "source_counts": source_counts,
            "confidence_counts": confidence_counts,
            "suggestions": self.get_suggestions(project_id, suggestion_ids),
        }

    def accept_suggestion(self, project_id: str, suggestion_id: str) -> list[dict[str, Any]]:
        return self.suggestion_decisions.accept_suggestion(project_id, suggestion_id)

    def accept_sentence_suggestions(self, project_id: str, sentence_id: str) -> dict[str, Any]:
        return self.suggestion_decisions.accept_sentence_suggestions(project_id, sentence_id)

    def apply_sentence_suggestion_reviews(self, project_id: str, sentence_id: str) -> dict[str, Any]:
        return self.suggestion_decisions.apply_sentence_suggestion_reviews(project_id, sentence_id)

    def apply_document_suggestion_reviews(self, project_id: str, document_id: str) -> dict[str, Any]:
        return self.suggestion_decisions.apply_document_suggestion_reviews(project_id, document_id)

    def auto_accept_document_suggestions(self, project_id: str, document_id: str, min_confidence: float = 0.9) -> dict[str, Any]:
        return self.suggestion_decisions.auto_accept_document_suggestions(project_id, document_id, min_confidence)

    def auto_annotate_document_suggestions(
        self,
        project_id: str,
        document_id: str,
        limit_per_sentence: int = 6,
        min_confidence: float = 0.9,
    ) -> dict[str, Any]:
        generated = self.generate_suggestions(project_id, document_id, limit_per_sentence, min_confidence)
        accepted = self.auto_accept_document_suggestions(project_id, document_id, min_confidence)
        return {
            "run_id": generated["run_id"],
            "suggestions_created": generated["suggestions_created"],
            "source_counts": generated["source_counts"],
            "confidence_counts": generated["confidence_counts"],
            **accepted,
        }

    def reject_suggestion(self, project_id: str, suggestion_id: str) -> dict[str, Any]:
        return self.suggestion_decisions.reject_suggestion(project_id, suggestion_id)

    def reject_sentence_suggestions(self, project_id: str, sentence_id: str) -> dict[str, Any]:
        return self.suggestion_decisions.reject_sentence_suggestions(project_id, sentence_id)

    def auto_reject_document_suggestions(self, project_id: str, document_id: str) -> dict[str, Any]:
        return self.suggestion_decisions.auto_reject_document_suggestions(project_id, document_id)

    def get_suggestion_review_context(self, project_id: str, suggestion_id: str) -> dict[str, Any]:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT sg.id, sg.tag_id, tags.name AS tag_name, sg.text AS span_text,
                       sg.start_token_index, sg.end_token_index, sg.confidence AS lexical_confidence,
                       sg.source, sg.evidence_text, sg.match_key, sg.evidence_match_key, sg.context_before, sg.context_after,
                       s.id AS sentence_id, s.sentence_index, s.text AS sentence_text,
                       d.id AS document_id, d.filename
                FROM annotation_suggestions sg
                JOIN sentences s ON s.id = sg.sentence_id
                JOIN documents d ON d.id = s.document_id
                JOIN tags ON tags.id = sg.tag_id AND tags.project_id = d.project_id
                WHERE sg.id = ? AND d.project_id = ? AND sg.status = 'pending'
                """,
                (suggestion_id, project_id),
            ).fetchone()
            if row is None:
                raise NotFoundError("Pending suggestion not found.")
            tags = self._get_tags(conn, project_id)
            annotations = conn.execute(
                """
                SELECT a.tag_id, tags.name AS tag_name, a.text
                FROM annotations a
                JOIN tags ON tags.id = a.tag_id AND tags.project_id = ?
                WHERE a.sentence_id = ?
                ORDER BY a.start_token_index
                """,
                (project_id, row["sentence_id"]),
            ).fetchall()
        return {
            "project_id": project_id,
            "document_id": row["document_id"],
            "filename": row["filename"],
            "sentence_id": row["sentence_id"],
            "sentence_index": row["sentence_index"],
            "sentence_text": row["sentence_text"],
            "suggestion": {
                "id": row["id"],
                "text": row["span_text"],
                "tag_id": row["tag_id"],
                "tag_name": row["tag_name"],
                "start_token_index": row["start_token_index"],
                "end_token_index": row["end_token_index"],
                "lexical_confidence": row["lexical_confidence"],
                "source": row["source"],
                "evidence_text": row["evidence_text"],
                "match_key": row["match_key"],
                "evidence_match_key": row["evidence_match_key"],
                "context_before": row["context_before"],
                "context_after": row["context_after"],
                "span_context": f"{row['context_before'] or ''}[{row['span_text']}]{row['context_after'] or ''}",
            },
            "tags": [{"id": tag["id"], "name": tag["name"]} for tag in tags],
            "existing_sentence_annotations": [self._row_dict(annotation) for annotation in annotations],
        }

    def record_suggestion_review(
        self,
        project_id: str,
        suggestion_id: str,
        review: dict[str, Any],
        context_sha256: str | None = None,
    ) -> dict[str, Any]:
        suggestion = self._get_suggestion_row(project_id, suggestion_id)
        review_id = self._new_id("rev")
        now = self._now()
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO annotation_suggestion_reviews (
                  id, suggestion_id, model, recommendation, confidence, rationale, context_sha256, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    review_id,
                    suggestion_id,
                    review["model"],
                    review["recommendation"],
                    review["confidence"],
                    review["rationale"],
                    context_sha256,
                    now,
                ),
            )
            self._enqueue_event(
                conn,
                project_id,
                {
                    "type": "suggestion.llm_reviewed",
                    "suggestion_id": suggestion_id,
                    "sentence_id": suggestion["sentence_id"],
                    "review_id": review_id,
                    "model": review["model"],
                    "recommendation": review["recommendation"],
                    "confidence": review["confidence"],
                    "rationale": review["rationale"],
                    "context_sha256": context_sha256,
                },
            )
        self.flush_event_outbox(project_id)
        return {"suggestion_id": suggestion_id, **review, "context_sha256": context_sha256, "created_at": now}

    def get_suggestions(self, project_id: str, suggestion_ids: list[str]) -> list[dict[str, Any]]:
        if not suggestion_ids:
            return []
        placeholders = ", ".join("?" for _ in suggestion_ids)
        with self.connect() as conn:
            rows = conn.execute(
                f"""
                SELECT sg.id, sg.run_id, sg.sentence_id, sg.tag_id, tags.name AS tag_name, tags.color AS tag_color,
                       sg.start_token_index, sg.end_token_index, sg.start_char, sg.end_char, sg.text,
                       sg.confidence, sg.source, sg.evidence_text, sg.match_key, sg.evidence_match_key, sg.context_before, sg.context_after, sg.status, sg.created_at,
                       rev.model AS review_model, rev.recommendation AS review_recommendation,
                       rev.confidence AS review_confidence, rev.rationale AS review_rationale,
                       rev.context_sha256 AS review_context_sha256,
                       rev.created_at AS review_created_at
                FROM annotation_suggestions sg
                JOIN sentences s ON s.id = sg.sentence_id
                JOIN documents d ON d.id = s.document_id
                JOIN tags ON tags.id = sg.tag_id AND tags.project_id = d.project_id
                LEFT JOIN annotation_suggestion_reviews rev ON rev.id = (
                    SELECT latest.id
                    FROM annotation_suggestion_reviews latest
                    WHERE latest.suggestion_id = sg.id
                    ORDER BY latest.created_at DESC, latest.id DESC
                    LIMIT 1
                )
                WHERE d.project_id = ? AND sg.id IN ({placeholders})
                ORDER BY s.sentence_index, sg.start_token_index
                """,
                (project_id, *suggestion_ids),
            ).fetchall()
        return [self._suggestion_row_dict(row) for row in rows]

    def get_tags(self, project_id: str) -> list[dict[str, Any]]:
        with self.connect() as conn:
            self._seed_tags(conn, project_id)
            return self.tag_queries.list_tags(conn, project_id)

    def get_runtime_setting(self, key: str) -> str | None:
        with self.connect() as conn:
            row = conn.execute("SELECT value FROM runtime_settings WHERE key = ?", (key,)).fetchone()
            return row["value"] if row else None

    def set_runtime_setting(self, key: str, value: str) -> dict[str, Any]:
        now = self._now()
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO runtime_settings (key, value, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                  value = excluded.value,
                  updated_at = excluded.updated_at
                """,
                (key, value, now),
            )
        return {"key": key, "value": value, "updated_at": now}

    def create_tag(
        self,
        project_id: str,
        name: str,
        description: str | None = None,
        examples: list[str] | None = None,
    ) -> dict[str, Any]:
        tag_name = name.strip()
        if not tag_name:
            raise ValidationError("Tag name is required.")
        tag_description = self._normalize_optional_text(description)
        tag_examples = self._normalize_examples(examples)

        tag_id = self._new_id("tag")
        with self.connect() as conn:
            self._seed_tags(conn, project_id)
            existing = self._get_tags(conn, project_id)
            if any(tag["name"].casefold() == tag_name.casefold() for tag in existing):
                raise ValidationError("Tag name already exists.")
            shortcut = self._next_tag_shortcut(existing)
            color = TAG_COLORS[len(existing) % len(TAG_COLORS)]
            conn.execute(
                """
                INSERT INTO tags (id, project_id, name, description, examples_json, shortcut, color)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (tag_id, project_id, tag_name, tag_description, json.dumps(tag_examples, ensure_ascii=False), shortcut, color),
            )
            self._enqueue_event(
                conn,
                project_id,
                {
                    "type": "tag.created",
                    "tag_id": tag_id,
                    "name": tag_name,
                    "description": tag_description,
                    "examples": tag_examples,
                    "shortcut": shortcut,
                    "color": color,
                },
            )

        self.flush_event_outbox(project_id)
        return {
            "id": tag_id,
            "name": tag_name,
            "description": tag_description,
            "examples": tag_examples,
            "shortcut": shortcut,
            "color": color,
            "count": 0,
            "usage_count": 0,
            "suggestion_count": 0,
        }

    def rename_tag(
        self,
        project_id: str,
        tag_id: str,
        name: str | None,
        description: str | None = None,
        examples: list[str] | None = None,
    ) -> dict[str, Any]:
        tag_name = name.strip() if name is not None else None
        tag_description = self._normalize_optional_text(description)
        tag_examples = self._normalize_examples(examples) if examples is not None else None
        if tag_name is None and description is None and examples is None:
            raise ValidationError("Tag name, description, or examples are required.")
        if tag_name is not None and not tag_name:
            raise ValidationError("Tag name is required.")

        with self.connect() as conn:
            self._seed_tags(conn, project_id)
            tag = conn.execute(
                "SELECT id, name, description, examples_json FROM tags WHERE project_id = ? AND id = ?",
                (project_id, tag_id),
            ).fetchone()
            if tag is None:
                raise NotFoundError("Tag not found.")

            existing = self._get_tags(conn, project_id)
            if tag_name is not None and any(
                existing_tag["id"] != tag_id and existing_tag["name"].casefold() == tag_name.casefold() for existing_tag in existing
            ):
                raise ValidationError("Tag name already exists.")

            next_name = tag_name if tag_name is not None else tag["name"]
            next_description = tag_description if description is not None else tag["description"]
            current_examples = self._parse_examples_json(tag["examples_json"])
            next_examples = tag_examples if examples is not None else current_examples
            if next_name == tag["name"] and next_description == tag["description"] and next_examples == current_examples:
                return next(tag_item for tag_item in existing if tag_item["id"] == tag_id)

            conn.execute(
                "UPDATE tags SET name = ?, description = ?, examples_json = ? WHERE project_id = ? AND id = ?",
                (next_name, next_description, json.dumps(next_examples, ensure_ascii=False), project_id, tag_id),
            )
            self._enqueue_event(
                conn,
                project_id,
                {
                    "type": "tag.updated",
                    "tag_id": tag_id,
                    "old_name": tag["name"],
                    "name": next_name,
                    "old_description": tag["description"],
                    "description": next_description,
                    "old_examples": current_examples,
                    "examples": next_examples,
                },
            )
            updated_tag = next(tag_item for tag_item in self._get_tags(conn, project_id) if tag_item["id"] == tag_id)

        self.flush_event_outbox(project_id)
        return updated_tag

    def delete_tag(self, project_id: str, tag_id: str) -> dict[str, Any]:
        with self.connect() as conn:
            tag = conn.execute(
                "SELECT id, name FROM tags WHERE project_id = ? AND id = ?",
                (project_id, tag_id),
            ).fetchone()
            if tag is None:
                raise NotFoundError("Tag not found.")

            annotation_count = conn.execute(
                """
                SELECT COUNT(*) AS count
                FROM annotations a
                JOIN sentences s ON s.id = a.sentence_id
                JOIN documents d ON d.id = s.document_id
                WHERE d.project_id = ? AND a.tag_id = ?
                """,
                (project_id, tag_id),
            ).fetchone()["count"]

            suggestion_count = conn.execute(
                """
                SELECT COUNT(*) AS count
                FROM annotation_suggestions sg
                JOIN sentences s ON s.id = sg.sentence_id
                JOIN documents d ON d.id = s.document_id
                WHERE d.project_id = ? AND sg.tag_id = ?
                """,
                (project_id, tag_id),
            ).fetchone()["count"]

            conn.execute(
                """
                DELETE FROM annotations
                WHERE tag_id = ?
                  AND sentence_id IN (
                    SELECT s.id
                    FROM sentences s
                    JOIN documents d ON d.id = s.document_id
                    WHERE d.project_id = ?
                  )
                """,
                (tag_id, project_id),
            )
            conn.execute(
                """
                DELETE FROM annotation_suggestions
                WHERE tag_id = ?
                  AND sentence_id IN (
                    SELECT s.id
                    FROM sentences s
                    JOIN documents d ON d.id = s.document_id
                    WHERE d.project_id = ?
                  )
                """,
                (tag_id, project_id),
            )
            conn.execute("DELETE FROM tags WHERE project_id = ? AND id = ?", (project_id, tag_id))
            self._enqueue_event(
                conn,
                project_id,
                {
                    "type": "tag.deleted",
                    "tag_id": tag_id,
                    "name": tag["name"],
                    "annotation_count": annotation_count,
                    "suggestion_count": suggestion_count,
                },
            )

        self.flush_event_outbox(project_id)
        return {"deleted": True, "tag_id": tag_id, "annotation_count": annotation_count, "suggestion_count": suggestion_count}

    def append_event(self, project_id: str, payload: dict[str, Any]) -> None:
        with self.connect() as conn:
            self._enqueue_event(conn, project_id, payload)
        self.flush_event_outbox(project_id)

    def flush_event_outbox(self, project_id: str) -> int:
        return self.event_outbox.flush(project_id)

    def _enqueue_event(self, conn: sqlite3.Connection, project_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        return self.event_outbox.enqueue(conn, project_id, payload)

    @classmethod
    def _count_project_runtime_rows(cls, conn: sqlite3.Connection, project_id: str) -> dict[str, int]:
        return {
            "deleted_documents": cls._count_rows(conn, "SELECT COUNT(*) AS count FROM documents WHERE project_id = ?", (project_id,)),
            "deleted_sentences": cls._count_rows(
                conn,
                """
                SELECT COUNT(*) AS count
                FROM sentences s
                JOIN documents d ON d.id = s.document_id
                WHERE d.project_id = ?
                """,
                (project_id,),
            ),
            "deleted_tokens": cls._count_rows(
                conn,
                """
                SELECT COUNT(*) AS count
                FROM tokens t
                JOIN sentences s ON s.id = t.sentence_id
                JOIN documents d ON d.id = s.document_id
                WHERE d.project_id = ?
                """,
                (project_id,),
            ),
            "deleted_annotations": cls._count_rows(
                conn,
                """
                SELECT COUNT(*) AS count
                FROM annotations a
                JOIN sentences s ON s.id = a.sentence_id
                JOIN documents d ON d.id = s.document_id
                WHERE d.project_id = ?
                """,
                (project_id,),
            ),
            "deleted_suggestions": cls._count_rows(
                conn,
                """
                SELECT COUNT(*) AS count
                FROM annotation_suggestions sg
                JOIN sentences s ON s.id = sg.sentence_id
                JOIN documents d ON d.id = s.document_id
                WHERE d.project_id = ?
                """,
                (project_id,),
            ),
            "deleted_suggestion_reviews": cls._count_rows(
                conn,
                """
                SELECT COUNT(*) AS count
                FROM annotation_suggestion_reviews rev
                JOIN annotation_suggestions sg ON sg.id = rev.suggestion_id
                JOIN sentences s ON s.id = sg.sentence_id
                JOIN documents d ON d.id = s.document_id
                WHERE d.project_id = ?
                """,
                (project_id,),
            ),
            "deleted_runs": cls._count_rows(conn, "SELECT COUNT(*) AS count FROM annotation_runs WHERE project_id = ?", (project_id,)),
            "deleted_sessions": cls._count_rows(conn, "SELECT COUNT(*) AS count FROM annotation_sessions WHERE project_id = ?", (project_id,)),
        }

    @staticmethod
    def _count_rows(conn: sqlite3.Connection, query: str, params: tuple[Any, ...]) -> int:
        return int(conn.execute(query, params).fetchone()["count"])

    @staticmethod
    def _clear_project_runtime_rows(conn: sqlite3.Connection, project_id: str) -> None:
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

    def _seed_tags(self, conn: sqlite3.Connection, project_id: str) -> None:
        self._remove_legacy_seeded_tags(conn, project_id)
        existing_count = conn.execute("SELECT COUNT(*) AS count FROM tags WHERE project_id = ?", (project_id,)).fetchone()[
            "count"
        ]
        if existing_count:
            return
        conn.executemany(
            """
            INSERT INTO tags (id, project_id, name, description, examples_json, shortcut, color)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(project_id, id) DO UPDATE SET
              name = excluded.name,
              description = excluded.description,
              examples_json = excluded.examples_json,
              shortcut = excluded.shortcut,
              color = excluded.color
            """,
            [
                (
                    tag["id"],
                    project_id,
                    tag["name"],
                    tag["description"],
                    json.dumps(tag["examples"], ensure_ascii=False),
                    tag["shortcut"],
                    tag["color"],
                )
                for tag in DEFAULT_TAGS
            ],
        )

    def _remove_legacy_seeded_tags(self, conn: sqlite3.Connection, project_id: str) -> None:
        removed_legacy_tag = False
        for tag in LEGACY_SEEDED_TAGS:
            legacy_tag = conn.execute(
                "SELECT 1 FROM tags WHERE project_id = ? AND id = ? AND name = ?",
                (project_id, tag["id"], tag["name"]),
            ).fetchone()
            if legacy_tag is None:
                continue
            conn.execute(
                """
                DELETE FROM annotations
                WHERE tag_id = ?
                  AND sentence_id IN (
                    SELECT s.id
                    FROM sentences s
                    JOIN documents d ON d.id = s.document_id
                    WHERE d.project_id = ?
                  )
                """,
                (tag["id"], project_id),
            )
            conn.execute(
                """
                DELETE FROM annotation_suggestions
                WHERE tag_id = ?
                  AND sentence_id IN (
                    SELECT s.id
                    FROM sentences s
                    JOIN documents d ON d.id = s.document_id
                    WHERE d.project_id = ?
                  )
                """,
                (tag["id"], project_id),
            )
            conn.execute(
                """
                DELETE FROM tags
                WHERE project_id = ?
                  AND id = ?
                  AND name = ?
                """,
                (project_id, tag["id"], tag["name"]),
            )
            removed_legacy_tag = True
        if removed_legacy_tag:
            self._compact_tag_shortcuts_and_colors(conn, project_id)

    def _compact_tag_shortcuts_and_colors(self, conn: sqlite3.Connection, project_id: str) -> None:
        rows = conn.execute(
            """
            SELECT id, shortcut, name
            FROM tags
            WHERE project_id = ?
            ORDER BY
              CASE WHEN shortcut GLOB '[0-9]*' THEN CAST(shortcut AS INTEGER) ELSE 10000 END,
              name,
              id
            """,
            (project_id,),
        ).fetchall()
        for index, row in enumerate(rows):
            conn.execute(
                "UPDATE tags SET shortcut = ?, color = ? WHERE project_id = ? AND id = ?",
                (str(index + 1), TAG_COLORS[index % len(TAG_COLORS)], project_id, row["id"]),
            )

    def _backfill_default_tag_descriptions(self, conn: sqlite3.Connection) -> None:
        conn.executemany(
            """
            UPDATE tags
            SET description = ?
            WHERE id = ? AND (description IS NULL OR TRIM(description) = '')
            """,
            [(tag["description"], tag["id"]) for tag in DEFAULT_TAGS],
        )

    def _backfill_default_tag_examples(self, conn: sqlite3.Connection) -> None:
        conn.executemany(
            """
            UPDATE tags
            SET examples_json = ?
            WHERE id = ? AND (examples_json IS NULL OR TRIM(examples_json) = '')
            """,
            [(json.dumps(tag["examples"], ensure_ascii=False), tag["id"]) for tag in DEFAULT_TAGS],
        )

    def _get_tags(self, conn: sqlite3.Connection, project_id: str) -> list[dict[str, Any]]:
        return self.tag_queries.list_tags(conn, project_id)

    def _get_document_session(self, project_id: str, document_id: str) -> dict[str, Any]:
        with self.connect() as conn:
            return self._get_session(conn, project_id, document_id)

    @staticmethod
    def _get_session(conn: sqlite3.Connection, project_id: str, document_id: str) -> dict[str, Any]:
        row = conn.execute(
            """
            SELECT id, actor_id, current_sentence_index, updated_at
            FROM annotation_sessions
            WHERE project_id = ? AND document_id = ? AND id = ?
            """,
            (project_id, document_id, DEFAULT_SESSION_ID),
        ).fetchone()
        if row is None:
            return {
                "id": DEFAULT_SESSION_ID,
                "actor_id": HUMAN_ACTOR_ID,
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
    def _next_tag_shortcut(tags: list[dict[str, Any]]) -> str:
        used = {tag["shortcut"] for tag in tags}
        next_number = 1
        while str(next_number) in used:
            next_number += 1
        return str(next_number)

    @staticmethod
    def _unique_shortcut(preferred: str | None, used: set[str]) -> str:
        normalized = str(preferred).strip() if preferred is not None else ""
        if normalized and normalized not in used:
            return normalized
        next_number = 1
        while str(next_number) in used:
            next_number += 1
        return str(next_number)

    @staticmethod
    def _normalize_optional_text(value: str | None) -> str | None:
        if value is None:
            return None
        normalized = str(value).strip()
        return normalized or None

    @staticmethod
    def _normalize_examples(values: list[str] | None) -> list[str]:
        if not values:
            return []
        normalized: list[str] = []
        seen: set[str] = set()
        for value in values:
            text = str(value).strip()
            if not text or text in seen:
                continue
            seen.add(text)
            normalized.append(text)
        return normalized[:80]

    @staticmethod
    def _parse_annotation_jsonl(text: str) -> list[tuple[int, dict[str, Any]]]:
        records: list[tuple[int, dict[str, Any]]] = []
        for line_number, line in enumerate(text.splitlines(), start=1):
            stripped = line.strip()
            if not stripped:
                continue
            try:
                record = json.loads(stripped)
            except json.JSONDecodeError as exc:
                raise ValidationError(f"Invalid JSONL at line {line_number}.") from exc
            if not isinstance(record, dict):
                raise ValidationError(f"JSONL line {line_number} must be an object.")
            records.append((line_number, record))
        return records

    @staticmethod
    def _match_import_sentence(
        record: dict[str, Any],
        sentence_by_id: dict[str, dict[str, Any]],
        sentence_by_index: dict[int, dict[str, Any]],
        sentences_by_text: dict[str, list[dict[str, Any]]],
    ) -> dict[str, Any] | None:
        meta = record.get("meta") if isinstance(record.get("meta"), dict) else {}
        candidate_id = record.get("sentence_id") or meta.get("sentence_id")
        if isinstance(candidate_id, str) and candidate_id in sentence_by_id:
            return sentence_by_id[candidate_id]

        candidate_index = record.get("sentence_index", meta.get("sentence_index"))
        try:
            if candidate_index is not None:
                return sentence_by_index.get(int(candidate_index))
        except (TypeError, ValueError):
            pass

        text = record.get("text")
        if isinstance(text, str):
            matches = sentences_by_text.get(text, [])
            if len(matches) == 1:
                return matches[0]
        return None

    @staticmethod
    def _normalize_import_answer(value: Any, has_spans: bool) -> str:
        if value is None or str(value).strip() == "":
            return "accept" if has_spans else "pending"
        normalized = str(value).strip().lower()
        if normalized not in {"accept", "reject", "ignore", "pending"}:
            raise ValidationError("Imported answer must be accept, reject, ignore, or pending.")
        return normalized

    @staticmethod
    def _import_record_source_metadata(record: dict[str, Any]) -> dict[str, Any]:
        metadata: dict[str, Any] = {}
        for field in ("_view_id", "_session_id", "_annotator_id", "_input_hash", "_task_hash"):
            value = record.get(field)
            if value is not None:
                metadata[field] = value
        meta = record.get("meta") if isinstance(record.get("meta"), dict) else {}
        for field in ("session_id", "annotator_id", "source", "source_id"):
            value = meta.get(field)
            if value is not None:
                metadata[f"meta.{field}"] = value
        return metadata

    @classmethod
    def _build_import_annotation_spec(
        cls,
        span: dict[str, Any],
        sentence: dict[str, Any],
        tokens: list[dict[str, Any]],
        document_text: str,
    ) -> dict[str, Any]:
        if not isinstance(span, dict):
            raise ValidationError("Imported span must be an object.")
        label = cls._import_span_label(span)
        start_token_index, end_token_index = cls._import_span_token_range(span, sentence, tokens)
        token_by_index = {token["token_index"]: token for token in tokens}
        start_token = token_by_index[start_token_index]
        end_token = token_by_index[end_token_index]
        start_char = start_token["start_char"]
        end_char = end_token["end_char"]
        return {
            "label": label,
            "start_token_index": start_token_index,
            "end_token_index": end_token_index,
            "start_char": start_char,
            "end_char": end_char,
            "text": document_text[start_char:end_char],
        }

    @staticmethod
    def _import_span_label(span: dict[str, Any]) -> str:
        label = span.get("label") or span.get("label_id")
        if label is None or str(label).strip() == "":
            raise ValidationError("Imported span label is required.")
        return str(label).strip()

    @classmethod
    def _import_span_token_range(
        cls,
        span: dict[str, Any],
        sentence: dict[str, Any],
        tokens: list[dict[str, Any]],
    ) -> tuple[int, int]:
        if not tokens:
            raise ValidationError("Imported span cannot be mapped because the sentence has no tokens.")
        token_by_index = {token["token_index"]: token for token in tokens}
        start_value = span.get("token_start", span.get("start_token_index"))
        end_value = span.get("token_end", span.get("end_token_index"))
        if start_value is not None and end_value is not None:
            start_index = cls._import_int(start_value, "token_start")
            end_index = cls._import_int(end_value, "token_end")
            if start_index > end_index or start_index not in token_by_index or end_index not in token_by_index:
                raise ValidationError("Imported span token range is invalid.")
            return start_index, end_index

        if "start" not in span or "end" not in span:
            raise ValidationError("Imported span must include token range or character offsets.")
        raw_start = cls._import_int(span["start"], "start")
        raw_end = cls._import_int(span["end"], "end")
        if raw_start >= raw_end:
            raise ValidationError("Imported span character range is invalid.")

        sentence_start = sentence["start_char"]
        sentence_end = sentence["end_char"]
        if 0 <= raw_start < raw_end <= len(sentence["text"]):
            start_char = sentence_start + raw_start
            end_char = sentence_start + raw_end
        elif sentence_start <= raw_start < raw_end <= sentence_end:
            start_char = raw_start
            end_char = raw_end
        else:
            raise ValidationError("Imported span character range is outside the matched sentence.")

        overlapping = [token for token in tokens if token["start_char"] < end_char and token["end_char"] > start_char]
        if not overlapping:
            raise ValidationError("Imported span character range does not overlap sentence tokens.")
        return overlapping[0]["token_index"], overlapping[-1]["token_index"]

    @staticmethod
    def _import_int(value: Any, field_name: str) -> int:
        try:
            return int(value)
        except (TypeError, ValueError) as exc:
            raise ValidationError(f"Imported {field_name} must be an integer.") from exc

    @staticmethod
    def _normalize_sentence_answer(completed: bool, answer: str | None) -> str:
        if not completed:
            return "pending"
        normalized = (answer or "accept").strip().lower()
        if normalized not in {"accept", "reject", "ignore"}:
            raise ValidationError("Sentence answer must be accept, reject, or ignore when completed.")
        return normalized

    @classmethod
    def _parse_examples_json(cls, value: str | None) -> list[str]:
        if not value:
            return []
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return []
        if not isinstance(parsed, list):
            return []
        return cls._normalize_examples([str(item) for item in parsed])

    def _validate_tag_schema_import(self, schema: dict[str, Any]) -> list[dict[str, Any]]:
        if schema.get("schema_version") != TAG_SCHEMA_VERSION or schema.get("record_type") != "tag_schema":
            raise ValidationError("Tag schema must be annopilot.tag_schema.v1.")
        raw_tags = schema.get("tags")
        if not isinstance(raw_tags, list) or not raw_tags:
            raise ValidationError("Tag schema must include at least one tag.")

        tags: list[dict[str, Any]] = []
        seen_ids: set[str] = set()
        seen_names: set[str] = set()
        for index, raw_tag in enumerate(raw_tags):
            if not isinstance(raw_tag, dict):
                raise ValidationError(f"Tag schema item {index + 1} must be an object.")
            tag_id = str(raw_tag.get("id", "")).strip()
            name = str(raw_tag.get("name", "")).strip()
            if not tag_id or not name:
                raise ValidationError(f"Tag schema item {index + 1} must include id and name.")
            if tag_id in seen_ids:
                raise ValidationError(f"Duplicate tag id in schema: {tag_id}")
            name_key = name.casefold()
            if name_key in seen_names:
                raise ValidationError(f"Duplicate tag name in schema: {name}")
            seen_ids.add(tag_id)
            seen_names.add(name_key)
            color = str(raw_tag.get("color") or TAG_COLORS[len(tags) % len(TAG_COLORS)]).strip()
            tags.append(
                {
                    "id": tag_id,
                    "name": name,
                    "description": self._normalize_optional_text(raw_tag.get("description")),
                    "examples": self._normalize_examples(raw_tag.get("examples") if isinstance(raw_tag.get("examples"), list) else []),
                    "shortcut": str(raw_tag.get("shortcut") or index + 1).strip(),
                    "color": color or TAG_COLORS[len(tags) % len(TAG_COLORS)],
                }
            )
        return tags

    def _get_suggestion_row(self, project_id: str, suggestion_id: str) -> sqlite3.Row:
        with self.connect() as conn:
            suggestion = conn.execute(
                """
                SELECT sg.id, sg.sentence_id, sg.tag_id, sg.start_token_index, sg.end_token_index, sg.status
                FROM annotation_suggestions sg
                JOIN sentences s ON s.id = sg.sentence_id
                JOIN documents d ON d.id = s.document_id
                WHERE sg.id = ? AND d.project_id = ?
                """,
                (suggestion_id, project_id),
            ).fetchone()
        if suggestion is None:
            raise NotFoundError("Suggestion not found.")
        return suggestion

    @staticmethod
    def _export_token(token: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": token["id"],
            "text": token["text"],
            "index": token["token_index"],
            "start": token["start_char"],
            "end": token["end_char"],
        }

    @staticmethod
    def _suggestion_context(sentence_text: str, sentence_start_char: int, start_char: int, end_char: int) -> dict[str, str]:
        local_start = max(0, min(len(sentence_text), start_char - sentence_start_char))
        local_end = max(local_start, min(len(sentence_text), end_char - sentence_start_char))
        before = sentence_text[max(0, local_start - SUGGESTION_CONTEXT_CHARS) : local_start]
        after = sentence_text[local_end : min(len(sentence_text), local_end + SUGGESTION_CONTEXT_CHARS)]
        return {"context_before": before, "context_after": after}

    @staticmethod
    def _export_span(annotation: dict[str, Any], source: str) -> dict[str, Any]:
        span = {
            "id": annotation["id"],
            "label": annotation["tag_name"],
            "label_id": annotation["tag_id"],
            "text": annotation["text"],
            "start": annotation["start_char"],
            "end": annotation["end_char"],
            "token_start": annotation["start_token_index"],
            "token_end": annotation["end_token_index"],
            "source": source,
        }
        if annotation.get("source_suggestion_id"):
            span["source_suggestion_id"] = annotation["source_suggestion_id"]
        return span

    @classmethod
    def _export_suggestion(cls, suggestion: dict[str, Any]) -> dict[str, Any]:
        return {
            **cls._export_span(suggestion, source="character_rag"),
            "run_id": suggestion.get("run_id"),
            "confidence": suggestion["confidence"],
            "match_source": suggestion["source"],
            "evidence_text": suggestion.get("evidence_text"),
            "match_key": suggestion.get("match_key"),
            "evidence_match_key": suggestion.get("evidence_match_key"),
            "context_before": suggestion.get("context_before"),
            "context_after": suggestion.get("context_after"),
            "status": suggestion["status"],
            "latest_review": suggestion.get("latest_review"),
        }

    @staticmethod
    def _export_prodigy_token(token: dict[str, Any], sentence_text: str, sentence_start_char: int) -> dict[str, Any]:
        local_start = token["start_char"] - sentence_start_char
        local_end = token["end_char"] - sentence_start_char
        return {
            "text": token["text"],
            "start": local_start,
            "end": local_end,
            "id": token["token_index"],
            "ws": local_end < len(sentence_text) and sentence_text[local_end].isspace(),
        }

    @staticmethod
    def _export_prodigy_span(annotation: dict[str, Any], sentence_start_char: int) -> dict[str, Any]:
        return {
            "start": annotation["start_char"] - sentence_start_char,
            "end": annotation["end_char"] - sentence_start_char,
            "token_start": annotation["start_token_index"],
            "token_end": annotation["end_token_index"],
            "label": annotation["tag_name"],
        }

    @staticmethod
    def _export_prodigy_annotation_source(annotation: dict[str, Any]) -> dict[str, Any]:
        source = {
            "annotation_id": annotation["id"],
            "label_id": annotation["tag_id"],
            "source": annotation.get("source", "human"),
        }
        if annotation.get("source_suggestion_id"):
            source["source_suggestion_id"] = annotation["source_suggestion_id"]
        return source

    @staticmethod
    def _export_prodigy_answer(sentence: dict[str, Any]) -> str:
        answer = sentence.get("answer") or ("accept" if sentence.get("completed") else "pending")
        return "ignore" if answer == "pending" else answer

    @classmethod
    def _export_prodigy_session_id(cls, project_id: str, document_id: str, annotations: list[dict[str, Any]]) -> str:
        return f"annopilot-{project_id}-{document_id}-{cls._export_prodigy_annotation_channel(annotations)}"

    @classmethod
    def _export_prodigy_annotator_id(cls, annotations: list[dict[str, Any]]) -> str:
        return f"annopilot-{cls._export_prodigy_annotation_channel(annotations)}"

    @staticmethod
    def _export_prodigy_annotation_channel(annotations: list[dict[str, Any]]) -> str:
        sources = {annotation.get("source", "human") for annotation in annotations}
        if not sources:
            return "unannotated"
        if sources == {"human"}:
            return "human"
        if sources == {"accepted_suggestion"}:
            return "character-rag"
        return "mixed"

    @staticmethod
    def _artifact_summary(
        filename: str,
        schema_version: str,
        lines: list[str],
        content_sha256: str | None = None,
    ) -> dict[str, Any]:
        content = "".join(lines)
        encoded = content.encode("utf-8")
        summary = {
            "filename": filename,
            "schema_version": schema_version,
            "line_count": len(lines),
            "byte_count": len(encoded),
            "sha256": hashlib.sha256(encoded).hexdigest(),
        }
        if content_sha256 is not None:
            summary["content_sha256"] = content_sha256
        return summary

    @staticmethod
    def _tag_schema_payload(project_id: str, tags: list[dict[str, Any]]) -> dict[str, Any]:
        return {**AnnotationStorage._tag_schema_content_payload(tags), "project_id": project_id}

    @staticmethod
    def _tag_schema_content_payload(tags: list[dict[str, Any]]) -> dict[str, Any]:
        schema_tags = [
            {
                "id": tag["id"],
                "name": tag["name"],
                "description": tag.get("description"),
                "examples": tag.get("examples", []),
                "shortcut": tag["shortcut"],
                "color": tag["color"],
            }
            for tag in tags
        ]
        return {
            "schema_version": TAG_SCHEMA_VERSION,
            "record_type": "tag_schema",
            "tag_count": len(schema_tags),
            "retrieval": "character_rag_lexical_examples",
            "tags": schema_tags,
        }

    @staticmethod
    def _payload_sha256(payload: dict[str, Any]) -> str:
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    @staticmethod
    def _ranges_overlap(start_a: int, end_a: int, start_b: int, end_b: int) -> bool:
        return start_a <= end_b and end_a >= start_b

    @staticmethod
    def _acceptance_rate(accepted_count: int, rejected_count: int) -> float | None:
        decided_count = accepted_count + rejected_count
        if decided_count == 0:
            return None
        return accepted_count / decided_count

    @staticmethod
    def _stable_hash(payload: dict[str, Any]) -> int:
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        value = int.from_bytes(hashlib.blake2b(encoded, digest_size=4).digest(), byteorder="big", signed=False)
        if value >= 2**31:
            return value - 2**32
        return value

    @staticmethod
    def _text_sha256(text: str) -> str:
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    @classmethod
    def _event_replay_issue(cls, event: dict[str, Any]) -> str | None:
        if event.get("record_type") != "event" or not event.get("event_id"):
            return "legacy_event"

        event_type = event.get("type")
        if event_type == "document.imported":
            text = event.get("text")
            if not isinstance(text, str):
                return "document_import_missing_text"
            if event.get("text_sha256") != cls._text_sha256(text):
                return "document_import_checksum_mismatch"
            if event.get("snapshot_version") != "annopilot.import_snapshot.v1":
                return "document_import_missing_snapshot_version"
            if not cls._has_import_snapshot(event):
                return "document_import_missing_sentence_snapshot"
        elif event_type == "suggestions.generated":
            suggestions = event.get("suggestions")
            if not isinstance(suggestions, list) or len(suggestions) != event.get("suggestion_count"):
                return "suggestion_run_missing_snapshot"
            required = {"id", "run_id", "sentence_id", "tag_id", "start_token_index", "end_token_index", "text", "confidence", "source", "status"}
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

    @staticmethod
    def _has_import_snapshot(event: dict[str, Any]) -> bool:
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

    @staticmethod
    def _record_replay_issue(
        issue_counts: dict[str, int],
        issue_samples: list[dict[str, Any]],
        *,
        line_number: int,
        event_id: str | None,
        event_type: str | None,
        message: str,
    ) -> None:
        issue_counts[message] = issue_counts.get(message, 0) + 1
        if len(issue_samples) >= 5:
            return
        issue_samples.append(
            {
                "line_number": line_number,
                "event_id": event_id,
                "event_type": event_type,
                "message": message,
            }
        )

    @staticmethod
    def _row_dict(row: sqlite3.Row, exclude: set[str] | None = None) -> dict[str, Any]:
        excluded = exclude or set()
        return {key: row[key] for key in row.keys() if key not in excluded}

    @staticmethod
    def _event_int(value: Any) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return 0

    @staticmethod
    def _decode_txt_payload(data: bytes) -> str:
        if len(data) > MAX_TXT_BYTES:
            raise ValidationError("TXT file is larger than the 10 MB limit.")
        try:
            text = normalize_text(data.decode("utf-8"))
        except UnicodeDecodeError as exc:
            raise ValidationError("TXT file must be valid UTF-8.") from exc
        if not text.strip():
            raise ValidationError("TXT file is empty.")
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
            sentence_id = self._new_id("sent")
            token_records = [
                {
                    "id": self._new_id("tok"),
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

    @classmethod
    def _document_import_snapshot(cls, conn: sqlite3.Connection, project_id: str, document_id: str) -> dict[str, Any]:
        document = conn.execute(
            "SELECT id, filename, text, created_at FROM documents WHERE id = ? AND project_id = ?",
            (document_id, project_id),
        ).fetchone()
        if document is None:
            raise NotFoundError("Document not found.")
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
                    cls._row_dict(token, exclude={"sentence_id"})
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
            "text_sha256": cls._text_sha256(document["text"]),
            "sentence_count": len(sentence_records),
            "token_count": token_count,
            "sentences": sentence_records,
        }

    def _suggestion_row_dict(self, row: sqlite3.Row, exclude: set[str] | None = None) -> dict[str, Any]:
        review_keys = {
            "review_model",
            "review_recommendation",
            "review_confidence",
            "review_rationale",
            "review_context_sha256",
            "review_created_at",
        }
        data = self._row_dict(row, exclude=(exclude or set()) | review_keys)
        if row["review_model"] is not None:
            data["latest_review"] = {
                "model": row["review_model"],
                "recommendation": row["review_recommendation"],
                "confidence": row["review_confidence"],
                "rationale": row["review_rationale"],
                "context_sha256": row["review_context_sha256"],
                "created_at": row["review_created_at"],
            }
        else:
            data["latest_review"] = None
        return data

    def _suggestion_decision_events(self, project_id: str, suggestion_ids: list[str]) -> dict[str, dict[str, Any]]:
        if not suggestion_ids:
            return {}
        tracked_ids = set(suggestion_ids)
        decisions: dict[str, dict[str, Any]] = {}
        for line in self.export_event_lines(project_id):
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if event.get("type") not in {"suggestion.accepted", "suggestion.rejected"}:
                continue
            suggestion_id = event.get("suggestion_id")
            if suggestion_id not in tracked_ids:
                continue
            event_type = str(event["type"])
            decisions[str(suggestion_id)] = {
                "event_id": event.get("event_id"),
                "type": event_type,
                "action": "accept" if event_type == "suggestion.accepted" else "reject",
                "ts": event.get("ts"),
                "sentence_id": event.get("sentence_id"),
                "actor_type": event.get("actor_type"),
                "actor_id": event.get("actor_id"),
            }
        return decisions

    def _run_provenance_suggestion(self, row: sqlite3.Row, decision_event: dict[str, Any] | None = None) -> dict[str, Any]:
        suggestion = self._suggestion_row_dict(row)
        return {
            "id": suggestion["id"],
            "sentence_id": suggestion["sentence_id"],
            "sentence_index": suggestion["sentence_index"],
            "tag_id": suggestion["tag_id"],
            "tag_name": suggestion["tag_name"],
            "tag_color": suggestion["tag_color"],
            "start_token_index": suggestion["start_token_index"],
            "end_token_index": suggestion["end_token_index"],
            "start_char": suggestion["start_char"],
            "end_char": suggestion["end_char"],
            "text": suggestion["text"],
            "confidence": suggestion["confidence"],
            "source": suggestion["source"],
            "evidence_text": suggestion.get("evidence_text"),
            "match_key": suggestion.get("match_key"),
            "evidence_match_key": suggestion.get("evidence_match_key"),
            "context_before": suggestion.get("context_before"),
            "context_after": suggestion.get("context_after"),
            "status": suggestion["status"],
            "decision_event": decision_event,
            "latest_review": suggestion.get("latest_review"),
            "created_at": suggestion["created_at"],
        }

    @staticmethod
    def _new_id(prefix: str) -> str:
        return f"{prefix}_{uuid.uuid4().hex}"

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()
