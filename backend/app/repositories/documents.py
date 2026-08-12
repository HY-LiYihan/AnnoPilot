from __future__ import annotations

import sqlite3
from collections.abc import Callable
from typing import Any

from .tags import TagQueryRepository


HIGH_CONFIDENCE_THRESHOLD = 0.9
MEDIUM_CONFIDENCE_THRESHOLD = 0.75
DEFAULT_SESSION_ID = "annopilot-human"
HUMAN_ACTOR_ID = "annopilot-human"


class DocumentQueryRepository:
    """Read-only document queries for the annotation workspace."""

    def __init__(
        self,
        connect: Callable[[], sqlite3.Connection],
        *,
        not_found_error: type[Exception],
        validation_error: type[Exception],
        default_tags: list[dict[str, Any]] | None = None,
    ) -> None:
        self.connect = connect
        self.not_found_error = not_found_error
        self.validation_error = validation_error
        self.tags = TagQueryRepository(default_tags=default_tags)

    def list_documents(self, project_id: str, limit: int = 50) -> dict[str, Any]:
        safe_limit = max(1, min(int(limit), 100))
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT
                  d.id,
                  d.project_id,
                  d.filename,
                  d.created_at,
                  (SELECT COUNT(*) FROM sentences s WHERE s.document_id = d.id) AS sentence_count,
                  (SELECT COUNT(*) FROM tokens t JOIN sentences s ON s.id = t.sentence_id WHERE s.document_id = d.id) AS token_count,
                  (SELECT COUNT(*) FROM sentences s WHERE s.document_id = d.id AND s.completed = 1) AS completed_count,
                  (SELECT COUNT(*) FROM annotations a JOIN sentences s ON s.id = a.sentence_id WHERE s.document_id = d.id) AS annotation_count,
                  (
                    SELECT COUNT(*)
                    FROM annotation_suggestions sg
                    JOIN sentences s ON s.id = sg.sentence_id
                    WHERE s.document_id = d.id AND sg.status = 'pending'
                      AND NOT EXISTS (
                        SELECT 1
                        FROM annotations a
                        WHERE a.sentence_id = sg.sentence_id
                          AND a.start_token_index <= sg.end_token_index
                          AND a.end_token_index >= sg.start_token_index
                      )
                  ) AS suggestion_count
                FROM documents d
                WHERE d.project_id = ?
                ORDER BY d.created_at DESC, d.id DESC
                LIMIT ?
                """,
                (project_id, safe_limit),
            ).fetchall()
            documents = []
            for row in rows:
                sentence_count = int(row["sentence_count"] or 0)
                completed_count = int(row["completed_count"] or 0)
                session = self._get_session(conn, project_id, row["id"])
                documents.append(
                    {
                        "id": row["id"],
                        "project_id": row["project_id"],
                        "filename": row["filename"],
                        "created_at": row["created_at"],
                        "sentence_count": sentence_count,
                        "token_count": int(row["token_count"] or 0),
                        "completed_count": completed_count,
                        "progress": completed_count / sentence_count if sentence_count else 0,
                        "annotation_count": int(row["annotation_count"] or 0),
                        "suggestion_count": int(row["suggestion_count"] or 0),
                        "current_sentence_index": session["current_sentence_index"],
                        "session_updated_at": session["updated_at"],
                    }
                )
        return {"documents": documents}

    def get_document(self, project_id: str, document_id: str) -> dict[str, Any]:
        summary = self.get_document_summary(project_id, document_id)
        page = self.get_document_sentences(
            project_id,
            document_id,
            offset=0,
            limit=max(summary["metrics"]["sentence_count"], 1),
        )
        return {
            "document": summary["document"],
            "tags": summary["tags"],
            "sentences": page["sentences"],
            "metrics": summary["metrics"],
            "session": summary["session"],
        }

    def get_document_summary(self, project_id: str, document_id: str) -> dict[str, Any]:
        with self.connect() as conn:
            document = conn.execute(
                "SELECT id, project_id, filename, created_at FROM documents WHERE id = ? AND project_id = ?",
                (document_id, project_id),
            ).fetchone()
            if document is None:
                raise self.not_found_error("Document not found.")

            sentence_stats = conn.execute(
                """
                SELECT COUNT(*) AS sentence_count, COALESCE(SUM(completed), 0) AS completed_count
                FROM sentences
                WHERE document_id = ?
                """,
                (document_id,),
            ).fetchone()
            answer_rows = conn.execute(
                """
                SELECT COALESCE(answer, CASE WHEN completed THEN 'accept' ELSE 'pending' END) AS answer, COUNT(*) AS count
                FROM sentences
                WHERE document_id = ?
                GROUP BY COALESCE(answer, CASE WHEN completed THEN 'accept' ELSE 'pending' END)
                """,
                (document_id,),
            ).fetchall()
            token_count = conn.execute(
                """
                SELECT COUNT(*) AS count
                FROM tokens t
                JOIN sentences s ON s.id = t.sentence_id
                WHERE s.document_id = ?
                """,
                (document_id,),
            ).fetchone()["count"]
            annotation_count = conn.execute(
                """
                SELECT COUNT(*) AS count
                FROM annotations a
                JOIN sentences s ON s.id = a.sentence_id
                WHERE s.document_id = ?
                """,
                (document_id,),
            ).fetchone()["count"]
            suggestion_count = conn.execute(
                """
                SELECT COUNT(*) AS count
                FROM annotation_suggestions sg
                JOIN sentences s ON s.id = sg.sentence_id
                WHERE s.document_id = ? AND sg.status = 'pending'
                  AND NOT EXISTS (
                    SELECT 1
                    FROM annotations a
                    WHERE a.sentence_id = sg.sentence_id
                      AND a.start_token_index <= sg.end_token_index
                      AND a.end_token_index >= sg.start_token_index
                  )
                """,
                (document_id,),
            ).fetchone()["count"]
            suggestion_metric_rows = conn.execute(
                """
                SELECT sg.source, sg.confidence
                FROM annotation_suggestions sg
                JOIN sentences s ON s.id = sg.sentence_id
                WHERE s.document_id = ? AND sg.status = 'pending'
                  AND NOT EXISTS (
                    SELECT 1
                    FROM annotations a
                    WHERE a.sentence_id = sg.sentence_id
                      AND a.start_token_index <= sg.end_token_index
                      AND a.end_token_index >= sg.start_token_index
                  )
                """,
                (document_id,),
            ).fetchall()
            suggestion_status_rows = conn.execute(
                """
                SELECT sg.status, COUNT(*) AS count
                FROM annotation_suggestions sg
                JOIN sentences s ON s.id = sg.sentence_id
                WHERE s.document_id = ?
                GROUP BY sg.status
                """,
                (document_id,),
            ).fetchall()
            review_count_rows = conn.execute(
                """
                SELECT rev.recommendation, COUNT(*) AS count
                FROM annotation_suggestions sg
                JOIN sentences s ON s.id = sg.sentence_id
                JOIN annotation_suggestion_reviews rev ON rev.id = (
                    SELECT latest.id
                    FROM annotation_suggestion_reviews latest
                    WHERE latest.suggestion_id = sg.id
                    ORDER BY latest.created_at DESC, latest.id DESC
                    LIMIT 1
                )
                WHERE s.document_id = ?
                GROUP BY rev.recommendation
                """,
                (document_id,),
            ).fetchall()
            review_metric_rows = conn.execute(
                """
                SELECT sg.status, rev.recommendation
                FROM annotation_suggestions sg
                JOIN sentences s ON s.id = sg.sentence_id
                JOIN documents d ON d.id = s.document_id
                JOIN annotation_suggestion_reviews rev ON rev.id = (
                    SELECT latest.id
                    FROM annotation_suggestion_reviews latest
                    WHERE latest.suggestion_id = sg.id
                    ORDER BY latest.created_at DESC, latest.id DESC
                    LIMIT 1
                )
                WHERE d.id = ? AND d.project_id = ?
                  AND sg.status IN ('accepted', 'rejected')
                  AND rev.recommendation IN ('accept', 'reject')
                """,
                (document_id, project_id),
            ).fetchall()
            tag_count_rows = conn.execute(
                """
                SELECT a.tag_id, COUNT(*) AS count
                FROM annotations a
                JOIN sentences s ON s.id = a.sentence_id
                WHERE s.document_id = ?
                GROUP BY a.tag_id
                """,
                (document_id,),
            ).fetchall()
            queue_rows = conn.execute(
                """
                SELECT s.id, s.sentence_index, s.completed, s.answer, COUNT(DISTINCT sg.id) AS suggestion_count
                FROM sentences s
                LEFT JOIN annotation_suggestions sg ON sg.sentence_id = s.id
                  AND sg.status = 'pending'
                  AND NOT EXISTS (
                    SELECT 1
                    FROM annotations a
                    WHERE a.sentence_id = sg.sentence_id
                      AND a.start_token_index <= sg.end_token_index
                      AND a.end_token_index >= sg.start_token_index
                  )
                WHERE s.document_id = ?
                GROUP BY s.id, s.sentence_index, s.completed, s.answer
                ORDER BY s.sentence_index
                """,
                (document_id,),
            ).fetchall()
            tags = self.tags.list_tags(conn, project_id)
            session = self._get_session(conn, project_id, document_id)

        sentence_count = int(sentence_stats["sentence_count"] or 0)
        completed_count = int(sentence_stats["completed_count"] or 0)
        answer_counts = {"accept": 0, "reject": 0, "ignore": 0, "pending": 0}
        for row in answer_rows:
            answer_counts[row["answer"]] = row["count"]
        review_total = len(review_metric_rows)
        review_agreements = sum(
            1
            for row in review_metric_rows
            if (row["status"] == "accepted" and row["recommendation"] == "accept")
            or (row["status"] == "rejected" and row["recommendation"] == "reject")
        )
        tag_counts = {row["tag_id"]: row["count"] for row in tag_count_rows}
        for tag in tags:
            tag["count"] = tag_counts.get(tag["id"], 0)
        visible_suggestion_metrics = [self._row_dict(row) for row in suggestion_metric_rows]
        suggestion_status_counts = {"pending": 0, "accepted": 0, "rejected": 0}
        for row in suggestion_status_rows:
            suggestion_status_counts[row["status"]] = row["count"]
        suggestion_review_counts = {"accept": 0, "reject": 0, "uncertain": 0}
        for row in review_count_rows:
            suggestion_review_counts[row["recommendation"]] = row["count"]
        reviewed_suggestion_count = sum(suggestion_review_counts.values())

        return {
            "document": {
                "id": document["id"],
                "project_id": document["project_id"],
                "filename": document["filename"],
                "created_at": document["created_at"],
                "sentence_count": sentence_count,
                "token_count": token_count,
            },
            "tags": tags,
            "metrics": {
                "sentence_count": sentence_count,
                "completed_count": completed_count,
                "answer_counts": answer_counts,
                "progress": completed_count / sentence_count if sentence_count else 0,
                "annotation_count": annotation_count,
                "suggestion_count": suggestion_count,
                "suggestion_status_counts": suggestion_status_counts,
                "suggestion_source_counts": self._suggestion_source_counts(visible_suggestion_metrics),
                "suggestion_confidence_counts": self._suggestion_confidence_counts(visible_suggestion_metrics),
                "suggestion_review_counts": suggestion_review_counts,
                "reviewed_suggestion_count": reviewed_suggestion_count,
                "accuracy": review_agreements / review_total if review_total else None,
                "accuracy_label": (
                    f"LLM review agreement ({review_agreements}/{review_total})"
                    if review_total
                    else "Waiting for reviewed accept/reject data"
                ),
            },
            "queue": [
                {
                    "id": row["id"],
                    "index": row["sentence_index"],
                    "completed": bool(row["completed"]),
                    "answer": row["answer"] or ("accept" if row["completed"] else "pending"),
                    "suggestion_count": row["suggestion_count"],
                }
                for row in queue_rows
            ],
            "session": session,
        }

    def get_document_sentences(self, project_id: str, document_id: str, offset: int = 0, limit: int = 50) -> dict[str, Any]:
        offset = max(offset, 0)
        limit = max(limit, 1)
        with self.connect() as conn:
            document = conn.execute(
                "SELECT id FROM documents WHERE id = ? AND project_id = ?",
                (document_id, project_id),
            ).fetchone()
            if document is None:
                raise self.not_found_error("Document not found.")
            total = conn.execute("SELECT COUNT(*) AS count FROM sentences WHERE document_id = ?", (document_id,)).fetchone()[
                "count"
            ]
            sentence_rows = conn.execute(
                """
                SELECT id, sentence_index, text, start_char, end_char, completed, answer
                FROM sentences
                WHERE document_id = ?
                ORDER BY sentence_index
                LIMIT ? OFFSET ?
                """,
                (document_id, limit, offset),
            ).fetchall()

            sentence_ids = [row["id"] for row in sentence_rows]
            if not sentence_ids:
                return {"sentences": [], "offset": offset, "limit": limit, "total": total, "has_more": offset < total}

            placeholders = ",".join("?" for _ in sentence_ids)
            token_rows = conn.execute(
                f"""
                SELECT t.id, t.sentence_id, t.token_index, t.text, t.start_char, t.end_char
                FROM tokens t
                JOIN sentences s ON s.id = t.sentence_id
                WHERE t.sentence_id IN ({placeholders})
                ORDER BY s.sentence_index, t.token_index
                """,
                sentence_ids,
            ).fetchall()
            annotation_rows = conn.execute(
                f"""
                SELECT a.id, a.sentence_id, a.tag_id, tags.name AS tag_name, tags.color AS tag_color,
                       a.start_token_index, a.end_token_index, a.start_char, a.end_char, a.text,
                       a.source, a.source_suggestion_id, a.created_at
                FROM annotations a
                JOIN sentences s ON s.id = a.sentence_id
                JOIN documents d ON d.id = s.document_id
                JOIN tags ON tags.id = a.tag_id AND tags.project_id = d.project_id
                WHERE a.sentence_id IN ({placeholders})
                ORDER BY s.sentence_index, a.start_token_index, a.created_at
                """,
                sentence_ids,
            ).fetchall()
            suggestion_rows = conn.execute(
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
                WHERE sg.sentence_id IN ({placeholders}) AND sg.status = 'pending'
                  AND NOT EXISTS (
                    SELECT 1
                    FROM annotations a
                    WHERE a.sentence_id = sg.sentence_id
                      AND a.start_token_index <= sg.end_token_index
                      AND a.end_token_index >= sg.start_token_index
                  )
                ORDER BY s.sentence_index, sg.start_token_index, sg.confidence DESC
                """,
                sentence_ids,
            ).fetchall()

        tokens_by_sentence: dict[str, list[dict[str, Any]]] = {}
        for row in token_rows:
            tokens_by_sentence.setdefault(row["sentence_id"], []).append(self._row_dict(row, exclude={"sentence_id"}))

        annotations_by_sentence: dict[str, list[dict[str, Any]]] = {}
        for row in annotation_rows:
            annotations_by_sentence.setdefault(row["sentence_id"], []).append(self._row_dict(row, exclude={"sentence_id"}))

        suggestions_by_sentence: dict[str, list[dict[str, Any]]] = {}
        for row in suggestion_rows:
            suggestions_by_sentence.setdefault(row["sentence_id"], []).append(self._suggestion_row_dict(row))

        sentences = [
            {
                "id": row["id"],
                "index": row["sentence_index"],
                "text": row["text"],
                "start_char": row["start_char"],
                "end_char": row["end_char"],
                "completed": bool(row["completed"]),
                "answer": row["answer"] or ("accept" if row["completed"] else "pending"),
                "tokens": tokens_by_sentence.get(row["id"], []),
                "annotations": annotations_by_sentence.get(row["id"], []),
                "suggestions": suggestions_by_sentence.get(row["id"], []),
            }
            for row in sentence_rows
        ]
        return {
            "sentences": sentences,
            "offset": offset,
            "limit": limit,
            "total": total,
            "has_more": offset + len(sentences) < total,
        }

    def get_review_queue(self, project_id: str, document_id: str, limit: int = 20, order: str = "position") -> dict[str, Any]:
        safe_limit = max(1, min(int(limit), 100))
        normalized_order = str(order or "position").strip().lower()
        if normalized_order not in {"position", "uncertain", "goldsmith"}:
            raise self.validation_error("Review queue order must be position, uncertain, or goldsmith.")
        if normalized_order == "uncertain":
            order_sql = "MIN(sg.confidence) ASC, s.sentence_index"
            suggestion_order_sql = "s.sentence_index, sg.confidence ASC, sg.start_token_index, sg.id"
        elif normalized_order == "goldsmith":
            order_sql = (
                "((1.0 - MIN(sg.confidence)) * COUNT(DISTINCT sg.id)) DESC, "
                "COUNT(DISTINCT sg.id) DESC, "
                "MIN(sg.confidence) ASC, "
                "s.sentence_index"
            )
            suggestion_order_sql = "s.sentence_index, sg.confidence ASC, sg.start_token_index, sg.id"
        else:
            order_sql = "s.sentence_index"
            suggestion_order_sql = "s.sentence_index, sg.start_token_index, sg.confidence DESC, sg.id"
        with self.connect() as conn:
            document = conn.execute(
                "SELECT id FROM documents WHERE id = ? AND project_id = ?",
                (document_id, project_id),
            ).fetchone()
            if document is None:
                raise self.not_found_error("Document not found.")

            total = conn.execute(
                """
                SELECT COUNT(*) AS count
                FROM (
                  SELECT s.id
                  FROM sentences s
                  JOIN annotation_suggestions sg ON sg.sentence_id = s.id
                  WHERE s.document_id = ? AND s.completed = 0 AND sg.status = 'pending'
                    AND NOT EXISTS (
                      SELECT 1
                      FROM annotations a
                      WHERE a.sentence_id = sg.sentence_id
                        AND a.start_token_index <= sg.end_token_index
                        AND a.end_token_index >= sg.start_token_index
                    )
                  GROUP BY s.id
                ) review_sentences
                """,
                (document_id,),
            ).fetchone()["count"]
            sentence_rows = conn.execute(
                f"""
                SELECT s.id, s.sentence_index, s.text,
                       COUNT(DISTINCT sg.id) AS suggestion_count,
                       MIN(sg.confidence) AS min_confidence,
                       ((1.0 - MIN(sg.confidence)) * COUNT(DISTINCT sg.id)) AS risk_score
                FROM sentences s
                JOIN annotation_suggestions sg ON sg.sentence_id = s.id
                WHERE s.document_id = ? AND s.completed = 0 AND sg.status = 'pending'
                  AND NOT EXISTS (
                    SELECT 1
                    FROM annotations a
                    WHERE a.sentence_id = sg.sentence_id
                      AND a.start_token_index <= sg.end_token_index
                      AND a.end_token_index >= sg.start_token_index
                  )
                GROUP BY s.id, s.sentence_index, s.text
                ORDER BY {order_sql}
                LIMIT ?
                """,
                (document_id, safe_limit),
            ).fetchall()
            sentence_ids = [row["id"] for row in sentence_rows]
            first_suggestion_by_sentence: dict[str, dict[str, Any]] = {}
            if sentence_ids:
                placeholders = ",".join("?" for _ in sentence_ids)
                suggestion_rows = conn.execute(
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
                    WHERE sg.sentence_id IN ({placeholders}) AND sg.status = 'pending'
                      AND NOT EXISTS (
                        SELECT 1
                        FROM annotations a
                        WHERE a.sentence_id = sg.sentence_id
                          AND a.start_token_index <= sg.end_token_index
                          AND a.end_token_index >= sg.start_token_index
                      )
                    ORDER BY {suggestion_order_sql}
                    """,
                    sentence_ids,
                ).fetchall()
                for row in suggestion_rows:
                    first_suggestion_by_sentence.setdefault(row["sentence_id"], self._suggestion_row_dict(row))

        return {
            "items": [
                {
                    "id": row["id"],
                    "index": row["sentence_index"],
                    "text": row["text"],
                    "suggestion_count": row["suggestion_count"],
                    "priority_score": float(row["min_confidence"] or 0),
                    "min_confidence": float(row["min_confidence"] or 0),
                    "risk_score": float(row["risk_score"] or 0),
                    "first_suggestion": first_suggestion_by_sentence.get(row["id"]),
                }
                for row in sentence_rows
            ],
            "total": int(total or 0),
        }

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
    def _row_dict(row: sqlite3.Row, exclude: set[str] | None = None) -> dict[str, Any]:
        excluded = exclude or set()
        return {key: row[key] for key in row.keys() if key not in excluded}

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
