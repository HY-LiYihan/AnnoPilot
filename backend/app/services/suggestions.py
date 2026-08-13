from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Callable
from typing import Any

from ..rag import (
    CHARACTER_RAG_RETRIEVAL,
    build_examples,
    build_match_keys_by_tag,
    build_negative_examples,
    generate_candidate_spans,
    match_normalization_config,
)


REVIEW_CONTEXT_SCHEMA_VERSION = "annopilot.suggestion_review_context.v1"
BOUNDARY_FEEDBACK_SCHEMA_VERSION = "annopilot.boundary_feedback.v1"
MAX_REVIEW_CONTEXT_EXAMPLES = 12
MAX_BOUNDARY_FEEDBACK_EXAMPLES = 8


class SuggestionService:
    """Suggestion generation, review context, and suggestion read workflows."""

    def __init__(
        self,
        connect: Callable[[], sqlite3.Connection],
        *,
        new_id: Callable[[str], str],
        now: Callable[[], str],
        enqueue_event: Callable[[sqlite3.Connection, str, dict[str, Any]], dict[str, Any]],
        flush_event_outbox: Callable[[str], int],
        get_tags: Callable[[sqlite3.Connection, str], list[dict[str, Any]]],
        not_found_error: type[Exception],
        validation_error: type[Exception],
        tag_schema_version: str,
        high_confidence_threshold: float,
        medium_confidence_threshold: float,
        suggestion_context_chars: int,
    ) -> None:
        self.connect = connect
        self.new_id = new_id
        self.now = now
        self.enqueue_event = enqueue_event
        self.flush_event_outbox = flush_event_outbox
        self.get_tags = get_tags
        self.not_found_error = not_found_error
        self.validation_error = validation_error
        self.tag_schema_version = tag_schema_version
        self.high_confidence_threshold = high_confidence_threshold
        self.medium_confidence_threshold = medium_confidence_threshold
        self.suggestion_context_chars = suggestion_context_chars

    def generate_suggestions(
        self,
        project_id: str,
        document_id: str,
        limit_per_sentence: int = 6,
        min_confidence: float = 0.0,
        sentence_id: str | None = None,
    ) -> dict[str, Any]:
        now = self.now()
        run_id = self.new_id("run")
        confidence_floor = max(0.0, min(float(min_confidence), 1.0))
        suggestion_ids: list[str] = []
        suggestion_records: list[dict[str, Any]] = []

        with self.connect() as conn:
            document = conn.execute(
                "SELECT id FROM documents WHERE id = ? AND project_id = ?",
                (document_id, project_id),
            ).fetchone()
            if document is None:
                raise self.not_found_error("Document not found.")
            if sentence_id is not None:
                sentence = conn.execute(
                    "SELECT id FROM sentences WHERE id = ? AND document_id = ?",
                    (sentence_id, document_id),
                ).fetchone()
                if sentence is None:
                    raise self.not_found_error("Sentence not found.")

            tags = self.get_tags(conn, project_id)
            if not tags:
                raise self.validation_error("At least one tag is required before generating suggestions.")
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
                SELECT sg.tag_id, sg.text,
                       CASE
                         WHEN sg.status = 'rejected' THEN 'human_rejected'
                         ELSE 'llm_rejected'
                       END AS negative_source
                FROM annotation_suggestions sg
                JOIN sentences s ON s.id = sg.sentence_id
                JOIN documents d ON d.id = s.document_id
                LEFT JOIN annotation_suggestion_reviews rev ON rev.id = (
                    SELECT latest.id
                    FROM annotation_suggestion_reviews latest
                    WHERE latest.suggestion_id = sg.id
                    ORDER BY latest.created_at DESC, latest.id DESC
                    LIMIT 1
                )
                WHERE d.project_id = ?
                  AND (
                    sg.status = 'rejected'
                    OR (sg.status = 'pending' AND rev.recommendation = 'reject')
                  )
                """,
                (project_id,),
            ).fetchall()
            negative_examples = build_negative_examples(tags, [self._row_dict(row) for row in project_rejected_suggestions])
            negative_example_count = sum(len(values) for values in negative_examples.values())
            negative_example_source_counts = self._negative_example_source_counts(project_rejected_suggestions)
            negative_examples_sha256 = self._payload_sha256(negative_examples)
            negative_examples_match_keys = build_match_keys_by_tag(negative_examples)
            negative_examples_match_key_count = sum(len(values) for values in negative_examples_match_keys.values())
            negative_examples_match_keys_sha256 = self._payload_sha256(negative_examples_match_keys)
            run_config = {
                "limit_per_sentence": limit_per_sentence,
                "min_confidence": confidence_floor,
                "tag_count": len(tags),
                "tag_schema_version": self.tag_schema_version,
                "tag_schema_sha256": tag_schema_sha256,
                "match_normalization": match_normalization_config(),
                "example_count": example_count,
                "examples_sha256": examples_sha256,
                "examples_by_tag": examples,
                "examples_match_key_count": examples_match_key_count,
                "examples_match_keys_sha256": examples_match_keys_sha256,
                "examples_match_keys_by_tag": examples_match_keys,
                "negative_example_count": negative_example_count,
                "negative_example_policy": "human_rejected_or_latest_llm_reject",
                "negative_example_source_counts": negative_example_source_counts,
                "negative_examples_sha256": negative_examples_sha256,
                "negative_examples_by_tag": negative_examples,
                "negative_examples_match_key_count": negative_examples_match_key_count,
                "negative_examples_match_keys_sha256": negative_examples_match_keys_sha256,
                "negative_examples_match_keys_by_tag": negative_examples_match_keys,
                "retrieval": CHARACTER_RAG_RETRIEVAL,
                "pending_suggestion_clear_policy": "clear_unreviewed_pending_preserve_llm_reviewed",
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
            blocked_suggestion_rows = conn.execute(
                f"""
                SELECT sg.sentence_id, sg.start_token_index, sg.end_token_index
                FROM annotation_suggestions sg
                JOIN sentences s ON s.id = sg.sentence_id
                LEFT JOIN annotation_suggestion_reviews rev ON rev.id = (
                    SELECT latest.id
                    FROM annotation_suggestion_reviews latest
                    WHERE latest.suggestion_id = sg.id
                    ORDER BY latest.created_at DESC, latest.id DESC
                    LIMIT 1
                )
                WHERE {sentence_filter}
                  AND (
                    sg.status = 'rejected'
                    OR (sg.status = 'pending' AND rev.recommendation IS NOT NULL)
                  )
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
                      AND NOT EXISTS (
                        SELECT 1
                        FROM annotation_suggestion_reviews rev
                        WHERE rev.suggestion_id = annotation_suggestions.id
                      )
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
                  AND NOT EXISTS (
                    SELECT 1
                    FROM annotation_suggestion_reviews rev
                    WHERE rev.suggestion_id = annotation_suggestions.id
                  )
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
            for row in list(annotation_rows) + list(blocked_suggestion_rows):
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
                    suggestion_id = self.new_id("sug")
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

            self.enqueue_event(
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

    def seed_calibration_suggestions(
        self,
        project_id: str,
        document_id: str,
        candidates: list[dict[str, Any]],
        *,
        preset_id: str | None = None,
    ) -> dict[str, Any]:
        now = self.now()
        run_id = self.new_id("run")
        suggestion_ids: list[str] = []
        suggestion_records: list[dict[str, Any]] = []
        run_config = {
            "preset_id": preset_id,
            "recipe": "goldsmith_rosetta_calibration",
            "candidate_count": len(candidates),
            "source": "calibration_seed",
            "purpose": "Seed overlapping bilingual Engagement candidates for Goldsmith/Rosetta review calibration.",
            "scope": "document",
        }

        with self.connect() as conn:
            document = conn.execute(
                "SELECT id FROM documents WHERE id = ? AND project_id = ?",
                (document_id, project_id),
            ).fetchone()
            if document is None:
                raise self.not_found_error("Document not found.")

            tags = self.get_tags(conn, project_id)
            tag_ids = {tag["id"] for tag in tags}
            sentence_rows = conn.execute(
                """
                SELECT id, sentence_index, text, start_char, end_char
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
            tokens_by_sentence: dict[str, list[sqlite3.Row]] = {}
            for token in token_rows:
                tokens_by_sentence.setdefault(token["sentence_id"], []).append(token)

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
                    "goldsmith_rosetta_calibration",
                    json.dumps(run_config, ensure_ascii=False),
                    len(sentence_rows),
                    now,
                ),
            )

            for candidate in candidates:
                tag_id = str(candidate.get("tag_id") or "")
                if tag_id not in tag_ids:
                    raise self.validation_error(f"Calibration candidate tag not found: {tag_id}")
                sentence = self._calibration_sentence(sentence_rows, str(candidate.get("sentence_contains") or ""))
                text = str(candidate.get("text") or "").strip()
                if not text:
                    raise self.validation_error("Calibration candidate text is required.")
                local_start = str(sentence["text"]).find(text)
                if local_start < 0:
                    raise self.validation_error(f"Calibration candidate span not found in sentence: {text}")
                local_end = local_start + len(text)
                start_char = int(sentence["start_char"]) + local_start
                end_char = int(sentence["start_char"]) + local_end
                span_tokens = [
                    token
                    for token in tokens_by_sentence.get(sentence["id"], [])
                    if int(token["start_char"]) < end_char and int(token["end_char"]) > start_char
                ]
                if not span_tokens:
                    raise self.validation_error(f"Calibration candidate does not overlap tokens: {text}")

                confidence = max(0.0, min(float(candidate.get("confidence") or 0.0), 1.0))
                evidence_text = str(candidate.get("evidence_text") or text)
                context = self._suggestion_context(sentence["text"], sentence["start_char"], start_char, end_char)
                suggestion_id = self.new_id("sug")
                suggestion_record = {
                    "id": suggestion_id,
                    "run_id": run_id,
                    "sentence_id": sentence["id"],
                    "tag_id": tag_id,
                    "start_token_index": int(span_tokens[0]["token_index"]),
                    "end_token_index": int(span_tokens[-1]["token_index"]),
                    "start_char": start_char,
                    "end_char": end_char,
                    "text": text,
                    "confidence": confidence,
                    "source": "calibration_seed",
                    "evidence_text": evidence_text,
                    "match_key": self._normalize_match_text(text),
                    "evidence_match_key": self._normalize_match_text(evidence_text),
                    "context_before": context["context_before"],
                    "context_after": context["context_after"],
                    "status": "pending",
                    "created_at": now,
                }
                suggestion_ids.append(suggestion_id)
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
            self.enqueue_event(
                conn,
                project_id,
                {
                    "type": "suggestions.generated",
                    "document_id": document_id,
                    "sentence_id": None,
                    "run_id": run_id,
                    "recipe": "goldsmith_rosetta_calibration",
                    "input_count": len(sentence_rows),
                    "suggestion_count": len(suggestion_ids),
                    "source_counts": source_counts,
                    "confidence_counts": confidence_counts,
                    "config": run_config,
                    "cleared_pending_suggestion_ids": [],
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
                raise self.not_found_error("Pending suggestion not found.")
            tags = self.get_tags(conn, project_id)
            review_tags = [self._review_tag_context(tag) for tag in tags]
            candidate_tag = next((tag for tag in review_tags if tag["id"] == row["tag_id"]), None)
            boundary_feedback = self._boundary_feedback(conn, project_id, row["tag_id"], suggestion_id)
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
            "review_guidance": self._review_guidance(review_tags),
            "tag_schema": {
                "schema_version": self.tag_schema_version,
                "record_type": "tag_schema_context",
                "tag_count": len(review_tags),
                "tags": review_tags,
            },
            "boundary_feedback": boundary_feedback,
            "suggestion": {
                "id": row["id"],
                "text": row["span_text"],
                "tag_id": row["tag_id"],
                "tag_name": row["tag_name"],
                "tag_description": candidate_tag["description"] if candidate_tag else None,
                "tag_examples": candidate_tag["examples"] if candidate_tag else [],
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
            "tags": review_tags,
            "existing_sentence_annotations": [self._row_dict(annotation) for annotation in annotations],
        }

    def _boundary_feedback(
        self,
        conn: sqlite3.Connection,
        project_id: str,
        target_tag_id: str,
        current_suggestion_id: str,
    ) -> dict[str, Any]:
        rows = conn.execute(
            """
            SELECT sg.id, sg.run_id, sg.sentence_id, s.sentence_index, s.text AS sentence_text,
                   sg.tag_id, tags.name AS tag_name, sg.start_token_index, sg.end_token_index,
                   sg.start_char, sg.end_char, sg.text, sg.confidence, sg.source, sg.evidence_text,
                   sg.match_key, sg.evidence_match_key, sg.context_before, sg.context_after,
                   sg.status, sg.created_at,
                   rev.model AS review_model, rev.recommendation AS review_recommendation,
                   rev.confidence AS review_confidence, rev.rationale AS review_rationale,
                   rev.context_sha256 AS review_context_sha256, rev.judge_json AS review_judge_json,
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
            WHERE d.project_id = ?
              AND sg.tag_id = ?
              AND sg.id != ?
              AND (
                sg.status IN ('accepted', 'rejected')
                OR (sg.status = 'pending' AND rev.recommendation IN ('reject', 'uncertain'))
              )
            ORDER BY sg.created_at DESC, sg.id DESC
            LIMIT ?
            """,
            (project_id, target_tag_id, current_suggestion_id, MAX_BOUNDARY_FEEDBACK_EXAMPLES * 2),
        ).fetchall()

        negative_examples = []
        hard_examples = []
        for row in rows:
            example = self._boundary_feedback_example(row)
            latest_review = example.get("latest_review") or {}
            if (
                row["status"] == "rejected" or latest_review.get("recommendation") == "reject"
            ) and len(negative_examples) < MAX_BOUNDARY_FEEDBACK_EXAMPLES:
                negative_examples.append(example)
            if example["hard_example_reasons"] and len(hard_examples) < MAX_BOUNDARY_FEEDBACK_EXAMPLES:
                hard_examples.append(example)

        return {
            "schema_version": BOUNDARY_FEEDBACK_SCHEMA_VERSION,
            "record_type": "boundary_feedback",
            "target_tag_id": target_tag_id,
            "negative_example_count": len(negative_examples),
            "hard_example_count": len(hard_examples),
            "negative_examples": negative_examples,
            "hard_examples": hard_examples,
        }

    def record_suggestion_review(
        self,
        project_id: str,
        suggestion_id: str,
        review: dict[str, Any],
        context_sha256: str | None = None,
    ) -> dict[str, Any]:
        suggestion = self._get_suggestion_row(project_id, suggestion_id)
        review_id = self.new_id("rev")
        now = self.now()
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO annotation_suggestion_reviews (
                  id, suggestion_id, model, recommendation, confidence, rationale, context_sha256, judge_json, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    review_id,
                    suggestion_id,
                    review["model"],
                    review["recommendation"],
                    review["confidence"],
                    review["rationale"],
                    context_sha256,
                    json.dumps(review.get("judge"), ensure_ascii=False) if review.get("judge") else None,
                    now,
                ),
            )
            self.enqueue_event(
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
                    "judge": review.get("judge"),
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
                       rev.context_sha256 AS review_context_sha256, rev.judge_json AS review_judge_json,
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
            raise self.not_found_error("Suggestion not found.")
        return suggestion

    def _suggestion_context(self, sentence_text: str, sentence_start_char: int, start_char: int, end_char: int) -> dict[str, str]:
        local_start = max(0, min(len(sentence_text), start_char - sentence_start_char))
        local_end = max(local_start, min(len(sentence_text), end_char - sentence_start_char))
        before = sentence_text[max(0, local_start - self.suggestion_context_chars) : local_start]
        after = sentence_text[local_end : min(len(sentence_text), local_end + self.suggestion_context_chars)]
        return {"context_before": before, "context_after": after}

    def _calibration_sentence(self, sentence_rows: list[sqlite3.Row], sentence_contains: str) -> sqlite3.Row:
        if not sentence_contains:
            raise self.validation_error("Calibration candidate sentence anchor is required.")
        matches = [sentence for sentence in sentence_rows if sentence_contains in str(sentence["text"])]
        if not matches:
            raise self.validation_error(f"Calibration sentence anchor not found: {sentence_contains}")
        if len(matches) > 1:
            raise self.validation_error(f"Calibration sentence anchor is ambiguous: {sentence_contains}")
        return matches[0]

    @staticmethod
    def _normalize_match_text(value: str) -> str:
        return " ".join(str(value).strip().split()).casefold()

    def _tag_schema_content_payload(self, tags: list[dict[str, Any]]) -> dict[str, Any]:
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
            "schema_version": self.tag_schema_version,
            "record_type": "tag_schema",
            "tag_count": len(schema_tags),
            "retrieval": "character_rag_lexical_examples",
            "tags": schema_tags,
        }

    def _review_guidance(self, tags: list[dict[str, Any]]) -> dict[str, Any]:
        guidance: dict[str, Any] = {
            "schema_version": REVIEW_CONTEXT_SCHEMA_VERSION,
            "task": "span_label_review",
            "domain": "appraisal_engagement" if self._is_appraisal_engagement_schema(tags) else "generic_span_annotation",
            "annotation_unit": "contiguous token span with document-level character offsets",
            "decision_policy": [
                "accept when the bracketed span is a minimal, sufficient realization of the suggested label",
                "reject when the span belongs to another label, is only a fragment of a multiword cue, or includes unrelated context",
                "return uncertain when the lexical cue is plausible but sentence context or project guideline boundaries are ambiguous",
            ],
        }
        if guidance["domain"] == "appraisal_engagement":
            guidance["framework"] = {
                "name": "Appraisal Theory: Engagement",
                "goal": "identify how the author opens or contracts dialogic space through attribution, modality, denial, countering, endorsement, or emphasis",
                "boundary_rules": [
                    "prefer the cue span itself over the whole clause unless the label definition requires a larger assertion",
                    "distinguish acknowledge attribution from distancing attribution by whether the author signals skepticism or non-commitment",
                    "distinguish disclaim deny from disclaim counter by whether the span directly negates or pivots against an expected position",
                    "treat Monogloss as direct unmodalized assertion; lexical suggestion usually needs human judgment for this label",
                ],
            }
        return guidance

    def _boundary_feedback_example(self, row: sqlite3.Row) -> dict[str, Any]:
        latest_review = self._latest_review_from_row(row)
        human_decision = self._human_decision_from_suggestion_status(row["status"])
        reasons = self._boundary_feedback_reasons(row, latest_review)
        return {
            "suggestion_id": row["id"],
            "run_id": row["run_id"],
            "sentence_id": row["sentence_id"],
            "sentence_index": row["sentence_index"],
            "text": row["text"],
            "sentence_text": row["sentence_text"],
            "tag_id": row["tag_id"],
            "tag_name": row["tag_name"],
            "human_decision": human_decision,
            "status": row["status"],
            "confidence": row["confidence"],
            "match_key": row["match_key"],
            "evidence_text": row["evidence_text"],
            "span_context": f"{row['context_before'] or ''}[{row['text']}]{row['context_after'] or ''}",
            "latest_review": latest_review,
            "hard_example_reasons": reasons,
        }

    def _boundary_feedback_reasons(self, row: sqlite3.Row, latest_review: dict[str, Any] | None) -> list[str]:
        reasons: list[str] = []
        human_decision = self._human_decision_from_suggestion_status(row["status"])
        review_recommendation = latest_review.get("recommendation") if latest_review else None
        if human_decision and review_recommendation in {"accept", "reject"} and review_recommendation != human_decision:
            reasons.append("llm_human_disagreement")
        if row["status"] == "rejected":
            reasons.append("human_rejected_suggestion")
        if row["status"] == "pending" and review_recommendation == "reject":
            reasons.append("llm_rejected_pending_suggestion")
        if float(row["confidence"] or 0.0) < self.medium_confidence_threshold:
            reasons.append("low_character_rag_confidence")
        if review_recommendation == "uncertain":
            reasons.append("llm_uncertain")
        return reasons

    @staticmethod
    def _human_decision_from_suggestion_status(status: str) -> str | None:
        if status == "accepted":
            return "accept"
        if status == "rejected":
            return "reject"
        return None

    @staticmethod
    def _latest_review_from_row(row: sqlite3.Row) -> dict[str, Any] | None:
        if row["review_model"] is None:
            return None
        return {
            "model": row["review_model"],
            "recommendation": row["review_recommendation"],
            "confidence": row["review_confidence"],
            "rationale": row["review_rationale"],
            "context_sha256": row["review_context_sha256"],
            "judge": SuggestionService._decode_json_object(row["review_judge_json"]),
            "created_at": row["review_created_at"],
        }

    @staticmethod
    def _is_appraisal_engagement_schema(tags: list[dict[str, Any]]) -> bool:
        engagement_tags = [tag for tag in tags if str(tag.get("id") or "").startswith("engagement_")]
        return len(engagement_tags) >= 4

    @staticmethod
    def _review_tag_context(tag: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": tag["id"],
            "name": tag["name"],
            "description": tag.get("description"),
            "examples": list(tag.get("examples") or [])[:MAX_REVIEW_CONTEXT_EXAMPLES],
            "shortcut": tag.get("shortcut"),
            "color": tag.get("color"),
        }

    @staticmethod
    def _suggestion_source_counts(suggestions: list[dict[str, Any]]) -> dict[str, int]:
        counts: dict[str, int] = {}
        for suggestion in suggestions:
            source = str(suggestion.get("source") or "unknown")
            counts[source] = counts.get(source, 0) + 1
        return dict(sorted(counts.items()))

    @staticmethod
    def _negative_example_source_counts(rows: list[sqlite3.Row]) -> dict[str, int]:
        counts: dict[str, int] = {}
        for row in rows:
            source = str(row["negative_source"] or "unknown")
            counts[source] = counts.get(source, 0) + 1
        return dict(sorted(counts.items()))

    def _suggestion_confidence_counts(self, suggestions: list[dict[str, Any]]) -> dict[str, int]:
        counts: dict[str, int] = {}
        for suggestion in suggestions:
            bucket = self._confidence_bucket(float(suggestion.get("confidence") or 0.0))
            counts[bucket] = counts.get(bucket, 0) + 1
        return dict(sorted(counts.items()))

    def _confidence_bucket(self, confidence: float) -> str:
        if confidence >= self.high_confidence_threshold:
            return "high"
        if confidence >= self.medium_confidence_threshold:
            return "medium"
        return "low"

    @staticmethod
    def _payload_sha256(payload: dict[str, Any]) -> str:
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    @staticmethod
    def _row_dict(row: sqlite3.Row, exclude: set[str] | None = None) -> dict[str, Any]:
        excluded = exclude or set()
        return {key: row[key] for key in row.keys() if key not in excluded}

    @staticmethod
    def _decode_json_object(value: str | None) -> dict[str, Any] | None:
        if not value:
            return None
        try:
            payload = json.loads(value)
        except json.JSONDecodeError:
            return None
        return payload if isinstance(payload, dict) else None

    def _suggestion_row_dict(self, row: sqlite3.Row, exclude: set[str] | None = None) -> dict[str, Any]:
        review_keys = {
            "review_model",
            "review_recommendation",
            "review_confidence",
            "review_rationale",
            "review_context_sha256",
            "review_judge_json",
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
                "judge": self._decode_json_object(row["review_judge_json"]),
                "created_at": row["review_created_at"],
            }
        else:
            data["latest_review"] = None
        return data
