from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from .db.connection import configure_connection, connect_database
from .db.migrations import migrate_database
from .events import EventOutbox, event_replay_issue, has_import_snapshot
from .repositories import DocumentQueryRepository, RunQueryRepository, TagQueryRepository
from .services import (
    AnnotationImportService,
    AnnotationService,
    AuditService,
    DocumentService,
    ExportService,
    ProjectService,
    RuntimeSettingsService,
    SuggestionDecisionService,
    SuggestionService,
    TagService,
)


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
PRODIGY_LABELS_SCHEMA_VERSION = "annopilot.prodigy_labels.v1"
TAG_SCHEMA_VERSION = "annopilot.tag_schema.v1"
RUN_PROVENANCE_SCHEMA_VERSION = "annopilot.run_provenance.v1"
GOLDSMITH_REVIEW_QUEUE_SCHEMA_VERSION = "annopilot.goldsmith_review_queue.v1"
GOLDSMITH_HUMAN_CHOICES_SCHEMA_VERSION = "annopilot.goldsmith_human_choices.v1"
GOLDSMITH_HARD_EXAMPLES_SCHEMA_VERSION = "annopilot.goldsmith_hard_examples.v1"
GOLDSMITH_BOUNDARY_FEEDBACK_SCHEMA_VERSION = "annopilot.goldsmith_boundary_feedback.v1"
GOLDSMITH_CONSISTENCY_SCORES_SCHEMA_VERSION = "annopilot.goldsmith_consistency_scores.v1"
GOLDSMITH_CANDIDATE_RUNS_SCHEMA_VERSION = "rosetta.prodigy_candidate.v1"
GOLDSMITH_RISK_REASONS_SCHEMA_VERSION = "annopilot.goldsmith_risk_reasons.v1"
GOLDSMITH_LABEL_STATISTICS_SCHEMA_VERSION = "annopilot.goldsmith_label_statistics.v1"
GOLDSMITH_CONTRASTIVE_EXAMPLES_SCHEMA_VERSION = "annopilot.goldsmith_contrastive_examples.v1"
GOLDSMITH_REFLECTION_PLANS_SCHEMA_VERSION = "annopilot.goldsmith_reflection_plans.v1"
GOLDSMITH_PROMPT_PACKAGE_SCHEMA_VERSION = "annopilot.goldsmith_prompt_package.v1"
GOLDSMITH_REVIEW_TASKS_SCHEMA_VERSION = "annopilot.goldsmith_review_tasks.v1"
GOLDSMITH_VERIFICATION_REPORT_SCHEMA_VERSION = "annopilot.goldsmith_verification_report.v1"
GOLDSMITH_BOOTSTRAP_REPORT_SCHEMA_VERSION = "annopilot.goldsmith_bootstrap_report.v1"
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
        self.tag_service = TagService(
            self.connect,
            new_id=self._new_id,
            now=self._now,
            enqueue_event=self._enqueue_event,
            flush_event_outbox=self.flush_event_outbox,
            tag_queries=self.tag_queries,
            default_tags=DEFAULT_TAGS,
            legacy_seeded_tags=LEGACY_SEEDED_TAGS,
            tag_colors=TAG_COLORS,
            tag_schema_version=TAG_SCHEMA_VERSION,
            not_found_error=NotFoundError,
            validation_error=ValidationError,
        )
        self.document_service = DocumentService(
            self.connect,
            new_id=self._new_id,
            now=self._now,
            enqueue_event=self._enqueue_event,
            flush_event_outbox=self.flush_event_outbox,
            seed_tags=self.tag_service.seed_tags,
            get_tags=self.get_tags,
            get_document_summary=self.get_document_summary,
            default_session_id=DEFAULT_SESSION_ID,
            human_actor_id=HUMAN_ACTOR_ID,
            max_txt_bytes=MAX_TXT_BYTES,
            not_found_error=NotFoundError,
            validation_error=ValidationError,
        )
        self.run_queries = RunQueryRepository(
            self.connect,
            event_lines=self.export_event_lines,
            now=self._now,
            not_found_error=NotFoundError,
            provenance_schema_version=RUN_PROVENANCE_SCHEMA_VERSION,
            high_confidence_threshold=HIGH_CONFIDENCE_THRESHOLD,
            medium_confidence_threshold=MEDIUM_CONFIDENCE_THRESHOLD,
        )
        self.runtime_settings_service = RuntimeSettingsService(self.connect, now=self._now)
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
        self.project_service = ProjectService(
            self.connect,
            now=self._now,
            enqueue_event=self._enqueue_event,
            flush_event_outbox=self.flush_event_outbox,
            seed_tags=self.tag_service.seed_tags,
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
        self.annotation_import_service = AnnotationImportService(
            self.connect,
            new_id=self._new_id,
            now=self._now,
            enqueue_event=self._enqueue_event,
            flush_event_outbox=self.flush_event_outbox,
            seed_tags=self.tag_service.seed_tags,
            list_tags_from_conn=self.tag_service.list_tags_from_conn,
            get_tags=self.get_tags,
            tag_colors=TAG_COLORS,
            max_jsonl_bytes=MAX_JSONL_BYTES,
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
            prodigy_labels_schema_version=PRODIGY_LABELS_SCHEMA_VERSION,
            tag_schema_version=TAG_SCHEMA_VERSION,
            event_schema_version=EVENT_SCHEMA_VERSION,
            run_provenance_schema_version=RUN_PROVENANCE_SCHEMA_VERSION,
            goldsmith_review_queue_schema_version=GOLDSMITH_REVIEW_QUEUE_SCHEMA_VERSION,
            goldsmith_human_choices_schema_version=GOLDSMITH_HUMAN_CHOICES_SCHEMA_VERSION,
            goldsmith_hard_examples_schema_version=GOLDSMITH_HARD_EXAMPLES_SCHEMA_VERSION,
            goldsmith_boundary_feedback_schema_version=GOLDSMITH_BOUNDARY_FEEDBACK_SCHEMA_VERSION,
            goldsmith_consistency_scores_schema_version=GOLDSMITH_CONSISTENCY_SCORES_SCHEMA_VERSION,
            goldsmith_candidate_runs_schema_version=GOLDSMITH_CANDIDATE_RUNS_SCHEMA_VERSION,
            goldsmith_risk_reasons_schema_version=GOLDSMITH_RISK_REASONS_SCHEMA_VERSION,
            goldsmith_label_statistics_schema_version=GOLDSMITH_LABEL_STATISTICS_SCHEMA_VERSION,
            goldsmith_contrastive_examples_schema_version=GOLDSMITH_CONTRASTIVE_EXAMPLES_SCHEMA_VERSION,
            goldsmith_reflection_plans_schema_version=GOLDSMITH_REFLECTION_PLANS_SCHEMA_VERSION,
            goldsmith_prompt_package_schema_version=GOLDSMITH_PROMPT_PACKAGE_SCHEMA_VERSION,
            goldsmith_review_tasks_schema_version=GOLDSMITH_REVIEW_TASKS_SCHEMA_VERSION,
            goldsmith_verification_report_schema_version=GOLDSMITH_VERIFICATION_REPORT_SCHEMA_VERSION,
            goldsmith_bootstrap_report_schema_version=GOLDSMITH_BOOTSTRAP_REPORT_SCHEMA_VERSION,
            medium_confidence_threshold=MEDIUM_CONFIDENCE_THRESHOLD,
        )

    def initialize(self) -> None:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self.data_root.mkdir(parents=True, exist_ok=True)
        with self.connect() as conn:
            configure_connection(conn, enable_wal=True)
            migrate_database(conn)
            self.tag_service.backfill_default_tag_descriptions(conn)
            self.tag_service.backfill_default_tag_examples(conn)
            self.tag_service.seed_tags(conn, DEFAULT_PROJECT_ID)

    def connect(self) -> sqlite3.Connection:
        return connect_database(self.database_path)

    def import_txt(self, project_id: str, filename: str, data: bytes) -> dict[str, Any]:
        return self.document_service.import_txt(project_id, filename, data)

    def merge_txt(self, project_id: str, document_id: str, filename: str, data: bytes) -> dict[str, Any]:
        return self.document_service.merge_txt(project_id, document_id, filename, data)

    def list_documents(self, project_id: str, limit: int = 50) -> dict[str, Any]:
        return self.document_queries.list_documents(project_id, limit=limit)

    def get_document(self, project_id: str, document_id: str) -> dict[str, Any]:
        return self.document_queries.get_document(project_id, document_id)

    def get_document_summary(self, project_id: str, document_id: str) -> dict[str, Any]:
        return self.document_queries.get_document_summary(project_id, document_id)

    def set_session_cursor(self, project_id: str, document_id: str, current_sentence_index: int) -> dict[str, Any]:
        return self.document_service.set_session_cursor(project_id, document_id, current_sentence_index)

    def get_document_sentences(self, project_id: str, document_id: str, offset: int = 0, limit: int = 50) -> dict[str, Any]:
        return self.document_queries.get_document_sentences(project_id, document_id, offset=offset, limit=limit)

    def get_review_queue(self, project_id: str, document_id: str, limit: int = 20, order: str = "position") -> dict[str, Any]:
        return self.document_queries.get_review_queue(project_id, document_id, limit=limit, order=order)

    def list_sentence_review_suggestion_ids(self, project_id: str, sentence_id: str, limit: int = 20) -> list[str]:
        return self.document_queries.list_sentence_review_suggestion_ids(project_id, sentence_id, limit=limit)

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

    def auto_mark_document_monogloss(self, project_id: str, document_id: str) -> dict[str, Any]:
        return self.annotation_service.auto_mark_document_monogloss(project_id, document_id)

    def get_sentence_annotations(self, project_id: str, sentence_id: str) -> list[dict[str, Any]]:
        return self.annotation_service.get_sentence_annotations(project_id, sentence_id)

    def export_document_lines(self, project_id: str, document_id: str) -> list[str]:
        return self.export_service.export_document_lines(project_id, document_id)

    def export_prodigy_document_lines(self, project_id: str, document_id: str) -> list[str]:
        return self.export_service.export_prodigy_document_lines(project_id, document_id)

    def export_prodigy_spans_document_lines(self, project_id: str, document_id: str) -> list[str]:
        return self.export_service.export_prodigy_spans_document_lines(project_id, document_id)

    def export_prodigy_labels(self, project_id: str) -> dict[str, Any]:
        return self.export_service.export_prodigy_labels(project_id)

    def export_manifest(self, project_id: str, document_id: str) -> dict[str, Any]:
        return self.export_service.export_manifest(project_id, document_id)

    def export_prodigy_bundle_bytes(self, project_id: str, document_id: str) -> bytes:
        return self.export_service.export_prodigy_bundle_bytes(project_id, document_id)

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

    def export_goldsmith_hard_examples_lines(self, project_id: str, document_id: str) -> list[str]:
        return self.export_service.export_goldsmith_hard_examples_lines(project_id, document_id)

    def export_goldsmith_boundary_feedback_lines(self, project_id: str, document_id: str) -> list[str]:
        return self.export_service.export_goldsmith_boundary_feedback_lines(project_id, document_id)

    def export_goldsmith_consistency_scores_lines(self, project_id: str, document_id: str) -> list[str]:
        return self.export_service.export_goldsmith_consistency_scores_lines(project_id, document_id)

    def export_goldsmith_candidate_runs_lines(self, project_id: str, document_id: str) -> list[str]:
        return self.export_service.export_goldsmith_candidate_runs_lines(project_id, document_id)

    def export_goldsmith_risk_reason_lines(self, project_id: str, document_id: str) -> list[str]:
        return self.export_service.export_goldsmith_risk_reason_lines(project_id, document_id)

    def export_goldsmith_label_statistics_lines(self, project_id: str, document_id: str) -> list[str]:
        return self.export_service.export_goldsmith_label_statistics_lines(project_id, document_id)

    def export_goldsmith_contrastive_examples_lines(
        self,
        project_id: str,
        document_id: str,
        similar_k: int = 3,
        boundary_k: int = 1,
    ) -> list[str]:
        return self.export_service.export_goldsmith_contrastive_examples_lines(
            project_id,
            document_id,
            similar_k=similar_k,
            boundary_k=boundary_k,
        )

    def export_goldsmith_reflection_plan_lines(self, project_id: str, document_id: str) -> list[str]:
        return self.export_service.export_goldsmith_reflection_plan_lines(project_id, document_id)

    def export_goldsmith_prompt_package_lines(self, project_id: str, document_id: str) -> list[str]:
        return self.export_service.export_goldsmith_prompt_package_lines(project_id, document_id)

    def export_goldsmith_review_task_lines(self, project_id: str, document_id: str) -> list[str]:
        return self.export_service.export_goldsmith_review_task_lines(project_id, document_id)

    def export_goldsmith_verification_report_lines(self, project_id: str, document_id: str) -> list[str]:
        return self.export_service.export_goldsmith_verification_report_lines(project_id, document_id)

    def export_goldsmith_bootstrap_report_lines(self, project_id: str, document_id: str) -> list[str]:
        return self.export_service.export_goldsmith_bootstrap_report_lines(project_id, document_id)

    def export_tag_schema(self, project_id: str) -> dict[str, Any]:
        return self.tag_service.export_tag_schema(project_id)

    def import_tag_schema(self, project_id: str, schema: dict[str, Any]) -> dict[str, Any]:
        return self.tag_service.import_tag_schema(project_id, schema)

    def import_annotations_jsonl(self, project_id: str, document_id: str, filename: str, data: bytes) -> dict[str, Any]:
        return self.annotation_import_service.import_annotations_jsonl(project_id, document_id, filename, data)

    def reset_project(self, project_id: str) -> dict[str, Any]:
        return self.project_service.reset_project(project_id)

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

    def seed_calibration_suggestions(
        self,
        project_id: str,
        document_id: str,
        candidates: list[dict[str, Any]],
        *,
        preset_id: str | None = None,
    ) -> dict[str, Any]:
        return self.suggestion_service.seed_calibration_suggestions(project_id, document_id, candidates, preset_id=preset_id)

    def accept_suggestion(self, project_id: str, suggestion_id: str) -> list[dict[str, Any]]:
        return self.suggestion_decisions.accept_suggestion(project_id, suggestion_id)

    def accept_sentence_suggestions(self, project_id: str, sentence_id: str) -> dict[str, Any]:
        return self.suggestion_decisions.accept_sentence_suggestions(project_id, sentence_id)

    def apply_sentence_suggestion_reviews(self, project_id: str, sentence_id: str) -> dict[str, Any]:
        return self.suggestion_decisions.apply_sentence_suggestion_reviews(project_id, sentence_id)

    def apply_document_suggestion_reviews(self, project_id: str, document_id: str) -> dict[str, Any]:
        return self.suggestion_decisions.apply_document_suggestion_reviews(project_id, document_id)

    def auto_accept_document_suggestions(
        self,
        project_id: str,
        document_id: str,
        min_confidence: float = 0.9,
        *,
        complete_sentences: bool = False,
        completion_source: str = "auto_accept_suggestions",
    ) -> dict[str, Any]:
        return self.suggestion_decisions.auto_accept_document_suggestions(
            project_id,
            document_id,
            min_confidence,
            complete_sentences=complete_sentences,
            completion_source=completion_source,
        )

    def auto_annotate_document_suggestions(
        self,
        project_id: str,
        document_id: str,
        limit_per_sentence: int = 6,
        min_confidence: float = 0.9,
        *,
        complete_sentences: bool = False,
    ) -> dict[str, Any]:
        generated = self.generate_suggestions(project_id, document_id, limit_per_sentence, min_confidence)
        accepted = self.auto_accept_document_suggestions(
            project_id,
            document_id,
            min_confidence,
            complete_sentences=complete_sentences,
            completion_source="auto_annotate_suggestions",
        )
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
        return self.tag_service.get_tags(project_id)

    def get_runtime_setting(self, key: str) -> str | None:
        return self.runtime_settings_service.get_runtime_setting(key)

    def set_runtime_setting(self, key: str, value: str) -> dict[str, Any]:
        return self.runtime_settings_service.set_runtime_setting(key, value)

    def create_tag(
        self,
        project_id: str,
        name: str,
        description: str | None = None,
        examples: list[str] | None = None,
    ) -> dict[str, Any]:
        return self.tag_service.create_tag(project_id, name, description, examples)

    def rename_tag(
        self,
        project_id: str,
        tag_id: str,
        name: str | None,
        description: str | None = None,
        examples: list[str] | None = None,
    ) -> dict[str, Any]:
        return self.tag_service.rename_tag(project_id, tag_id, name, description, examples)

    def delete_tag(self, project_id: str, tag_id: str) -> dict[str, Any]:
        return self.tag_service.delete_tag(project_id, tag_id)

    def append_event(self, project_id: str, payload: dict[str, Any]) -> None:
        with self.connect() as conn:
            self._enqueue_event(conn, project_id, payload)
        self.flush_event_outbox(project_id)

    def flush_event_outbox(self, project_id: str) -> int:
        return self.event_outbox.flush(project_id)

    def _enqueue_event(self, conn: sqlite3.Connection, project_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        return self.event_outbox.enqueue(conn, project_id, payload)

    def _get_tags(self, conn: sqlite3.Connection, project_id: str) -> list[dict[str, Any]]:
        return self.tag_service.list_tags_from_conn(conn, project_id)

    @staticmethod
    def _payload_sha256(payload: dict[str, Any]) -> str:
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    @staticmethod
    def _ranges_overlap(start_a: int, end_a: int, start_b: int, end_b: int) -> bool:
        return start_a <= end_b and end_a >= start_b

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
    def _new_id(prefix: str) -> str:
        return f"{prefix}_{uuid.uuid4().hex}"

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()
