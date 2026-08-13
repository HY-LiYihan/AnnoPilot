from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Callable
from typing import Any


class RunQueryRepository:
    """Read-only run queries and provenance exports."""

    def __init__(
        self,
        connect: Callable[[], sqlite3.Connection],
        *,
        event_lines: Callable[[str], list[str]],
        now: Callable[[], str],
        not_found_error: type[Exception],
        provenance_schema_version: str,
        high_confidence_threshold: float,
        medium_confidence_threshold: float,
    ) -> None:
        self.connect = connect
        self.event_lines = event_lines
        self.now = now
        self.not_found_error = not_found_error
        self.provenance_schema_version = provenance_schema_version
        self.high_confidence_threshold = high_confidence_threshold
        self.medium_confidence_threshold = medium_confidence_threshold

    def list_runs(self, project_id: str, document_id: str | None = None, limit: int = 10) -> list[dict[str, Any]]:
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
        return [self._run_row_dict(row, source_counts_by_run, confidence_counts_by_run) for row in rows]

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
                raise self.not_found_error("Run not found.")

            suggestion_rows = conn.execute(
                """
                SELECT sg.id, sg.run_id, sg.sentence_id, s.sentence_index, sg.tag_id,
                       tags.name AS tag_name, tags.color AS tag_color,
                       sg.start_token_index, sg.end_token_index, sg.start_char, sg.end_char,
                       sg.text, sg.confidence, sg.source, sg.evidence_text, sg.match_key, sg.evidence_match_key, sg.context_before, sg.context_after, sg.status, sg.created_at,
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

        run = self._run_row_dict(run_row, {run_row["id"]: source_counts}, {run_row["id"]: confidence_counts})
        content_payload = {
            "schema_version": self.provenance_schema_version,
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
            "generated_at": self.now(),
            "content_sha256": self._payload_sha256(content_payload),
        }

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
                (self.high_confidence_threshold, self.medium_confidence_threshold, project_id, *run_ids),
            ).fetchall()
        counts: dict[str, dict[str, int]] = {}
        for row in rows:
            counts.setdefault(row["run_id"], {})[row["bucket"]] = int(row["count"])
        return counts

    def _suggestion_decision_events(self, project_id: str, suggestion_ids: list[str]) -> dict[str, dict[str, Any]]:
        if not suggestion_ids:
            return {}
        tracked_ids = set(suggestion_ids)
        decisions: dict[str, dict[str, Any]] = {}
        for line in self.event_lines(project_id):
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

    def _run_row_dict(
        self,
        row: sqlite3.Row,
        source_counts_by_run: dict[str, dict[str, int]],
        confidence_counts_by_run: dict[str, dict[str, int]],
    ) -> dict[str, Any]:
        return {
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

    def _confidence_bucket(self, confidence: float) -> str:
        if confidence >= self.high_confidence_threshold:
            return "high"
        if confidence >= self.medium_confidence_threshold:
            return "medium"
        return "low"

    @staticmethod
    def _acceptance_rate(accepted_count: int, rejected_count: int) -> float | None:
        decided_count = accepted_count + rejected_count
        if decided_count == 0:
            return None
        return accepted_count / decided_count

    @staticmethod
    def _suggestion_row_dict(row: sqlite3.Row, exclude: set[str] | None = None) -> dict[str, Any]:
        review_keys = {
            "review_model",
            "review_recommendation",
            "review_confidence",
            "review_rationale",
            "review_context_sha256",
            "review_judge_json",
            "review_created_at",
        }
        data = RunQueryRepository._row_dict(row, exclude=(exclude or set()) | review_keys)
        if row["review_model"] is not None:
            data["latest_review"] = {
                "model": row["review_model"],
                "recommendation": row["review_recommendation"],
                "confidence": row["review_confidence"],
                "rationale": row["review_rationale"],
                "context_sha256": row["review_context_sha256"],
                "judge": RunQueryRepository._decode_json_object(row["review_judge_json"]),
                "created_at": row["review_created_at"],
            }
        else:
            data["latest_review"] = None
        return data

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
    def _payload_sha256(payload: dict[str, Any]) -> str:
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()
