from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from typing import Any


class SuggestionDecisionService:
    """Transactional suggestion decision workflows."""

    def __init__(
        self,
        connect: Callable[[], sqlite3.Connection],
        *,
        new_id: Callable[[str], str],
        now: Callable[[], str],
        enqueue_event: Callable[[sqlite3.Connection, str, dict[str, Any]], dict[str, Any]],
        flush_event_outbox: Callable[[str], int],
        get_sentence_annotations: Callable[[str, str], list[dict[str, Any]]],
        ranges_overlap: Callable[[int, int, int, int], bool],
        not_found_error: type[Exception],
        validation_error: type[Exception],
    ) -> None:
        self.connect = connect
        self.new_id = new_id
        self.now = now
        self.enqueue_event = enqueue_event
        self.flush_event_outbox = flush_event_outbox
        self.get_sentence_annotations = get_sentence_annotations
        self.ranges_overlap = ranges_overlap
        self.not_found_error = not_found_error
        self.validation_error = validation_error

    def accept_suggestion(self, project_id: str, suggestion_id: str) -> list[dict[str, Any]]:
        now = self.now()
        with self.connect() as conn:
            suggestion = self._get_project_suggestion(conn, project_id, suggestion_id, include_span=True)
            if suggestion["status"] != "pending":
                raise self.validation_error("Only pending suggestions can be accepted.")
            self._accept_suggestion_row(conn, project_id, suggestion, now)

        self.flush_event_outbox(project_id)
        return self.get_sentence_annotations(project_id, suggestion["sentence_id"])

    def reject_suggestion(self, project_id: str, suggestion_id: str) -> dict[str, Any]:
        with self.connect() as conn:
            suggestion = self._get_project_suggestion(conn, project_id, suggestion_id)
            if suggestion["status"] != "pending":
                raise self.validation_error("Only pending suggestions can be rejected.")
            conn.execute("UPDATE annotation_suggestions SET status = 'rejected' WHERE id = ?", (suggestion_id,))
            self._enqueue_rejected(conn, project_id, suggestion)

        self.flush_event_outbox(project_id)
        return {"rejected": True, "suggestion_id": suggestion_id}

    def accept_sentence_suggestions(self, project_id: str, sentence_id: str) -> dict[str, Any]:
        now = self.now()
        accepted_suggestion_ids: list[str] = []
        skipped = 0

        with self.connect() as conn:
            self._require_project_sentence(conn, project_id, sentence_id)
            blocked_ranges = self._sentence_blocked_ranges(conn, sentence_id)

            suggestions = conn.execute(
                """
                SELECT id, sentence_id, tag_id, start_token_index, end_token_index, start_char, end_char, text
                FROM annotation_suggestions
                WHERE sentence_id = ? AND status = 'pending'
                ORDER BY start_token_index, end_token_index, confidence DESC, id
                """,
                (sentence_id,),
            ).fetchall()

            for suggestion in suggestions:
                if self._is_blocked(suggestion, blocked_ranges):
                    skipped += 1
                    continue

                self._accept_suggestion_row(conn, project_id, suggestion, now)
                blocked_ranges.append((suggestion["start_token_index"], suggestion["end_token_index"]))
                accepted_suggestion_ids.append(suggestion["id"])

        self.flush_event_outbox(project_id)
        return {
            "accepted": len(accepted_suggestion_ids),
            "skipped": skipped,
            "accepted_suggestion_ids": accepted_suggestion_ids,
            "affected_sentence_ids": [sentence_id] if accepted_suggestion_ids else [],
            "annotations": self.get_sentence_annotations(project_id, sentence_id),
        }

    def reject_sentence_suggestions(self, project_id: str, sentence_id: str) -> dict[str, Any]:
        rejected_suggestion_ids: list[str] = []

        with self.connect() as conn:
            self._require_project_sentence(conn, project_id, sentence_id)
            suggestions = conn.execute(
                """
                SELECT id, sentence_id
                FROM annotation_suggestions
                WHERE sentence_id = ? AND status = 'pending'
                ORDER BY start_token_index, end_token_index, confidence DESC, id
                """,
                (sentence_id,),
            ).fetchall()

            for suggestion in suggestions:
                conn.execute("UPDATE annotation_suggestions SET status = 'rejected' WHERE id = ?", (suggestion["id"],))
                self._enqueue_rejected(conn, project_id, suggestion)
                rejected_suggestion_ids.append(suggestion["id"])

        self.flush_event_outbox(project_id)
        return {
            "rejected": len(rejected_suggestion_ids),
            "rejected_suggestion_ids": rejected_suggestion_ids,
            "affected_sentence_ids": [sentence_id] if rejected_suggestion_ids else [],
        }

    def apply_sentence_suggestion_reviews(self, project_id: str, sentence_id: str) -> dict[str, Any]:
        now = self.now()
        accepted_suggestion_ids: list[str] = []
        rejected_suggestion_ids: list[str] = []
        skipped = 0
        kept = 0

        with self.connect() as conn:
            self._require_project_sentence(conn, project_id, sentence_id)
            blocked_ranges = self._sentence_blocked_ranges(conn, sentence_id)
            suggestions = conn.execute(
                """
                SELECT sg.id, sg.sentence_id, sg.tag_id, sg.start_token_index, sg.end_token_index,
                       sg.start_char, sg.end_char, sg.text, rev.recommendation
                FROM annotation_suggestions sg
                LEFT JOIN annotation_suggestion_reviews rev ON rev.id = (
                    SELECT latest.id
                    FROM annotation_suggestion_reviews latest
                    WHERE latest.suggestion_id = sg.id
                    ORDER BY latest.created_at DESC, latest.id DESC
                    LIMIT 1
                )
                WHERE sg.sentence_id = ? AND sg.status = 'pending'
                ORDER BY sg.start_token_index, sg.end_token_index, sg.confidence DESC, sg.id
                """,
                (sentence_id,),
            ).fetchall()

            for suggestion in suggestions:
                recommendation = suggestion["recommendation"]
                if recommendation == "reject":
                    conn.execute("UPDATE annotation_suggestions SET status = 'rejected' WHERE id = ?", (suggestion["id"],))
                    self._enqueue_rejected(conn, project_id, suggestion)
                    rejected_suggestion_ids.append(suggestion["id"])
                    continue

                if recommendation != "accept":
                    kept += 1
                    continue

                if self._is_blocked(suggestion, blocked_ranges):
                    skipped += 1
                    continue

                self._accept_suggestion_row(conn, project_id, suggestion, now)
                blocked_ranges.append((suggestion["start_token_index"], suggestion["end_token_index"]))
                accepted_suggestion_ids.append(suggestion["id"])

        self.flush_event_outbox(project_id)
        affected = bool(accepted_suggestion_ids or rejected_suggestion_ids)
        return {
            "accepted": len(accepted_suggestion_ids),
            "rejected": len(rejected_suggestion_ids),
            "skipped": skipped,
            "kept": kept,
            "accepted_suggestion_ids": accepted_suggestion_ids,
            "rejected_suggestion_ids": rejected_suggestion_ids,
            "affected_sentence_ids": [sentence_id] if affected else [],
            "annotations": self.get_sentence_annotations(project_id, sentence_id),
        }

    def apply_document_suggestion_reviews(self, project_id: str, document_id: str) -> dict[str, Any]:
        now = self.now()
        accepted_suggestion_ids: list[str] = []
        rejected_suggestion_ids: list[str] = []
        affected_sentence_ids: list[str] = []
        skipped = 0
        kept = 0

        with self.connect() as conn:
            self._require_project_document(conn, project_id, document_id)
            blocked_by_sentence = self._document_blocked_ranges(conn, document_id)
            suggestions = conn.execute(
                """
                SELECT sg.id, sg.sentence_id, sg.tag_id, sg.start_token_index, sg.end_token_index,
                       sg.start_char, sg.end_char, sg.text, s.sentence_index, rev.recommendation
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
                WHERE d.id = ? AND d.project_id = ? AND sg.status = 'pending'
                ORDER BY s.sentence_index, sg.start_token_index, sg.end_token_index, sg.confidence DESC, sg.id
                """,
                (document_id, project_id),
            ).fetchall()

            for suggestion in suggestions:
                recommendation = suggestion["recommendation"]
                if recommendation == "reject":
                    conn.execute("UPDATE annotation_suggestions SET status = 'rejected' WHERE id = ?", (suggestion["id"],))
                    self._enqueue_rejected(conn, project_id, suggestion)
                    rejected_suggestion_ids.append(suggestion["id"])
                    self._append_unique(affected_sentence_ids, suggestion["sentence_id"])
                    continue

                if recommendation != "accept":
                    kept += 1
                    continue

                blocked_ranges = blocked_by_sentence.setdefault(suggestion["sentence_id"], [])
                if self._is_blocked(suggestion, blocked_ranges):
                    skipped += 1
                    continue

                self._accept_suggestion_row(conn, project_id, suggestion, now)
                blocked_ranges.append((suggestion["start_token_index"], suggestion["end_token_index"]))
                accepted_suggestion_ids.append(suggestion["id"])
                self._append_unique(affected_sentence_ids, suggestion["sentence_id"])

        self.flush_event_outbox(project_id)
        return {
            "accepted": len(accepted_suggestion_ids),
            "rejected": len(rejected_suggestion_ids),
            "skipped": skipped,
            "kept": kept,
            "accepted_suggestion_ids": accepted_suggestion_ids,
            "rejected_suggestion_ids": rejected_suggestion_ids,
            "affected_sentence_ids": affected_sentence_ids,
        }

    def auto_accept_document_suggestions(
        self,
        project_id: str,
        document_id: str,
        min_confidence: float = 0.9,
        *,
        complete_sentences: bool = False,
        completion_source: str = "auto_accept_suggestions",
    ) -> dict[str, Any]:
        confidence_floor = max(0.0, min(float(min_confidence), 1.0))
        now = self.now()
        accepted_suggestion_ids: list[str] = []
        affected_sentence_ids: list[str] = []
        accepted_by_sentence: dict[str, list[str]] = {}
        completed_sentence_ids: list[str] = []
        skipped = 0

        with self.connect() as conn:
            self._require_project_document(conn, project_id, document_id)
            blocked_by_sentence = self._document_blocked_ranges(conn, document_id)
            suggestions = conn.execute(
                """
                SELECT sg.id, sg.sentence_id, sg.tag_id, sg.start_token_index, sg.end_token_index,
                       sg.start_char, sg.end_char, sg.text, sg.confidence, s.sentence_index,
                       sg.source, cg.verifier_status, cg.consistency_json
                FROM annotation_suggestions sg
                JOIN sentences s ON s.id = sg.sentence_id
                JOIN documents d ON d.id = s.document_id
                LEFT JOIN annotation_candidate_groups cg ON cg.id = sg.candidate_group_id
                WHERE d.id = ? AND d.project_id = ? AND sg.status = 'pending' AND sg.confidence >= ?
                ORDER BY sg.confidence DESC, s.sentence_index, sg.start_token_index, sg.id
                """,
                (document_id, project_id, confidence_floor),
            ).fetchall()

            for suggestion in suggestions:
                if suggestion["source"] == "llm_engagement" and not self._engagement_auto_accept_allowed(suggestion):
                    skipped += 1
                    continue
                blocked_ranges = blocked_by_sentence.setdefault(suggestion["sentence_id"], [])
                if self._is_blocked(suggestion, blocked_ranges):
                    skipped += 1
                    continue

                self._accept_suggestion_row(conn, project_id, suggestion, now)
                blocked_ranges.append((suggestion["start_token_index"], suggestion["end_token_index"]))
                accepted_suggestion_ids.append(suggestion["id"])
                accepted_by_sentence.setdefault(suggestion["sentence_id"], []).append(suggestion["id"])
                self._append_unique(affected_sentence_ids, suggestion["sentence_id"])

            if complete_sentences and affected_sentence_ids:
                completed_sentence_ids = self._complete_clear_accepted_sentences(
                    conn,
                    project_id,
                    document_id,
                    affected_sentence_ids,
                    accepted_by_sentence,
                    completion_source,
                )

        self.flush_event_outbox(project_id)
        return {
            "accepted": len(accepted_suggestion_ids),
            "skipped": skipped,
            "min_confidence": confidence_floor,
            "accepted_suggestion_ids": accepted_suggestion_ids,
            "affected_sentence_ids": affected_sentence_ids,
            "completed": len(completed_sentence_ids),
            "completed_sentence_ids": completed_sentence_ids,
        }

    def auto_reject_document_suggestions(self, project_id: str, document_id: str) -> dict[str, Any]:
        rejected_suggestion_ids: list[str] = []
        affected_sentence_ids: list[str] = []

        with self.connect() as conn:
            self._require_project_document(conn, project_id, document_id)
            suggestions = conn.execute(
                """
                SELECT sg.id, sg.sentence_id
                FROM annotation_suggestions sg
                JOIN sentences s ON s.id = sg.sentence_id
                JOIN documents d ON d.id = s.document_id
                WHERE d.id = ? AND d.project_id = ? AND sg.status = 'pending'
                ORDER BY s.sentence_index, sg.start_token_index, sg.id
                """,
                (document_id, project_id),
            ).fetchall()

            for suggestion in suggestions:
                conn.execute("UPDATE annotation_suggestions SET status = 'rejected' WHERE id = ?", (suggestion["id"],))
                self._enqueue_rejected(conn, project_id, suggestion)
                rejected_suggestion_ids.append(suggestion["id"])
                self._append_unique(affected_sentence_ids, suggestion["sentence_id"])

        self.flush_event_outbox(project_id)
        return {
            "rejected": len(rejected_suggestion_ids),
            "rejected_suggestion_ids": rejected_suggestion_ids,
            "affected_sentence_ids": affected_sentence_ids,
        }

    def _accept_suggestion_row(self, conn: sqlite3.Connection, project_id: str, suggestion: sqlite3.Row, now: str) -> str:
        annotation_id = self.new_id("ann")
        conn.execute(
            """
            INSERT INTO annotations (
                id, sentence_id, tag_id, start_token_index, end_token_index,
                start_char, end_char, text, source, source_suggestion_id, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'accepted_suggestion', ?, ?)
            """,
            (
                annotation_id,
                suggestion["sentence_id"],
                suggestion["tag_id"],
                suggestion["start_token_index"],
                suggestion["end_token_index"],
                suggestion["start_char"],
                suggestion["end_char"],
                suggestion["text"],
                suggestion["id"],
                now,
            ),
        )
        conn.execute("UPDATE annotation_suggestions SET status = 'accepted' WHERE id = ?", (suggestion["id"],))
        self.enqueue_event(
            conn,
            project_id,
            {
                "type": "annotation.created",
                "annotation_id": annotation_id,
                "sentence_id": suggestion["sentence_id"],
                "tag_id": suggestion["tag_id"],
                "start_token_index": suggestion["start_token_index"],
                "end_token_index": suggestion["end_token_index"],
                "start_char": suggestion["start_char"],
                "end_char": suggestion["end_char"],
                "text": suggestion["text"],
                "source": "accepted_suggestion",
                "source_suggestion_id": suggestion["id"],
                "created_at": now,
            },
        )
        self.enqueue_event(
            conn,
            project_id,
            {"type": "suggestion.accepted", "suggestion_id": suggestion["id"], "sentence_id": suggestion["sentence_id"]},
        )
        return annotation_id

    def _enqueue_rejected(self, conn: sqlite3.Connection, project_id: str, suggestion: sqlite3.Row) -> None:
        self.enqueue_event(
            conn,
            project_id,
            {"type": "suggestion.rejected", "suggestion_id": suggestion["id"], "sentence_id": suggestion["sentence_id"]},
        )

    def _get_project_suggestion(
        self,
        conn: sqlite3.Connection,
        project_id: str,
        suggestion_id: str,
        *,
        include_span: bool = False,
    ) -> sqlite3.Row:
        columns = "sg.id, sg.sentence_id, sg.status"
        if include_span:
            columns = """
                sg.id, sg.sentence_id, sg.tag_id, sg.start_token_index, sg.end_token_index,
                sg.start_char, sg.end_char, sg.text, sg.status
            """
        suggestion = conn.execute(
            f"""
            SELECT {columns}
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

    def _require_project_sentence(self, conn: sqlite3.Connection, project_id: str, sentence_id: str) -> None:
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
            raise self.not_found_error("Sentence not found.")

    def _require_project_document(self, conn: sqlite3.Connection, project_id: str, document_id: str) -> None:
        document = conn.execute("SELECT id FROM documents WHERE id = ? AND project_id = ?", (document_id, project_id)).fetchone()
        if document is None:
            raise self.not_found_error("Document not found.")

    def _sentence_blocked_ranges(self, conn: sqlite3.Connection, sentence_id: str) -> list[tuple[int, int]]:
        rows = conn.execute(
            """
            SELECT start_token_index, end_token_index
            FROM annotations
            WHERE sentence_id = ?
            """,
            (sentence_id,),
        ).fetchall()
        return [(row["start_token_index"], row["end_token_index"]) for row in rows]

    def _document_blocked_ranges(self, conn: sqlite3.Connection, document_id: str) -> dict[str, list[tuple[int, int]]]:
        rows = conn.execute(
            """
            SELECT a.sentence_id, a.start_token_index, a.end_token_index
            FROM annotations a
            JOIN sentences s ON s.id = a.sentence_id
            WHERE s.document_id = ?
            """,
            (document_id,),
        ).fetchall()
        blocked_by_sentence: dict[str, list[tuple[int, int]]] = {}
        for row in rows:
            blocked_by_sentence.setdefault(row["sentence_id"], []).append((row["start_token_index"], row["end_token_index"]))
        return blocked_by_sentence

    def _is_blocked(self, suggestion: sqlite3.Row, blocked_ranges: list[tuple[int, int]]) -> bool:
        return any(
            self.ranges_overlap(suggestion["start_token_index"], suggestion["end_token_index"], start, end)
            for start, end in blocked_ranges
        )

    @staticmethod
    def _engagement_auto_accept_allowed(suggestion: sqlite3.Row) -> bool:
        if suggestion["verifier_status"] != "passed":
            return False
        try:
            consistency = json.loads(suggestion["consistency_json"] or "{}")
        except (TypeError, ValueError, json.JSONDecodeError):
            return False
        return bool(consistency.get("auto_accept_eligible", False))

    @staticmethod
    def _append_unique(values: list[str], value: str) -> None:
        if value not in values:
            values.append(value)

    def _complete_clear_accepted_sentences(
        self,
        conn: sqlite3.Connection,
        project_id: str,
        document_id: str,
        candidate_sentence_ids: list[str],
        accepted_by_sentence: dict[str, list[str]],
        source: str,
    ) -> list[str]:
        remaining_pending = self._sentences_with_visible_pending_suggestions(conn, document_id, candidate_sentence_ids)
        completed_sentence_ids: list[str] = []
        for sentence_id in candidate_sentence_ids:
            if sentence_id in remaining_pending:
                continue
            sentence = conn.execute(
                "SELECT id, completed, answer FROM sentences WHERE id = ? AND document_id = ?",
                (sentence_id, document_id),
            ).fetchone()
            if sentence is None or bool(sentence["completed"]):
                continue
            old_answer = sentence["answer"] or ("accept" if sentence["completed"] else "pending")
            conn.execute("UPDATE sentences SET completed = 1, answer = 'accept' WHERE id = ?", (sentence_id,))
            self.enqueue_event(
                conn,
                project_id,
                {
                    "type": "sentence.completed",
                    "sentence_id": sentence_id,
                    "old_completed": bool(sentence["completed"]),
                    "old_answer": old_answer,
                    "completed": True,
                    "answer": "accept",
                    "source": source,
                    "accepted_suggestion_ids": accepted_by_sentence.get(sentence_id, []),
                },
            )
            completed_sentence_ids.append(sentence_id)
        return completed_sentence_ids

    @staticmethod
    def _sentences_with_visible_pending_suggestions(
        conn: sqlite3.Connection,
        document_id: str,
        sentence_ids: list[str],
    ) -> set[str]:
        if not sentence_ids:
            return set()
        placeholders = ",".join("?" for _ in sentence_ids)
        rows = conn.execute(
            f"""
            SELECT DISTINCT sg.sentence_id
            FROM annotation_suggestions sg
            WHERE sg.sentence_id IN ({placeholders})
              AND sg.status = 'pending'
              AND EXISTS (SELECT 1 FROM sentences s WHERE s.id = sg.sentence_id AND s.document_id = ?)
              AND NOT EXISTS (
                SELECT 1
                FROM annotations a
                WHERE a.sentence_id = sg.sentence_id
                  AND a.start_token_index <= sg.end_token_index
                  AND a.end_token_index >= sg.start_token_index
              )
            """,
            (*sentence_ids, document_id),
        ).fetchall()
        return {row["sentence_id"] for row in rows}
