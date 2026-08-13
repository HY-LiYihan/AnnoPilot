from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from typing import Any

from ..db.connection import judge_review_risk_score
from .tags import TagQueryRepository


HIGH_CONFIDENCE_THRESHOLD = 0.9
MEDIUM_CONFIDENCE_THRESHOLD = 0.75
LLM_REVIEW_REJECT_RISK_WEIGHT = 1.0
LLM_REVIEW_UNCERTAIN_RISK_WEIGHT = 0.6
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
            suggestion_tag_count_rows = conn.execute(
                """
                SELECT sg.tag_id, COUNT(*) AS count
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
                GROUP BY sg.tag_id
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
            review_efficiency_curves = self._review_efficiency_curves(conn, project_id, document_id)
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
        review_disagreements = review_total - review_agreements
        tag_counts = {row["tag_id"]: row["count"] for row in tag_count_rows}
        for tag in tags:
            tag["count"] = tag_counts.get(tag["id"], 0)
        suggestion_tag_counts = {row["tag_id"]: row["count"] for row in suggestion_tag_count_rows}
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
                "annotation_label_counts": self._label_counts(tags, tag_counts, include_zero=True),
                "suggestion_label_counts": self._label_counts(tags, suggestion_tag_counts),
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
                "calibration_count": review_total,
                "calibration_disagreement_count": review_disagreements,
                "calibration_error_rate": review_disagreements / review_total if review_total else None,
                "review_efficiency_curves": review_efficiency_curves,
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
        if normalized_order not in {"position", "random", "uncertain", "goldsmith", "hybrid"}:
            raise self.validation_error("Review queue order must be position, random, uncertain, goldsmith, or hybrid.")
        if normalized_order == "random":
            order_sql = "substr(s.id, -12), s.sentence_index"
            suggestion_order_sql = "s.sentence_index, sg.start_token_index, sg.confidence DESC, sg.id"
        elif normalized_order == "uncertain":
            order_sql = "MIN(sg.confidence) ASC, s.sentence_index"
            suggestion_order_sql = "s.sentence_index, sg.confidence ASC, sg.start_token_index, sg.id"
        elif normalized_order in {"goldsmith", "hybrid"}:
            order_sql = (
                "risk_score DESC, "
                "candidate_disagreement_score DESC, "
                "llm_review_risk_score DESC, "
                "judge_review_risk_score DESC, "
                "suggestion_count DESC, "
                "min_confidence ASC, "
                "s.sentence_index"
            )
            suggestion_order_sql = (
                f"s.sentence_index, {self._combined_review_risk_sql()} DESC, "
                "sg.confidence ASC, sg.start_token_index, sg.id"
            )
        else:
            order_sql = "s.sentence_index"
            suggestion_order_sql = "s.sentence_index, sg.start_token_index, sg.confidence DESC, sg.id"
        hybrid_calibration_limit = max(1, safe_limit // 5) if normalized_order == "hybrid" and safe_limit >= 5 else 0
        query_limit = safe_limit + hybrid_calibration_limit if normalized_order == "hybrid" else safe_limit
        candidate_disagreement_score_sql = self._candidate_disagreement_score_sql("s")
        lexical_risk_score_sql = "((1.0 - MIN(sg.confidence)) * COUNT(DISTINCT sg.id))"
        llm_review_risk_score_sql = f"SUM({self._llm_review_risk_sql()})"
        judge_review_risk_score_sql = f"SUM({self._judge_review_risk_sql()})"
        risk_score_sql = (
            f"({lexical_risk_score_sql} + {llm_review_risk_score_sql} + "
            f"{judge_review_risk_score_sql} + {candidate_disagreement_score_sql})"
        )
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
                       {lexical_risk_score_sql} AS lexical_risk_score,
                       {llm_review_risk_score_sql} AS llm_review_risk_score,
                       {judge_review_risk_score_sql} AS judge_review_risk_score,
                       {candidate_disagreement_score_sql} AS candidate_disagreement_score,
                       {risk_score_sql} AS risk_score
                FROM sentences s
                JOIN annotation_suggestions sg ON sg.sentence_id = s.id
                LEFT JOIN annotation_suggestion_reviews rev ON rev.id = (
                    SELECT latest.id
                    FROM annotation_suggestion_reviews latest
                    WHERE latest.suggestion_id = sg.id
                    ORDER BY latest.created_at DESC, latest.id DESC
                    LIMIT 1
                )
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
                (document_id, query_limit),
            ).fetchall()
            review_routes: dict[str, str] = {}
            if normalized_order == "hybrid":
                calibration_rows = []
                if hybrid_calibration_limit:
                    calibration_rows = conn.execute(
                        f"""
                        SELECT s.id, s.sentence_index, s.text,
                               COUNT(DISTINCT sg.id) AS suggestion_count,
                               MIN(sg.confidence) AS min_confidence,
                               {lexical_risk_score_sql} AS lexical_risk_score,
                               {llm_review_risk_score_sql} AS llm_review_risk_score,
                               {judge_review_risk_score_sql} AS judge_review_risk_score,
                               {candidate_disagreement_score_sql} AS candidate_disagreement_score,
                               {risk_score_sql} AS risk_score
                        FROM sentences s
                        JOIN annotation_suggestions sg ON sg.sentence_id = s.id
                        LEFT JOIN annotation_suggestion_reviews rev ON rev.id = (
                            SELECT latest.id
                            FROM annotation_suggestion_reviews latest
                            WHERE latest.suggestion_id = sg.id
                            ORDER BY latest.created_at DESC, latest.id DESC
                            LIMIT 1
                        )
                        WHERE s.document_id = ? AND s.completed = 0 AND sg.status = 'pending'
                          AND NOT EXISTS (
                            SELECT 1
                            FROM annotations a
                            WHERE a.sentence_id = sg.sentence_id
                              AND a.start_token_index <= sg.end_token_index
                              AND a.end_token_index >= sg.start_token_index
                          )
                        GROUP BY s.id, s.sentence_index, s.text
                        HAVING MIN(sg.confidence) >= ? AND SUM({self._combined_review_risk_sql()}) = 0
                        ORDER BY MIN(sg.confidence) DESC, s.sentence_index
                        LIMIT ?
                        """,
                        (document_id, HIGH_CONFIDENCE_THRESHOLD, hybrid_calibration_limit),
                    ).fetchall()
                sentence_rows, review_routes = self._hybrid_review_rows(sentence_rows, calibration_rows, safe_limit)
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

        items = []
        for row in sentence_rows:
            first_suggestion = first_suggestion_by_sentence.get(row["id"])
            review_route = review_routes.get(
                row["id"],
                "risk" if normalized_order in {"goldsmith", "hybrid"} else normalized_order,
            )
            item = {
                "id": row["id"],
                "index": row["sentence_index"],
                "text": row["text"],
                "suggestion_count": row["suggestion_count"],
                "priority_score": float(row["min_confidence"] or 0),
                "min_confidence": float(row["min_confidence"] or 0),
                "lexical_risk_score": float(row["lexical_risk_score"] or 0),
                "llm_review_risk_score": float(row["llm_review_risk_score"] or 0),
                "judge_review_risk_score": float(row["judge_review_risk_score"] or 0),
                "candidate_disagreement_score": float(row["candidate_disagreement_score"] or 0),
                "risk_score": float(row["risk_score"] or 0),
                "review_route": review_route,
                "first_suggestion": first_suggestion,
            }
            item["risk_reason_codes"] = self._review_queue_risk_reason_codes(item, first_suggestion)
            items.append(item)

        return {"items": items, "total": int(total or 0)}

    @staticmethod
    def _llm_review_risk_sql(alias: str = "rev") -> str:
        return (
            f"CASE {alias}.recommendation "
            f"WHEN 'reject' THEN {LLM_REVIEW_REJECT_RISK_WEIGHT} "
            f"WHEN 'uncertain' THEN {LLM_REVIEW_UNCERTAIN_RISK_WEIGHT} "
            "ELSE 0 END"
        )

    @staticmethod
    def _judge_review_risk_sql(alias: str = "rev") -> str:
        return f"annopilot_judge_review_risk({alias}.judge_json)"

    @classmethod
    def _combined_review_risk_sql(cls, alias: str = "rev") -> str:
        return f"({cls._llm_review_risk_sql(alias)} + {cls._judge_review_risk_sql(alias)})"

    @staticmethod
    def _review_queue_risk_reason_codes(item: dict[str, Any], first_suggestion: dict[str, Any] | None) -> list[str]:
        codes: list[str] = []
        latest_review = (first_suggestion or {}).get("latest_review") or {}
        judge = latest_review.get("judge") or {}
        recommendation = latest_review.get("recommendation")
        error_types = set(judge.get("error_types") or []) if isinstance(judge, dict) else set()
        risk_flags = set(judge.get("risk_flags") or []) if isinstance(judge, dict) else set()

        if item.get("review_route") == "calibration":
            codes.append("calibration_sample")
        if float(item.get("candidate_disagreement_score") or 0.0) > 0:
            codes.append("candidate_conflict")
        if float(item.get("llm_review_risk_score") or 0.0) > 0:
            if recommendation == "reject":
                codes.append("llm_reject")
            elif recommendation == "uncertain":
                codes.append("llm_uncertain")
            else:
                codes.append("llm_review_risk")
        if float(item.get("judge_review_risk_score") or 0.0) > 0:
            if isinstance(judge, dict) and judge.get("needs_review") is True:
                codes.append("judge_needs_review")
            boundary_score = DocumentQueryRepository._float_or_default(judge.get("boundary_score") if isinstance(judge, dict) else None, 1.0)
            missed_span_risk = DocumentQueryRepository._float_or_default(judge.get("missed_span_risk") if isinstance(judge, dict) else None, 0.0)
            extra_span_risk = DocumentQueryRepository._float_or_default(judge.get("extra_span_risk") if isinstance(judge, dict) else None, 0.0)
            overall_score = DocumentQueryRepository._float_or_default(judge.get("overall_score") if isinstance(judge, dict) else None, 1.0)
            if boundary_score <= 0.65 or {"boundary_too_wide", "boundary_too_narrow"} & error_types:
                codes.append("judge_boundary")
            if missed_span_risk >= 0.5 or "missed_span" in error_types or "possible_under_annotation" in risk_flags:
                codes.append("judge_missing_span")
            if extra_span_risk >= 0.5 or "extra_span" in error_types or "possible_over_annotation" in risk_flags:
                codes.append("judge_extra_span")
            if overall_score <= 0.75:
                codes.append("judge_low_score")
            if not any(code.startswith("judge_") for code in codes):
                codes.append("judge_risk")
        if float(item.get("lexical_risk_score") or 0.0) > 0:
            if float(item.get("min_confidence") or 0.0) < MEDIUM_CONFIDENCE_THRESHOLD:
                codes.append("low_confidence")
            if int(item.get("suggestion_count") or 0) >= 2:
                codes.append("dense_candidates")
        if not codes and item.get("review_route") == "uncertain":
            codes.append("uncertain_confidence")
        return list(dict.fromkeys(codes))

    @staticmethod
    def _float_or_default(value: Any, default: float) -> float:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return default
        return number

    @staticmethod
    def _candidate_disagreement_score_sql(sentence_alias: str = "s") -> str:
        return f"""
            (
              SELECT CASE
                WHEN COUNT(*) = 0 THEN 0.0
                ELSE COALESCE(
                  SUM(
                    CASE
                      WHEN left_sg.start_token_index <= right_sg.end_token_index
                       AND left_sg.end_token_index >= right_sg.start_token_index
                       AND (
                         left_sg.start_token_index != right_sg.start_token_index
                         OR left_sg.end_token_index != right_sg.end_token_index
                         OR left_sg.tag_id != right_sg.tag_id
                       )
                      THEN 1.0
                      ELSE 0.0
                    END
                  ) / COUNT(*),
                  0.0
                )
              END
              FROM annotation_suggestions left_sg
              JOIN annotation_suggestions right_sg
                ON right_sg.sentence_id = left_sg.sentence_id
               AND right_sg.id > left_sg.id
              WHERE left_sg.sentence_id = {sentence_alias}.id
                AND left_sg.status = 'pending'
                AND right_sg.status = 'pending'
                AND NOT EXISTS (
                  SELECT 1
                  FROM annotations left_annotation
                  WHERE left_annotation.sentence_id = left_sg.sentence_id
                    AND left_annotation.start_token_index <= left_sg.end_token_index
                    AND left_annotation.end_token_index >= left_sg.start_token_index
                )
                AND NOT EXISTS (
                  SELECT 1
                  FROM annotations right_annotation
                  WHERE right_annotation.sentence_id = right_sg.sentence_id
                    AND right_annotation.start_token_index <= right_sg.end_token_index
                    AND right_annotation.end_token_index >= right_sg.start_token_index
                )
            )
        """

    def get_goldsmith_human_choices(self, project_id: str, document_id: str) -> list[dict[str, Any]]:
        with self.connect() as conn:
            document = conn.execute(
                "SELECT id FROM documents WHERE id = ? AND project_id = ?",
                (document_id, project_id),
            ).fetchone()
            if document is None:
                raise self.not_found_error("Document not found.")
            rows = conn.execute(
                """
                SELECT sg.id, sg.run_id, sg.sentence_id, s.sentence_index, s.text AS sentence_text,
                       sg.tag_id, tags.name AS tag_name, tags.color AS tag_color,
                       sg.start_token_index, sg.end_token_index, sg.start_char, sg.end_char, sg.text,
                       sg.confidence, sg.source, sg.evidence_text, sg.match_key, sg.evidence_match_key,
                       sg.context_before, sg.context_after, sg.status, sg.created_at,
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
                WHERE d.id = ? AND d.project_id = ? AND sg.status IN ('accepted', 'rejected')
                ORDER BY s.sentence_index, sg.start_token_index, sg.id
                """,
                (document_id, project_id),
            ).fetchall()
        return [self._goldsmith_choice_row_dict(row) for row in rows]

    @staticmethod
    def _hybrid_review_rows(
        risk_rows: list[sqlite3.Row],
        calibration_rows: list[sqlite3.Row],
        limit: int,
    ) -> tuple[list[sqlite3.Row], dict[str, str]]:
        if limit <= 0:
            return [], {}
        calibration_ids = {row["id"] for row in calibration_rows}
        risk_pool = [row for row in risk_rows if row["id"] not in calibration_ids]

        risk_slots = max(limit - len(calibration_rows), 0)
        selected = risk_pool[:risk_slots]
        for index, row in enumerate(calibration_rows):
            insert_at = min((index + 1) * 4, len(selected))
            selected.insert(insert_at, row)
        for row in risk_pool[risk_slots:]:
            if len(selected) >= limit:
                break
            selected.append(row)
        selected = selected[:limit]
        routes = {row["id"]: ("calibration" if row["id"] in calibration_ids else "risk") for row in selected}
        return selected, routes

    def _review_efficiency_curves(self, conn: sqlite3.Connection, project_id: str, document_id: str) -> dict[str, dict[str, Any]]:
        rows = conn.execute(
            """
            SELECT sg.id, sg.sentence_id, s.sentence_index, sg.start_token_index, sg.end_token_index,
                   sg.tag_id, sg.confidence, sg.status, rev.recommendation, rev.judge_json
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
            ORDER BY s.sentence_index, sg.start_token_index, sg.id
            """,
            (document_id, project_id),
        ).fetchall()
        if not rows:
            return {}

        items = []
        sentence_stats: dict[str, dict[str, Any]] = {}
        items_by_sentence: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            human_decision = "accept" if row["status"] == "accepted" else "reject"
            item = {
                "suggestion_id": row["id"],
                "sentence_id": row["sentence_id"],
                "sentence_index": int(row["sentence_index"]),
                "tag_id": row["tag_id"],
                "start_token_index": int(row["start_token_index"]),
                "end_token_index": int(row["end_token_index"]),
                "confidence": float(row["confidence"] or 0.0),
                "human_decision": human_decision,
                "review_recommendation": row["recommendation"],
                "judge_review_risk_score": judge_review_risk_score(row["judge_json"]),
                "disagreement": human_decision != row["recommendation"],
            }
            items.append(item)
            items_by_sentence.setdefault(row["sentence_id"], []).append(item)
            stats = sentence_stats.setdefault(
                row["sentence_id"],
                {
                    "suggestion_count": 0,
                    "min_confidence": float(row["confidence"] or 0.0),
                },
            )
            stats["suggestion_count"] += 1
            stats["min_confidence"] = min(float(stats["min_confidence"]), float(row["confidence"] or 0.0))

        candidate_disagreement_by_sentence = {
            sentence_id: self._candidate_disagreement_score(sentence_items)
            for sentence_id, sentence_items in items_by_sentence.items()
        }

        for item in items:
            stats = sentence_stats[item["sentence_id"]]
            candidate_disagreement_score = candidate_disagreement_by_sentence.get(item["sentence_id"], 0.0)
            item["sentence_suggestion_count"] = int(stats["suggestion_count"])
            item["sentence_min_confidence"] = float(stats["min_confidence"])
            item["candidate_disagreement_score"] = candidate_disagreement_score
            item["risk_score"] = (
                ((1.0 - item["sentence_min_confidence"]) * item["sentence_suggestion_count"])
                + float(item.get("judge_review_risk_score", 0.0))
                + candidate_disagreement_score
            )

        return {
            order: self._review_efficiency_curve(order, self._order_review_efficiency_items(items, order))
            for order in ("position", "random", "uncertain", "goldsmith", "hybrid")
        }

    def _order_review_efficiency_items(self, items: list[dict[str, Any]], order: str) -> list[dict[str, Any]]:
        if order == "random":
            return sorted(items, key=lambda item: (item["suggestion_id"][-12:], item["sentence_index"], item["start_token_index"], item["suggestion_id"]))
        if order == "uncertain":
            return sorted(items, key=lambda item: (item["confidence"], item["sentence_index"], item["start_token_index"], item["suggestion_id"]))
        if order == "goldsmith":
            return sorted(items, key=self._review_efficiency_risk_key)
        if order == "hybrid":
            return self._hybrid_review_efficiency_items(items)
        return sorted(items, key=lambda item: (item["sentence_index"], item["start_token_index"], item["suggestion_id"]))

    def _hybrid_review_efficiency_items(self, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        limit = len(items)
        if limit <= 0:
            return []
        calibration_limit = max(1, limit // 5) if limit >= 5 else 0
        calibration_rows = sorted(
            [item for item in items if item["confidence"] >= HIGH_CONFIDENCE_THRESHOLD],
            key=lambda item: (-item["confidence"], item["sentence_index"], item["start_token_index"], item["suggestion_id"]),
        )[:calibration_limit]
        calibration_ids = {item["suggestion_id"] for item in calibration_rows}
        risk_pool = [item for item in sorted(items, key=self._review_efficiency_risk_key) if item["suggestion_id"] not in calibration_ids]
        risk_slots = max(limit - len(calibration_rows), 0)
        selected = [{**item, "review_route": "risk"} for item in risk_pool[:risk_slots]]
        for index, item in enumerate(calibration_rows):
            insert_at = min((index + 1) * 4, len(selected))
            selected.insert(insert_at, {**item, "review_route": "calibration"})
        for item in risk_pool[risk_slots:]:
            if len(selected) >= limit:
                break
            selected.append({**item, "review_route": "risk"})
        return selected[:limit]

    @staticmethod
    def _review_efficiency_risk_key(item: dict[str, Any]) -> tuple[Any, ...]:
        return (
            -float(item["risk_score"]),
            -float(item.get("candidate_disagreement_score", 0.0)),
            -float(item.get("judge_review_risk_score", 0.0)),
            -int(item["sentence_suggestion_count"]),
            float(item["sentence_min_confidence"]),
            item["sentence_index"],
            float(item["confidence"]),
            item["start_token_index"],
            item["suggestion_id"],
        )

    @staticmethod
    def _candidate_disagreement_score(items: list[dict[str, Any]]) -> float:
        pair_count = 0
        conflict_count = 0
        for left_index, left in enumerate(items):
            for right in items[left_index + 1 :]:
                pair_count += 1
                overlaps = left["start_token_index"] <= right["end_token_index"] and left["end_token_index"] >= right["start_token_index"]
                disagrees = (
                    left["start_token_index"] != right["start_token_index"]
                    or left["end_token_index"] != right["end_token_index"]
                    or left["tag_id"] != right["tag_id"]
                )
                if overlaps and disagrees:
                    conflict_count += 1
        return conflict_count / pair_count if pair_count else 0.0

    @staticmethod
    def _review_efficiency_curve(order: str, ordered_items: list[dict[str, Any]]) -> dict[str, Any]:
        cumulative_disagreements = 0
        first_disagreement_rank = None
        points = []
        point_limit = 20
        for rank, item in enumerate(ordered_items, start=1):
            if item["disagreement"]:
                cumulative_disagreements += 1
                if first_disagreement_rank is None:
                    first_disagreement_rank = rank
            if rank <= point_limit:
                points.append(
                    {
                        "rank": rank,
                        "suggestion_id": item["suggestion_id"],
                        "sentence_id": item["sentence_id"],
                        "sentence_index": item["sentence_index"],
                        "cumulative_reviewed": rank,
                        "cumulative_disagreements": cumulative_disagreements,
                        "disagreement": item["disagreement"],
                        "route": item.get("review_route") or ("risk" if order in {"goldsmith", "hybrid"} else order),
                    }
                )
        reviewed_count = len(ordered_items)
        early_reviewed_count = min(5, reviewed_count)
        return {
            "order": order,
            "reviewed_count": reviewed_count,
            "disagreement_count": cumulative_disagreements,
            "early_reviewed_count": early_reviewed_count,
            "early_disagreement_count": sum(1 for item in ordered_items[:early_reviewed_count] if item["disagreement"]),
            "first_disagreement_rank": first_disagreement_rank,
            "points": points,
        }

    def _goldsmith_choice_row_dict(self, row: sqlite3.Row) -> dict[str, Any]:
        latest_review = None
        if row["review_model"] is not None:
            latest_review = {
                "model": row["review_model"],
                "recommendation": row["review_recommendation"],
                "confidence": row["review_confidence"],
                "rationale": row["review_rationale"],
                "context_sha256": row["review_context_sha256"],
                "judge": self._decode_json_object(row["review_judge_json"]),
                "created_at": row["review_created_at"],
            }
        human_decision = "accept" if row["status"] == "accepted" else "reject"
        return {
            "id": row["id"],
            "run_id": row["run_id"],
            "sentence_id": row["sentence_id"],
            "sentence_index": row["sentence_index"],
            "sentence_text": row["sentence_text"],
            "tag_id": row["tag_id"],
            "tag_name": row["tag_name"],
            "tag_color": row["tag_color"],
            "start_token_index": row["start_token_index"],
            "end_token_index": row["end_token_index"],
            "start_char": row["start_char"],
            "end_char": row["end_char"],
            "text": row["text"],
            "confidence": row["confidence"],
            "source": row["source"],
            "evidence_text": row["evidence_text"],
            "match_key": row["match_key"],
            "evidence_match_key": row["evidence_match_key"],
            "context_before": row["context_before"],
            "context_after": row["context_after"],
            "status": row["status"],
            "human_decision": human_decision,
            "latest_review": latest_review,
            "disagreement": bool(
                latest_review
                and latest_review.get("recommendation") in {"accept", "reject"}
                and latest_review["recommendation"] != human_decision
            ),
            "created_at": row["created_at"],
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

    @staticmethod
    def _decode_json_object(value: str | None) -> dict[str, Any] | None:
        if not value:
            return None
        try:
            payload = json.loads(value)
        except json.JSONDecodeError:
            return None
        return payload if isinstance(payload, dict) else None

    @staticmethod
    def _label_counts(tags: list[dict[str, Any]], counts: dict[str, int], include_zero: bool = False) -> list[dict[str, Any]]:
        rows = [
            {
                "tag_id": tag["id"],
                "name": tag["name"],
                "color": tag["color"],
                "count": int(counts.get(tag["id"], 0) or 0),
                "tag_index": index,
            }
            for index, tag in enumerate(tags)
            if include_zero or int(counts.get(tag["id"], 0) or 0) > 0
        ]
        rows.sort(key=lambda row: (-row["count"], row["tag_index"]))
        return [{key: value for key, value in row.items() if key != "tag_index"} for row in rows]

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
