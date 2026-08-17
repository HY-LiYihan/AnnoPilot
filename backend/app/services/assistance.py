from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from typing import Any

from ..hashing import payload_sha256


ASSISTANCE_SEED_PER_TAG = 5
ASSISTANCE_CONCURRENCY = 5
ASSISTANCE_LEASE_SECONDS = 300
ASSISTANCE_ERROR_REASONS = {
    "missed_span",
    "extra_span",
    "wrong_label",
    "boundary_too_wide",
    "boundary_too_narrow",
    "other",
}


class AssistanceService:
    """Durable rolling assistance queue and atomic human decision boundary."""

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
        conflict_error: type[Exception],
    ) -> None:
        self.connect = connect
        self.new_id = new_id
        self.now = now
        self.enqueue_event = enqueue_event
        self.flush_event_outbox = flush_event_outbox
        self.get_tags = get_tags
        self.not_found_error = not_found_error
        self.validation_error = validation_error
        self.conflict_error = conflict_error

    def get_status(self, project_id: str, document_id: str) -> dict[str, Any]:
        with self.connect() as conn:
            self._require_document(conn, project_id, document_id)
            settings = self._ensure_settings(conn, project_id, document_id)
            tag_progress = self._tag_progress(conn, project_id)
            jobs = conn.execute(
                """
                SELECT j.*, s.sentence_index, s.text AS sentence_text
                FROM assistance_jobs j
                JOIN sentences s ON s.id = j.sentence_id
                WHERE j.project_id = ? AND j.document_id = ?
                  AND j.status IN ('queued', 'running', 'ready', 'skipped', 'failed')
                ORDER BY
                  CASE j.status WHEN 'ready' THEN 0 WHEN 'running' THEN 1 WHEN 'queued' THEN 2 WHEN 'skipped' THEN 3 ELSE 4 END,
                  j.queue_order, s.sentence_index
                LIMIT 200
                """,
                (project_id, document_id),
            ).fetchall()
            counts = {
                row["status"]: int(row["count"])
                for row in conn.execute(
                    """
                    SELECT status, COUNT(*) AS count
                    FROM assistance_jobs
                    WHERE project_id = ? AND document_id = ?
                    GROUP BY status
                    """,
                    (project_id, document_id),
                ).fetchall()
            }
            items = [self._public_job(conn, row) for row in jobs]
            usage = self._usage_totals(conn, project_id, document_id)
        return {
            "enabled": bool(settings["enabled"]),
            "seed_per_tag": ASSISTANCE_SEED_PER_TAG,
            "concurrency": ASSISTANCE_CONCURRENCY,
            "knowledge_revision": int(settings["knowledge_revision"]),
            "active_tags": [item for item in tag_progress if item["active"]],
            "tag_progress": tag_progress,
            "queue": {
                "counts": counts,
                "items": items,
                "ready": counts.get("ready", 0),
                "running": counts.get("running", 0),
                "queued": counts.get("queued", 0),
                "skipped": counts.get("skipped", 0),
                "failed": counts.get("failed", 0),
            },
            "usage": usage,
        }

    def set_enabled(self, project_id: str, document_id: str, enabled: bool) -> dict[str, Any]:
        now = self.now()
        with self.connect() as conn:
            self._require_document(conn, project_id, document_id)
            self._ensure_settings(conn, project_id, document_id)
            conn.execute(
                "UPDATE assistance_settings SET enabled = ?, updated_at = ? WHERE project_id = ? AND document_id = ?",
                (int(enabled), now, project_id, document_id),
            )
            if enabled:
                conn.execute(
                    "UPDATE assistance_jobs SET status = 'queued', lease_until = NULL, updated_at = ? WHERE project_id = ? AND document_id = ? AND status = 'paused'",
                    (now, project_id, document_id),
                )
            else:
                conn.execute(
                    "UPDATE assistance_jobs SET status = 'paused', lease_until = NULL, updated_at = ? WHERE project_id = ? AND document_id = ? AND status IN ('queued', 'running')",
                    (now, project_id, document_id),
                )
            self.enqueue_event(
                conn,
                project_id,
                {"type": "assistance.settings.updated", "document_id": document_id, "enabled": enabled},
            )
        self.flush_event_outbox(project_id)
        if enabled:
            self.ensure_queue(project_id, document_id)
        return self.get_status(project_id, document_id)

    def ensure_all_queues(self) -> int:
        with self.connect() as conn:
            documents = conn.execute("SELECT project_id, id FROM documents ORDER BY created_at, id").fetchall()
        queued = 0
        for document in documents:
            queued += self.ensure_queue(document["project_id"], document["id"])
        return queued

    def ensure_queue(self, project_id: str, document_id: str) -> int:
        now = self.now()
        inserted = 0
        emitted = False
        with self.connect() as conn:
            self._require_document(conn, project_id, document_id)
            settings = self._ensure_settings(conn, project_id, document_id)
            revision = self._sync_knowledge_revision(conn, project_id, document_id, settings)
            tag_progress = self._tag_progress(conn, project_id)
            active_tag_ids = [item["tag_id"] for item in tag_progress if item["active"]]
            if not bool(settings["enabled"]) or not active_tag_ids:
                return 0

            open_count = int(
                conn.execute(
                    """
                    SELECT COUNT(*) FROM assistance_jobs
                    WHERE project_id = ? AND document_id = ? AND status IN ('queued', 'running', 'ready')
                    """,
                    (project_id, document_id),
                ).fetchone()[0]
            )
            needed = max(ASSISTANCE_CONCURRENCY - open_count, 0)
            if not needed:
                return 0

            candidates = conn.execute(
                """
                SELECT s.id, s.sentence_index
                FROM sentences s
                WHERE s.document_id = ? AND s.completed = 0
                  AND NOT EXISTS (SELECT 1 FROM annotations a WHERE a.sentence_id = s.id)
                  AND NOT EXISTS (SELECT 1 FROM assistance_jobs j WHERE j.sentence_id = s.id)
                ORDER BY s.sentence_index
                LIMIT ?
                """,
                (document_id, needed),
            ).fetchall()
            tags = self.get_tags(conn, project_id)
            active_tags = [tag for tag in tags if tag["id"] in active_tag_ids]
            schema_hash = self._tag_schema_hash(active_tags)
            had_jobs = bool(
                conn.execute(
                    "SELECT 1 FROM assistance_jobs WHERE project_id = ? AND document_id = ? LIMIT 1",
                    (project_id, document_id),
                ).fetchone()
            )

            for candidate in candidates:
                queue_order = self._next_queue_order(conn, project_id, document_id, now)
                job_id = self.new_id("assist")
                run_id = self.new_id("run")
                run_config = {
                    "recipe": "rag_llm_assistance",
                    "assistance_job_id": job_id,
                    "knowledge_revision": revision,
                    "active_tag_ids": active_tag_ids,
                    "tag_schema_sha256": schema_hash,
                    "seed_per_tag": ASSISTANCE_SEED_PER_TAG,
                }
                conn.execute(
                    """
                    INSERT INTO annotation_runs (
                      id, project_id, document_id, recipe, config_json, input_count, suggestion_count, snapshot_complete, created_at
                    ) VALUES (?, ?, ?, 'rag_llm_assistance', ?, 1, 0, 0, ?)
                    """,
                    (run_id, project_id, document_id, json.dumps(run_config, ensure_ascii=False), now),
                )
                conn.execute(
                    "INSERT INTO annotation_run_sentences (run_id, sentence_id) VALUES (?, ?)",
                    (run_id, candidate["id"]),
                )
                conn.execute(
                    """
                    INSERT INTO assistance_jobs (
                      id, project_id, document_id, sentence_id, run_id, status, queue_order,
                      knowledge_revision, draft_version, active_tag_ids_json, tag_schema_sha256,
                      created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, 'queued', ?, ?, 1, ?, ?, ?, ?)
                    """,
                    (
                        job_id,
                        project_id,
                        document_id,
                        candidate["id"],
                        run_id,
                        queue_order,
                        revision,
                        json.dumps(active_tag_ids, ensure_ascii=False),
                        schema_hash,
                        now,
                        now,
                    ),
                )
                inserted += 1

            needed -= inserted
            if needed > 0 and not candidates:
                skipped = conn.execute(
                    """
                    SELECT id FROM assistance_jobs
                    WHERE project_id = ? AND document_id = ? AND status = 'skipped'
                    ORDER BY queue_order LIMIT ?
                    """,
                    (project_id, document_id, needed),
                ).fetchall()
                for row in skipped:
                    queue_order = self._next_queue_order(conn, project_id, document_id, now)
                    conn.execute(
                        "UPDATE assistance_jobs SET status = 'ready', queue_order = ?, updated_at = ? WHERE id = ?",
                        (queue_order, now, row["id"]),
                    )

            if inserted and not had_jobs:
                self.enqueue_event(
                    conn,
                    project_id,
                    {
                        "type": "assistance.activated",
                        "document_id": document_id,
                        "active_tag_ids": active_tag_ids,
                        "seed_per_tag": ASSISTANCE_SEED_PER_TAG,
                        "concurrency": ASSISTANCE_CONCURRENCY,
                    },
                )
                emitted = True
        if emitted:
            self.flush_event_outbox(project_id)
        return inserted

    def claim_jobs(self, limit: int = ASSISTANCE_CONCURRENCY) -> list[str]:
        limit = max(1, min(int(limit), ASSISTANCE_CONCURRENCY))
        now = self.now()
        lease_until = (datetime.now(timezone.utc) + timedelta(seconds=ASSISTANCE_LEASE_SECONDS)).isoformat()
        claimed: list[str] = []
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT j.id
                FROM assistance_jobs j
                JOIN assistance_settings st ON st.project_id = j.project_id AND st.document_id = j.document_id
                WHERE st.enabled = 1
                  AND (j.status = 'queued' OR (j.status = 'running' AND COALESCE(j.lease_until, '') < ?))
                ORDER BY j.queue_order, j.created_at, j.id
                LIMIT ?
                """,
                (now, limit),
            ).fetchall()
            for row in rows:
                updated = conn.execute(
                    """
                    UPDATE assistance_jobs
                    SET status = 'running', lease_until = ?, attempt_count = attempt_count + 1, updated_at = ?
                    WHERE id = ? AND (status = 'queued' OR (status = 'running' AND COALESCE(lease_until, '') < ?))
                    """,
                    (lease_until, now, row["id"], now),
                )
                if updated.rowcount:
                    claimed.append(row["id"])
        return claimed

    def get_generation_context(self, job_id: str) -> dict[str, Any]:
        with self.connect() as conn:
            job = conn.execute(
                """
                SELECT j.*, s.text AS sentence_text, s.start_char AS sentence_start_char,
                       s.completed, st.enabled
                FROM assistance_jobs j
                JOIN sentences s ON s.id = j.sentence_id
                JOIN assistance_settings st ON st.project_id = j.project_id AND st.document_id = j.document_id
                WHERE j.id = ?
                """,
                (job_id,),
            ).fetchone()
            if job is None:
                raise self.not_found_error("Assistance job not found.")
            if job["status"] != "running" or not bool(job["enabled"]):
                raise self.conflict_error("Assistance job is no longer runnable.")
            if bool(job["completed"]) or self._sentence_has_annotations(conn, job["sentence_id"]):
                self._cancel_job(conn, job_id, "human_annotation_started")
                conn.commit()
                raise self.conflict_error("Sentence is no longer untouched.")

            active_tag_ids = self._json_list(job["active_tag_ids_json"])
            tags = [tag for tag in self.get_tags(conn, job["project_id"]) if tag["id"] in active_tag_ids]
            if self._tag_schema_hash(tags) != job["tag_schema_sha256"]:
                self._cancel_job(conn, job_id, "tag_schema_changed")
                conn.commit()
                raise self.conflict_error("Tag schema changed while the draft was queued.")
            tokens = [
                {
                    "id": row["id"],
                    "token_index": int(row["token_index"]),
                    "text": row["text"],
                    "start_char": int(row["start_char"]) - int(job["sentence_start_char"]),
                    "end_char": int(row["end_char"]) - int(job["sentence_start_char"]),
                }
                for row in conn.execute(
                    "SELECT id, token_index, text, start_char, end_char FROM tokens WHERE sentence_id = ? ORDER BY token_index",
                    (job["sentence_id"],),
                ).fetchall()
            ]
            examples_by_tag, corrections_by_tag = self._trusted_examples(conn, job["project_id"], active_tag_ids)
            negatives = self._negative_examples(conn, job["project_id"], limit=8)
        return {
            "job_id": job_id,
            "project_id": job["project_id"],
            "document_id": job["document_id"],
            "sentence_id": job["sentence_id"],
            "source_text": job["sentence_text"],
            "sentence_start_char": int(job["sentence_start_char"]),
            "tokens": tokens,
            "tags": tags,
            "examples_by_tag": examples_by_tag,
            "corrections_by_tag": corrections_by_tag,
            "negative_examples": negatives,
            "knowledge_revision": int(job["knowledge_revision"]),
            "attempt_count": int(job["attempt_count"]),
        }

    def store_generation_result(self, job_id: str, result: dict[str, Any]) -> None:
        now = self.now()
        candidate = result.get("candidate") if isinstance(result.get("candidate"), dict) else result
        spans = candidate.get("spans") if isinstance(candidate, dict) else None
        if not isinstance(spans, list):
            raise self.validation_error("Assistance generator did not return a spans list.")
        issues = result.get("issues") or candidate.get("issues") or []
        if any(issue.get("severity") == "error" for issue in issues if isinstance(issue, dict)):
            raise self.validation_error("Assistance draft failed verification.")

        with self.connect() as conn:
            job = conn.execute(
                """
                SELECT j.*, s.text AS sentence_text, s.start_char AS sentence_start_char, s.completed,
                       st.enabled
                FROM assistance_jobs j
                JOIN sentences s ON s.id = j.sentence_id
                JOIN assistance_settings st ON st.project_id = j.project_id AND st.document_id = j.document_id
                WHERE j.id = ?
                """,
                (job_id,),
            ).fetchone()
            if job is None:
                raise self.not_found_error("Assistance job not found.")
            if job["status"] != "running" or not bool(job["enabled"]):
                raise self.conflict_error("Assistance job is no longer runnable.")
            expected_attempt_count = result.get("attempt_count")
            if expected_attempt_count is not None and int(job["attempt_count"]) != int(expected_attempt_count):
                raise self.conflict_error("Assistance job attempt is stale.")
            if bool(job["completed"]) or self._sentence_has_annotations(conn, job["sentence_id"]):
                self._cancel_job(conn, job_id, "human_annotation_started")
                conn.commit()
                raise self.conflict_error("Sentence was manually annotated while assistance was running.")

            active_tags = [
                tag for tag in self.get_tags(conn, job["project_id"])
                if tag["id"] in self._json_list(job["active_tag_ids_json"])
            ]
            if self._tag_schema_hash(active_tags) != job["tag_schema_sha256"]:
                self._cancel_job(conn, job_id, "tag_schema_changed")
                conn.commit()
                raise self.conflict_error("Tag schema changed while assistance was running.")
            tag_ids = {tag["id"] for tag in active_tags}
            tokens = conn.execute(
                "SELECT token_index, start_char, end_char FROM tokens WHERE sentence_id = ? ORDER BY token_index",
                (job["sentence_id"],),
            ).fetchall()
            suggestion_records: list[dict[str, Any]] = []
            occupied: list[tuple[int, int]] = []
            for span in spans:
                record = self._verified_span_record(span, job, tokens, tag_ids)
                if any(left <= record["end_token_index"] and right >= record["start_token_index"] for left, right in occupied):
                    raise self.validation_error("Assistance spans overlap.")
                occupied.append((record["start_token_index"], record["end_token_index"]))
                suggestion_id = self.new_id("sug")
                record["id"] = suggestion_id
                suggestion_records.append(record)
                conn.execute(
                    """
                    INSERT INTO annotation_suggestions (
                      id, run_id, sentence_id, tag_id, start_token_index, end_token_index,
                      start_char, end_char, text, confidence, source, evidence_text,
                      context_before, context_after, assistance_job_id, status, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'rag_llm_assistance', ?, ?, ?, ?, 'pending', ?)
                    """,
                    (
                        suggestion_id,
                        job["run_id"],
                        job["sentence_id"],
                        record["tag_id"],
                        record["start_token_index"],
                        record["end_token_index"],
                        record["start_char"],
                        record["end_char"],
                        record["text"],
                        record["confidence"],
                        record["text"],
                        job["sentence_text"][: record["local_start"]][-48:],
                        job["sentence_text"][record["local_end"] :][:48],
                        job_id,
                        now,
                    ),
                )
                tag_name = next(tag["name"] for tag in active_tags if tag["id"] == record["tag_id"])
                conn.execute(
                    """
                    INSERT INTO annotation_run_candidate_spans (
                      id, run_id, sentence_id, tag_id, tag_name, start_token_index, end_token_index,
                      start_char, end_char, text, confidence, source, evidence_text, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'rag_llm_assistance', ?, ?)
                    """,
                    (
                        f"{job_id}:{record['local_start']}:{record['local_end']}:{record['tag_id']}",
                        job["run_id"],
                        job["sentence_id"],
                        record["tag_id"],
                        tag_name,
                        record["start_token_index"],
                        record["end_token_index"],
                        record["start_char"],
                        record["end_char"],
                        record["text"],
                        record["confidence"],
                        record["text"],
                        now,
                    ),
                )

            public_spans = [self._public_generated_span(record) for record in suggestion_records]
            usage = result.get("usage") if isinstance(result.get("usage"), dict) else {}
            prompt_sha256 = str(result.get("prompt_sha256") or "")
            retrieved_examples = result.get("retrieved_examples") if isinstance(result.get("retrieved_examples"), dict) else {}
            model = str(result.get("model") or "unknown")
            raw_response = str(result.get("raw_response") or candidate.get("raw_response") or "")[:20000]
            result_payload = {"text": job["sentence_text"], "spans": public_spans}
            conn.execute(
                """
                UPDATE assistance_jobs
                SET status = 'ready', lease_until = NULL, model = ?, prompt_sha256 = ?, raw_response = ?,
                    result_json = ?, verifier_status = 'passed', verifier_issues_json = ?,
                    retrieved_examples_json = ?, usage_json = ?, error_message = NULL, updated_at = ?
                WHERE id = ?
                """,
                (
                    model,
                    prompt_sha256,
                    raw_response,
                    json.dumps(result_payload, ensure_ascii=False),
                    json.dumps(issues, ensure_ascii=False),
                    json.dumps(retrieved_examples, ensure_ascii=False),
                    json.dumps(usage, ensure_ascii=False),
                    now,
                    job_id,
                ),
            )
            run_row = conn.execute("SELECT config_json FROM annotation_runs WHERE id = ?", (job["run_id"],)).fetchone()
            run_config = self._json_object(run_row["config_json"] if run_row else "{}")
            run_config.update(
                {
                    "model": model,
                    "prompt_sha256": prompt_sha256,
                    "retrieved_examples": retrieved_examples,
                    "usage": usage,
                    "verifier_status": "passed",
                }
            )
            conn.execute(
                "UPDATE annotation_runs SET config_json = ?, suggestion_count = ?, snapshot_complete = 1 WHERE id = ?",
                (json.dumps(run_config, ensure_ascii=False), len(public_spans), job["run_id"]),
            )
            self.enqueue_event(
                conn,
                job["project_id"],
                {
                    "type": "assistance.draft.generated",
                    "document_id": job["document_id"],
                    "sentence_id": job["sentence_id"],
                    "job_id": job_id,
                    "run_id": job["run_id"],
                    "draft_version": int(job["draft_version"]),
                    "knowledge_revision": int(job["knowledge_revision"]),
                    "model": model,
                    "prompt_sha256": prompt_sha256,
                    "spans": public_spans,
                    "usage": usage,
                },
            )
            project_id = job["project_id"]
        self.flush_event_outbox(project_id)

    def fail_job(self, job_id: str, message: str, *, expected_attempt_count: int | None = None) -> None:
        now = self.now()
        with self.connect() as conn:
            job = conn.execute("SELECT project_id, attempt_count, status FROM assistance_jobs WHERE id = ?", (job_id,)).fetchone()
            if job is None or job["status"] != "running":
                return
            if expected_attempt_count is not None and int(job["attempt_count"]) != int(expected_attempt_count):
                return
            next_status = "queued" if int(job["attempt_count"]) < 2 else "failed"
            conn.execute(
                "UPDATE assistance_jobs SET status = ?, lease_until = NULL, error_message = ?, updated_at = ? WHERE id = ?",
                (next_status, str(message)[:1000], now, job_id),
            )

    def decide(
        self,
        project_id: str,
        sentence_id: str,
        *,
        action: str,
        draft_id: str,
        draft_version: int,
        final_spans: list[dict[str, Any]] | None = None,
        error_reasons: list[str] | None = None,
        error_note: str | None = None,
    ) -> dict[str, Any]:
        normalized_action = str(action).strip().lower()
        if normalized_action not in {"confirm", "skip", "correct"}:
            raise self.validation_error("Assistance action must be confirm, skip, or correct.")
        try:
            normalized_reasons = self._normalize_error_reasons(error_reasons or [])
        except ValueError as exc:
            raise self.validation_error(str(exc)) from exc
        now = self.now()
        project_to_flush = project_id
        with self.connect() as conn:
            job = conn.execute(
                """
                SELECT j.*, s.completed, s.answer, s.text AS sentence_text, s.start_char AS sentence_start_char
                FROM assistance_jobs j
                JOIN sentences s ON s.id = j.sentence_id
                WHERE j.id = ? AND j.project_id = ? AND j.sentence_id = ?
                """,
                (draft_id, project_id, sentence_id),
            ).fetchone()
            if job is None:
                raise self.not_found_error("Assistance draft not found.")
            if int(job["draft_version"]) != int(draft_version):
                raise self.conflict_error("Assistance draft version is stale.")
            if job["status"] not in {"ready", "skipped"}:
                raise self.conflict_error("Assistance draft is not ready for a decision.")

            if normalized_action == "skip":
                queue_order = self._next_queue_order(conn, project_id, job["document_id"], now)
                conn.execute(
                    "UPDATE assistance_jobs SET status = 'skipped', queue_order = ?, updated_at = ? WHERE id = ?",
                    (queue_order, now, job["id"]),
                )
                self.enqueue_event(
                    conn,
                    project_id,
                    {
                        "type": "assistance.sentence.skipped",
                        "document_id": job["document_id"],
                        "sentence_id": sentence_id,
                        "job_id": job["id"],
                        "draft_version": int(job["draft_version"]),
                    },
                )
                document_id = job["document_id"]
            else:
                if bool(job["completed"]) or self._sentence_has_annotations(conn, sentence_id):
                    raise self.conflict_error("Human annotations already exist for this sentence.")
                suggestions = conn.execute(
                    """
                    SELECT id, tag_id, start_token_index, end_token_index, start_char, end_char, text, confidence
                    FROM annotation_suggestions
                    WHERE assistance_job_id = ? AND status = 'pending'
                    ORDER BY start_token_index, end_token_index, id
                    """,
                    (job["id"],),
                ).fetchall()
                original_spans = [self._public_suggestion_span(row) for row in suggestions]
                submitted = original_spans if normalized_action == "confirm" else list(final_spans or [])
                verified_final = self._validate_final_spans(conn, project_id, sentence_id, submitted)
                suggestion_by_key = {
                    (row["tag_id"], int(row["start_token_index"]), int(row["end_token_index"])): row
                    for row in suggestions
                }
                final_keys = {
                    (span["tag_id"], span["start_token_index"], span["end_token_index"])
                    for span in verified_final
                }
                annotation_ids: list[str] = []
                for span in verified_final:
                    key = (span["tag_id"], span["start_token_index"], span["end_token_index"])
                    suggestion = suggestion_by_key.get(key)
                    annotation_id = self.new_id("ann")
                    source = "accepted_suggestion" if normalized_action == "confirm" else "human"
                    source_suggestion_id = suggestion["id"] if source == "accepted_suggestion" and suggestion else None
                    conn.execute(
                        """
                        INSERT INTO annotations (
                          id, sentence_id, tag_id, start_token_index, end_token_index,
                          start_char, end_char, text, source, source_suggestion_id, created_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            annotation_id,
                            sentence_id,
                            span["tag_id"],
                            span["start_token_index"],
                            span["end_token_index"],
                            span["start_char"],
                            span["end_char"],
                            span["text"],
                            source,
                            source_suggestion_id,
                            now,
                        ),
                    )
                    annotation_ids.append(annotation_id)
                    self.enqueue_event(
                        conn,
                        project_id,
                        {
                            "type": "annotation.created",
                            "annotation_id": annotation_id,
                            "sentence_id": sentence_id,
                            "tag_id": span["tag_id"],
                            "start_token_index": span["start_token_index"],
                            "end_token_index": span["end_token_index"],
                            "start_char": span["start_char"],
                            "end_char": span["end_char"],
                            "text": span["text"],
                            "source": source,
                            "source_suggestion_id": source_suggestion_id,
                            "created_at": now,
                        },
                    )

                for suggestion in suggestions:
                    key = (suggestion["tag_id"], int(suggestion["start_token_index"]), int(suggestion["end_token_index"]))
                    status = "accepted" if key in final_keys else "rejected"
                    conn.execute("UPDATE annotation_suggestions SET status = ? WHERE id = ?", (status, suggestion["id"]))

                old_answer = job["answer"] or ("accept" if job["completed"] else "pending")
                conn.execute("UPDATE sentences SET completed = 1, answer = 'accept' WHERE id = ?", (sentence_id,))
                self.enqueue_event(
                    conn,
                    project_id,
                    {
                        "type": "sentence.completed",
                        "sentence_id": sentence_id,
                        "old_completed": bool(job["completed"]),
                        "old_answer": old_answer,
                        "completed": True,
                        "answer": "accept",
                        "source": "assistance_human_confirmation",
                    },
                )
                feedback_id = self.new_id("feedback")
                reason_source = "user" if normalized_reasons else ("pending" if normalized_action == "correct" else None)
                conn.execute(
                    """
                    INSERT INTO assistance_feedback (
                      id, job_id, project_id, document_id, sentence_id, action,
                      original_spans_json, final_spans_json, error_reasons_json,
                      reason_source, error_note, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        feedback_id,
                        job["id"],
                        project_id,
                        job["document_id"],
                        sentence_id,
                        normalized_action,
                        json.dumps(original_spans, ensure_ascii=False),
                        json.dumps(verified_final, ensure_ascii=False),
                        json.dumps(normalized_reasons, ensure_ascii=False),
                        reason_source,
                        str(error_note or "")[:800] or None,
                        now,
                    ),
                )
                next_status = "confirmed" if normalized_action == "confirm" else "corrected"
                conn.execute(
                    "UPDATE assistance_jobs SET status = ?, lease_until = NULL, updated_at = ? WHERE id = ?",
                    (next_status, now, job["id"]),
                )
                conn.execute(
                    """
                    UPDATE assistance_settings
                    SET knowledge_revision = knowledge_revision + 1, updated_at = ?
                    WHERE project_id = ? AND document_id = ?
                    """,
                    (now, project_id, job["document_id"]),
                )
                event_type = "assistance.draft.confirmed" if normalized_action == "confirm" else "assistance.draft.corrected"
                self.enqueue_event(
                    conn,
                    project_id,
                    {
                        "type": event_type,
                        "document_id": job["document_id"],
                        "sentence_id": sentence_id,
                        "job_id": job["id"],
                        "feedback_id": feedback_id,
                        "draft_version": int(job["draft_version"]),
                        "original_spans": original_spans,
                        "final_spans": verified_final,
                        "annotation_ids": annotation_ids,
                        "error_reasons": normalized_reasons,
                        "reason_source": reason_source,
                    },
                )
                document_id = job["document_id"]

        self.flush_event_outbox(project_to_flush)
        self.ensure_queue(project_id, document_id)
        status = self.get_status(project_id, document_id)
        next_item = next((item for item in status["queue"]["items"] if item["status"] == "ready"), None)
        return {
            "action": normalized_action,
            "sentence_id": sentence_id,
            "completed": normalized_action != "skip",
            "next_sentence_id": next_item["sentence_id"] if next_item else None,
            "queue": status["queue"],
        }

    def claim_feedback_for_classification(self) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM assistance_feedback
                WHERE action = 'correct' AND reason_source = 'pending'
                ORDER BY created_at, id LIMIT 1
                """
            ).fetchone()
            if row is None:
                return None
            updated = conn.execute(
                "UPDATE assistance_feedback SET reason_source = 'classifying' WHERE id = ? AND reason_source = 'pending'",
                (row["id"],),
            )
            if not updated.rowcount:
                return None
            return {
                "feedback_id": row["id"],
                "project_id": row["project_id"],
                "document_id": row["document_id"],
                "sentence_id": row["sentence_id"],
                "original_spans": self._json_list(row["original_spans_json"]),
                "final_spans": self._json_list(row["final_spans_json"]),
                "allowed_reasons": sorted(ASSISTANCE_ERROR_REASONS),
            }

    def recover_feedback_classifications(self) -> int:
        with self.connect() as conn:
            updated = conn.execute(
                "UPDATE assistance_feedback SET reason_source = 'pending' WHERE reason_source = 'classifying'"
            )
            return int(updated.rowcount)

    def store_feedback_classification(self, feedback_id: str, reasons: list[str], note: str = "") -> None:
        try:
            normalized = self._normalize_error_reasons(reasons)
        except ValueError as exc:
            raise self.validation_error(str(exc)) from exc
        if not normalized:
            normalized = ["other"]
        now = self.now()
        with self.connect() as conn:
            row = conn.execute("SELECT project_id, reason_source FROM assistance_feedback WHERE id = ?", (feedback_id,)).fetchone()
            if row is None:
                raise self.not_found_error("Assistance feedback not found.")
            if row["reason_source"] != "classifying":
                raise self.conflict_error("Assistance feedback is not awaiting classification.")
            conn.execute(
                """
                UPDATE assistance_feedback
                SET error_reasons_json = ?, reason_source = 'llm_inferred', error_note = ?, classified_at = ?
                WHERE id = ?
                """,
                (json.dumps(normalized, ensure_ascii=False), str(note)[:800] or None, now, feedback_id),
            )
            self.enqueue_event(
                conn,
                row["project_id"],
                {
                    "type": "assistance.error.classified",
                    "feedback_id": feedback_id,
                    "error_reasons": normalized,
                    "reason_source": "llm_inferred",
                    "note": str(note)[:800],
                },
            )
            project_id = row["project_id"]
        self.flush_event_outbox(project_id)

    def release_feedback_classification(self, feedback_id: str) -> None:
        with self.connect() as conn:
            conn.execute(
                "UPDATE assistance_feedback SET reason_source = 'pending' WHERE id = ? AND reason_source = 'classifying'",
                (feedback_id,),
            )

    def _tag_progress(self, conn: sqlite3.Connection, project_id: str) -> list[dict[str, Any]]:
        tags = self.get_tags(conn, project_id)
        counts = {
            row["tag_id"]: {"human": int(row["human_count"]), "trusted": int(row["trusted_count"])}
            for row in conn.execute(
                """
                SELECT a.tag_id,
                       SUM(CASE WHEN a.source = 'human' THEN 1 ELSE 0 END) AS human_count,
                       COUNT(*) AS trusted_count
                FROM annotations a
                JOIN sentences s ON s.id = a.sentence_id
                JOIN documents d ON d.id = s.document_id
                WHERE d.project_id = ? AND s.completed = 1 AND s.answer = 'accept'
                GROUP BY a.tag_id
                """,
                (project_id,),
            ).fetchall()
        }
        result = []
        for tag in tags:
            tag_counts = counts.get(tag["id"], {"human": 0, "trusted": 0})
            result.append(
                {
                    "tag_id": tag["id"],
                    "tag_name": tag["name"],
                    "tag_color": tag["color"],
                    "human_verified_count": tag_counts["human"],
                    "trusted_count": tag_counts["trusted"],
                    "threshold": ASSISTANCE_SEED_PER_TAG,
                    "active": tag_counts["human"] >= ASSISTANCE_SEED_PER_TAG,
                }
            )
        return result

    def _ensure_settings(self, conn: sqlite3.Connection, project_id: str, document_id: str) -> sqlite3.Row:
        row = conn.execute(
            "SELECT * FROM assistance_settings WHERE project_id = ? AND document_id = ?",
            (project_id, document_id),
        ).fetchone()
        if row is not None:
            return row
        now = self.now()
        conn.execute(
            """
            INSERT INTO assistance_settings (project_id, document_id, enabled, knowledge_revision, queue_sequence, updated_at)
            VALUES (?, ?, 1, 0, 0, ?)
            """,
            (project_id, document_id, now),
        )
        return conn.execute(
            "SELECT * FROM assistance_settings WHERE project_id = ? AND document_id = ?",
            (project_id, document_id),
        ).fetchone()

    def _sync_knowledge_revision(
        self,
        conn: sqlite3.Connection,
        project_id: str,
        document_id: str,
        settings: sqlite3.Row,
    ) -> int:
        trusted_sentence_count = int(
            conn.execute(
                """
                SELECT COUNT(*) FROM sentences s
                WHERE s.document_id = ? AND s.completed = 1 AND s.answer = 'accept'
                """,
                (document_id,),
            ).fetchone()[0]
        )
        revision = max(int(settings["knowledge_revision"]), trusted_sentence_count)
        if revision != int(settings["knowledge_revision"]):
            conn.execute(
                "UPDATE assistance_settings SET knowledge_revision = ?, updated_at = ? WHERE project_id = ? AND document_id = ?",
                (revision, self.now(), project_id, document_id),
            )
        return revision

    def _trusted_examples(
        self,
        conn: sqlite3.Connection,
        project_id: str,
        active_tag_ids: list[str],
    ) -> tuple[dict[str, list[str]], dict[str, list[str]]]:
        examples = {tag_id: [] for tag_id in active_tag_ids}
        corrections = {tag_id: [] for tag_id in active_tag_ids}
        if not active_tag_ids:
            return examples, corrections
        placeholders = ",".join("?" for _ in active_tag_ids)
        rows = conn.execute(
            f"""
            SELECT a.tag_id, a.text, a.source, a.created_at
            FROM annotations a
            JOIN sentences s ON s.id = a.sentence_id
            JOIN documents d ON d.id = s.document_id
            WHERE d.project_id = ? AND s.completed = 1 AND s.answer = 'accept'
              AND a.tag_id IN ({placeholders})
            ORDER BY a.created_at, a.id
            """,
            (project_id, *active_tag_ids),
        ).fetchall()
        for row in rows:
            text = str(row["text"]).strip()
            if not text:
                continue
            examples.setdefault(row["tag_id"], []).append(text)
            if row["source"] == "human":
                corrections.setdefault(row["tag_id"], []).append(text)
        return examples, corrections

    @staticmethod
    def _negative_examples(conn: sqlite3.Connection, project_id: str, limit: int) -> list[str]:
        rows = conn.execute(
            """
            SELECT s.text
            FROM sentences s
            JOIN documents d ON d.id = s.document_id
            WHERE d.project_id = ? AND s.completed = 1 AND s.answer = 'accept'
              AND NOT EXISTS (SELECT 1 FROM annotations a WHERE a.sentence_id = s.id)
            ORDER BY s.sentence_index DESC
            LIMIT ?
            """,
            (project_id, limit),
        ).fetchall()
        return [row["text"] for row in rows]

    def _validate_final_spans(
        self,
        conn: sqlite3.Connection,
        project_id: str,
        sentence_id: str,
        spans: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        tags = {tag["id"] for tag in self.get_tags(conn, project_id)}
        sentence = conn.execute(
            """
            SELECT s.id, d.text AS document_text
            FROM sentences s JOIN documents d ON d.id = s.document_id
            WHERE s.id = ? AND d.project_id = ?
            """,
            (sentence_id, project_id),
        ).fetchone()
        if sentence is None:
            raise self.not_found_error("Sentence not found.")
        normalized: list[dict[str, Any]] = []
        occupied: list[tuple[int, int]] = []
        for raw in spans:
            tag_id = str(raw.get("tag_id") or raw.get("label") or "")
            if tag_id not in tags:
                raise self.validation_error("Corrected assistance span uses an unknown tag.")
            try:
                start_index = int(raw["start_token_index"])
                end_index = int(raw["end_token_index"])
            except (KeyError, TypeError, ValueError) as exc:
                raise self.validation_error("Corrected assistance span has an invalid token range.") from exc
            start_index, end_index = sorted((start_index, end_index))
            if any(left <= end_index and right >= start_index for left, right in occupied):
                raise self.validation_error("Corrected assistance spans overlap.")
            token_rows = conn.execute(
                """
                SELECT token_index, start_char, end_char FROM tokens
                WHERE sentence_id = ? AND token_index BETWEEN ? AND ? ORDER BY token_index
                """,
                (sentence_id, start_index, end_index),
            ).fetchall()
            if len(token_rows) != end_index - start_index + 1:
                raise self.validation_error("Corrected assistance span has an invalid token range.")
            start_char = int(token_rows[0]["start_char"])
            end_char = int(token_rows[-1]["end_char"])
            occupied.append((start_index, end_index))
            normalized.append(
                {
                    "tag_id": tag_id,
                    "start_token_index": start_index,
                    "end_token_index": end_index,
                    "start_char": start_char,
                    "end_char": end_char,
                    "text": sentence["document_text"][start_char:end_char],
                }
            )
        return sorted(normalized, key=lambda item: (item["start_token_index"], item["end_token_index"], item["tag_id"]))

    def _verified_span_record(
        self,
        span: dict[str, Any],
        job: sqlite3.Row,
        tokens: list[sqlite3.Row],
        allowed_tags: set[str],
    ) -> dict[str, Any]:
        tag_id = str(span.get("label") or span.get("tag_id") or "")
        if tag_id not in allowed_tags:
            raise self.validation_error("Assistance draft uses an inactive or unknown tag.")
        try:
            local_start = int(span.get("start", span.get("start_char")))
            local_end = int(span.get("end", span.get("end_char")))
            confidence = float(span.get("confidence", 0.5))
        except (TypeError, ValueError) as exc:
            raise self.validation_error("Assistance draft span is malformed.") from exc
        if local_start < 0 or local_end <= local_start or local_end > len(job["sentence_text"]):
            raise self.validation_error("Assistance draft span offset is invalid.")
        if job["sentence_text"][local_start:local_end] != str(span.get("text") or ""):
            raise self.validation_error("Assistance draft span text does not match its offsets.")
        sentence_start = int(job["sentence_start_char"])
        start_char = sentence_start + local_start
        end_char = sentence_start + local_end
        matching_tokens = [
            token
            for token in tokens
            if int(token["start_char"]) >= start_char and int(token["end_char"]) <= end_char
        ]
        matching = [int(token["token_index"]) for token in matching_tokens]
        if not matching:
            raise self.validation_error("Assistance draft span does not map to tokens.")
        if int(matching_tokens[0]["start_char"]) != start_char or int(matching_tokens[-1]["end_char"]) != end_char:
            raise self.validation_error("Assistance draft span does not align to token boundaries.")
        return {
            "tag_id": tag_id,
            "start_token_index": min(matching),
            "end_token_index": max(matching),
            "start_char": start_char,
            "end_char": end_char,
            "local_start": local_start,
            "local_end": local_end,
            "text": str(span["text"]),
            "confidence": max(0.0, min(confidence, 1.0)),
        }

    def _public_job(self, conn: sqlite3.Connection, row: sqlite3.Row) -> dict[str, Any]:
        suggestions = conn.execute(
            """
            SELECT id, tag_id, start_token_index, end_token_index, start_char, end_char, text, confidence, status
            FROM annotation_suggestions WHERE assistance_job_id = ? ORDER BY start_token_index, end_token_index, id
            """,
            (row["id"],),
        ).fetchall()
        return {
            "id": row["id"],
            "draft_id": row["id"],
            "draft_version": int(row["draft_version"]),
            "document_id": row["document_id"],
            "sentence_id": row["sentence_id"],
            "sentence_index": int(row["sentence_index"]),
            "sentence_text": row["sentence_text"],
            "status": row["status"],
            "queue_order": int(row["queue_order"]),
            "knowledge_revision": int(row["knowledge_revision"]),
            "active_tag_ids": self._json_list(row["active_tag_ids_json"]),
            "model": row["model"],
            "verifier_status": row["verifier_status"],
            "verifier_issues": self._json_list(row["verifier_issues_json"]),
            "attempt_count": int(row["attempt_count"]),
            "error_message": row["error_message"],
            "usage": self._json_object(row["usage_json"]),
            "spans": [self._public_suggestion_span(item) for item in suggestions if item["status"] == "pending"],
        }

    @staticmethod
    def _public_suggestion_span(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "suggestion_id": row["id"],
            "tag_id": row["tag_id"],
            "start_token_index": int(row["start_token_index"]),
            "end_token_index": int(row["end_token_index"]),
            "start_char": int(row["start_char"]),
            "end_char": int(row["end_char"]),
            "text": row["text"],
            "confidence": float(row["confidence"]),
        }

    @staticmethod
    def _public_generated_span(record: dict[str, Any]) -> dict[str, Any]:
        return {
            "suggestion_id": record["id"],
            "tag_id": record["tag_id"],
            "start_token_index": record["start_token_index"],
            "end_token_index": record["end_token_index"],
            "start_char": record["start_char"],
            "end_char": record["end_char"],
            "text": record["text"],
            "confidence": record["confidence"],
        }

    @staticmethod
    def _usage_totals(conn: sqlite3.Connection, project_id: str, document_id: str) -> dict[str, int]:
        totals = {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
            "api_calls": 0,
            "validation_attempts": 0,
            "validation_retries": 0,
        }
        rows = conn.execute(
            "SELECT usage_json FROM assistance_jobs WHERE project_id = ? AND document_id = ? AND usage_json != '{}'",
            (project_id, document_id),
        ).fetchall()
        for row in rows:
            payload = AssistanceService._json_object(row["usage_json"])
            totals["api_calls"] += int(payload.get("api_calls") or 1)
            for key in ("prompt_tokens", "completion_tokens", "total_tokens", "validation_attempts", "validation_retries"):
                totals[key] += int(payload.get(key) or 0)
        return totals

    def _next_queue_order(self, conn: sqlite3.Connection, project_id: str, document_id: str, now: str) -> int:
        row = conn.execute(
            "SELECT queue_sequence FROM assistance_settings WHERE project_id = ? AND document_id = ?",
            (project_id, document_id),
        ).fetchone()
        next_value = int(row["queue_sequence"] if row else 0) + 1
        conn.execute(
            "UPDATE assistance_settings SET queue_sequence = ?, updated_at = ? WHERE project_id = ? AND document_id = ?",
            (next_value, now, project_id, document_id),
        )
        return next_value

    @staticmethod
    def _sentence_has_annotations(conn: sqlite3.Connection, sentence_id: str) -> bool:
        return bool(conn.execute("SELECT 1 FROM annotations WHERE sentence_id = ? LIMIT 1", (sentence_id,)).fetchone())

    @staticmethod
    def _tag_schema_hash(tags: list[dict[str, Any]]) -> str:
        return payload_sha256(
            [
                {
                    "id": tag["id"],
                    "name": tag["name"],
                    "description": tag.get("description"),
                    "taxonomy": tag.get("taxonomy"),
                }
                for tag in sorted(tags, key=lambda item: item["id"])
            ]
        )

    def _require_document(self, conn: sqlite3.Connection, project_id: str, document_id: str) -> sqlite3.Row:
        row = conn.execute(
            "SELECT id FROM documents WHERE id = ? AND project_id = ?",
            (document_id, project_id),
        ).fetchone()
        if row is None:
            raise self.not_found_error("Document not found.")
        return row

    def _cancel_job(self, conn: sqlite3.Connection, job_id: str, reason: str) -> None:
        conn.execute(
            "UPDATE assistance_jobs SET status = 'cancelled', lease_until = NULL, error_message = ?, updated_at = ? WHERE id = ?",
            (reason, self.now(), job_id),
        )

    @staticmethod
    def _normalize_error_reasons(reasons: list[str]) -> list[str]:
        normalized: list[str] = []
        for reason in reasons:
            value = str(reason).strip().lower()
            if value not in ASSISTANCE_ERROR_REASONS:
                raise ValueError(f"Unknown assistance error reason: {value}")
            if value not in normalized:
                normalized.append(value)
        return normalized

    @staticmethod
    def _json_list(value: Any) -> list[Any]:
        try:
            payload = json.loads(value or "[]") if isinstance(value, str) else value
        except (TypeError, ValueError, json.JSONDecodeError):
            return []
        return payload if isinstance(payload, list) else []

    @staticmethod
    def _json_object(value: Any) -> dict[str, Any]:
        try:
            payload = json.loads(value or "{}") if isinstance(value, str) else value
        except (TypeError, ValueError, json.JSONDecodeError):
            return {}
        return payload if isinstance(payload, dict) else {}
