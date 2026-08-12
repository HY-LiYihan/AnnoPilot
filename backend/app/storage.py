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
from .events import EventOutbox, clear_project_runtime_rows, event_replay_issue, has_import_snapshot
from .repositories import DocumentQueryRepository, RunQueryRepository, TagQueryRepository
from .services import AnnotationService, AuditService, ExportService, SuggestionDecisionService, SuggestionService
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
GOLDSMITH_REVIEW_QUEUE_SCHEMA_VERSION = "annopilot.goldsmith_review_queue.v1"
GOLDSMITH_HUMAN_CHOICES_SCHEMA_VERSION = "annopilot.goldsmith_human_choices.v1"
HIGH_CONFIDENCE_THRESHOLD = 0.9
MEDIUM_CONFIDENCE_THRESHOLD = 0.75
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
        self.run_queries = RunQueryRepository(
            self.connect,
            event_lines=self.export_event_lines,
            now=self._now,
            not_found_error=NotFoundError,
            provenance_schema_version=RUN_PROVENANCE_SCHEMA_VERSION,
            high_confidence_threshold=HIGH_CONFIDENCE_THRESHOLD,
            medium_confidence_threshold=MEDIUM_CONFIDENCE_THRESHOLD,
        )
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
        self.audit_service = AuditService(
            self.connect,
            self.data_root,
            flush_event_outbox=self.flush_event_outbox,
            event_replay_issue=event_replay_issue,
        )
        self.annotation_service = AnnotationService(
            self.connect,
            new_id=self._new_id,
            now=self._now,
            enqueue_event=self._enqueue_event,
            flush_event_outbox=self.flush_event_outbox,
            not_found_error=NotFoundError,
            validation_error=ValidationError,
        )
        self.suggestion_decisions = SuggestionDecisionService(
            self.connect,
            new_id=self._new_id,
            now=self._now,
            enqueue_event=self._enqueue_event,
            flush_event_outbox=self.flush_event_outbox,
            get_sentence_annotations=self.annotation_service.get_sentence_annotations,
            ranges_overlap=self._ranges_overlap,
            not_found_error=NotFoundError,
            validation_error=ValidationError,
        )
        self.suggestion_service = SuggestionService(
            self.connect,
            new_id=self._new_id,
            now=self._now,
            enqueue_event=self._enqueue_event,
            flush_event_outbox=self.flush_event_outbox,
            get_tags=self._get_tags,
            not_found_error=NotFoundError,
            validation_error=ValidationError,
            tag_schema_version=TAG_SCHEMA_VERSION,
            high_confidence_threshold=HIGH_CONFIDENCE_THRESHOLD,
            medium_confidence_threshold=MEDIUM_CONFIDENCE_THRESHOLD,
            suggestion_context_chars=SUGGESTION_CONTEXT_CHARS,
        )
        self.export_service = ExportService(
            get_document=self.get_document,
            get_review_queue=self.get_review_queue,
            get_goldsmith_human_choices=self.document_queries.get_goldsmith_human_choices,
            export_event_lines=self.export_event_lines,
            audit_project=self.audit_project,
            export_tag_schema=self.export_tag_schema,
            list_runs=self.list_runs,
            list_annotation_imports=self.list_annotation_imports,
            export_run_provenance=self.export_run_provenance,
            now=self._now,
            task_schema_version=TASK_SCHEMA_VERSION,
            export_manifest_schema_version=EXPORT_MANIFEST_SCHEMA_VERSION,
            prodigy_export_schema_version=PRODIGY_EXPORT_SCHEMA_VERSION,
            prodigy_spans_export_schema_version=PRODIGY_SPANS_EXPORT_SCHEMA_VERSION,
            tag_schema_version=TAG_SCHEMA_VERSION,
            event_schema_version=EVENT_SCHEMA_VERSION,
            run_provenance_schema_version=RUN_PROVENANCE_SCHEMA_VERSION,
            goldsmith_review_queue_schema_version=GOLDSMITH_REVIEW_QUEUE_SCHEMA_VERSION,
            goldsmith_human_choices_schema_version=GOLDSMITH_HUMAN_CHOICES_SCHEMA_VERSION,
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
        return self.annotation_service.create_annotation(
            project_id,
            sentence_id,
            tag_id,
            start_token_index,
            end_token_index,
            source,
            source_suggestion_id,
        )

    def delete_annotation(self, project_id: str, annotation_id: str) -> None:
        self.annotation_service.delete_annotation(project_id, annotation_id)

    def set_sentence_completed(self, project_id: str, sentence_id: str, completed: bool, answer: str | None = None) -> dict[str, Any]:
        return self.annotation_service.set_sentence_completed(project_id, sentence_id, completed, answer)

    def get_sentence_annotations(self, project_id: str, sentence_id: str) -> list[dict[str, Any]]:
        return self.annotation_service.get_sentence_annotations(project_id, sentence_id)

    def export_document_lines(self, project_id: str, document_id: str) -> list[str]:
        return self.export_service.export_document_lines(project_id, document_id)

    def export_prodigy_document_lines(self, project_id: str, document_id: str) -> list[str]:
        return self.export_service.export_prodigy_document_lines(project_id, document_id)

    def export_prodigy_spans_document_lines(self, project_id: str, document_id: str) -> list[str]:
        return self.export_service.export_prodigy_spans_document_lines(project_id, document_id)

    def export_manifest(self, project_id: str, document_id: str) -> dict[str, Any]:
        return self.export_service.export_manifest(project_id, document_id)

    def export_goldsmith_review_queue_lines(
        self,
        project_id: str,
        document_id: str,
        order: str = "hybrid",
        limit: int = 100,
    ) -> list[str]:
        return self.export_service.export_goldsmith_review_queue_lines(project_id, document_id, order=order, limit=limit)

    def export_goldsmith_human_choices_lines(self, project_id: str, document_id: str) -> list[str]:
        return self.export_service.export_goldsmith_human_choices_lines(project_id, document_id)

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
            clear_project_runtime_rows(conn, project_id)
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
        return self.audit_service.export_event_lines(project_id)

    def audit_project(self, project_id: str) -> dict[str, Any]:
        return self.audit_service.audit_project(project_id)

    def list_annotation_imports(
        self,
        project_id: str,
        document_id: Optional[str] = None,
        limit: int = 5,
    ) -> dict[str, Any]:
        return self.audit_service.list_annotation_imports(project_id, document_id=document_id, limit=limit)

    def list_runs(self, project_id: str, document_id: Optional[str] = None, limit: int = 10) -> list[dict[str, Any]]:
        return self.run_queries.list_runs(project_id, document_id=document_id, limit=limit)

    def export_run_provenance(self, project_id: str, run_id: str) -> dict[str, Any]:
        return self.run_queries.export_run_provenance(project_id, run_id)

    def generate_suggestions(
        self,
        project_id: str,
        document_id: str,
        limit_per_sentence: int = 6,
        min_confidence: float = 0.0,
        sentence_id: Optional[str] = None,
    ) -> dict[str, Any]:
        return self.suggestion_service.generate_suggestions(
            project_id,
            document_id,
            limit_per_sentence,
            min_confidence,
            sentence_id=sentence_id,
        )

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
        return self.suggestion_service.get_suggestion_review_context(project_id, suggestion_id)

    def record_suggestion_review(
        self,
        project_id: str,
        suggestion_id: str,
        review: dict[str, Any],
        context_sha256: str | None = None,
    ) -> dict[str, Any]:
        return self.suggestion_service.record_suggestion_review(project_id, suggestion_id, review, context_sha256=context_sha256)

    def get_suggestions(self, project_id: str, suggestion_ids: list[str]) -> list[dict[str, Any]]:
        return self.suggestion_service.get_suggestions(project_id, suggestion_ids)

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
        clear_project_runtime_rows(conn, project_id)

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
    def _text_sha256(text: str) -> str:
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    @classmethod
    def _event_replay_issue(cls, event: dict[str, Any]) -> str | None:
        return event_replay_issue(event)

    @staticmethod
    def _has_import_snapshot(event: dict[str, Any]) -> bool:
        return has_import_snapshot(event)

    @staticmethod
    def _row_dict(row: sqlite3.Row, exclude: set[str] | None = None) -> dict[str, Any]:
        excluded = exclude or set()
        return {key: row[key] for key in row.keys() if key not in excluded}

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

    @staticmethod
    def _new_id(prefix: str) -> str:
        return f"{prefix}_{uuid.uuid4().hex}"

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()
