from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Callable
from typing import Any

from ..hashing import payload_sha256


class AnnotationImportService:
    """Import Prodigy/AnnoPilot JSONL annotations into an existing document."""

    def __init__(
        self,
        connect: Callable[[], sqlite3.Connection],
        *,
        new_id: Callable[[str], str],
        now: Callable[[], str],
        enqueue_event: Callable[[sqlite3.Connection, str, dict[str, Any]], dict[str, Any]],
        flush_event_outbox: Callable[[str], int],
        seed_tags: Callable[[sqlite3.Connection, str], list[dict[str, Any]]],
        list_tags_from_conn: Callable[[sqlite3.Connection, str], list[dict[str, Any]]],
        get_tags: Callable[[str], list[dict[str, Any]]],
        tag_colors: list[str],
        max_jsonl_bytes: int,
        not_found_error: type[Exception],
        validation_error: type[Exception],
    ) -> None:
        self.connect = connect
        self.new_id = new_id
        self.now = now
        self.enqueue_event = enqueue_event
        self.flush_event_outbox = flush_event_outbox
        self.seed_tags = seed_tags
        self.list_tags_from_conn = list_tags_from_conn
        self.get_tags = get_tags
        self.tag_colors = tag_colors
        self.max_jsonl_bytes = max_jsonl_bytes
        self.not_found_error = not_found_error
        self.validation_error = validation_error

    def import_annotations_jsonl(self, project_id: str, document_id: str, filename: str, data: bytes) -> dict[str, Any]:
        if len(data) > self.max_jsonl_bytes:
            raise self.validation_error("JSONL file is larger than the 10 MB limit.")
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise self.validation_error("JSONL file must be valid UTF-8.") from exc
        records = self._parse_annotation_jsonl(text)
        if not records:
            raise self.validation_error("JSONL file does not contain annotation records.")

        source_sha256 = hashlib.sha256(data).hexdigest()
        matched_count = 0
        skipped_count = 0
        created_tag_count = 0
        created_annotation_count = 0
        deleted_annotation_count = 0
        completed_sentence_count = 0
        source_record_results: list[dict[str, Any]] = []
        now = self.now()

        with self.connect() as conn:
            document = conn.execute(
                "SELECT id, text FROM documents WHERE id = ? AND project_id = ?",
                (document_id, project_id),
            ).fetchone()
            if document is None:
                raise self.not_found_error("Document not found.")
            self.seed_tags(conn, project_id)

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
            token_rows = []
            if sentence_ids:
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

            tags = self.list_tags_from_conn(conn, project_id)
            tags_by_id = {tag["id"]: tag for tag in tags}
            tags_by_name = {tag["name"].casefold(): tag for tag in tags}
            used_shortcuts = {tag["shortcut"] for tag in tags}

            def ensure_import_tag(label: str) -> dict[str, Any]:
                nonlocal created_tag_count, tags, used_shortcuts
                normalized_label = label.strip()
                if not normalized_label:
                    raise self.validation_error("Imported span label is required.")
                existing_tag = tags_by_id.get(normalized_label) or tags_by_name.get(normalized_label.casefold())
                if existing_tag:
                    return existing_tag
                tag_id = self.new_id("tag")
                shortcut = self._unique_shortcut(None, used_shortcuts)
                color = self.tag_colors[len(tags) % len(self.tag_colors)]
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
                self.enqueue_event(
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
                    "record_sha256": payload_sha256(record),
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
                except self.validation_error as exc:
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
                    self.enqueue_event(
                        conn,
                        project_id,
                        {"type": "annotation.deleted", "annotation_id": annotation["id"], "sentence_id": annotation["sentence_id"]},
                    )
                    deleted_annotation_count += 1

                created_for_record_ids: list[str] = []
                if answer == "accept":
                    for spec in annotation_specs:
                        tag = ensure_import_tag(spec["label"])
                        annotation_id = self.new_id("ann")
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
                        self.enqueue_event(
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
                self.enqueue_event(
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

            self.enqueue_event(
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

    def _parse_annotation_jsonl(self, text: str) -> list[tuple[int, dict[str, Any]]]:
        records: list[tuple[int, dict[str, Any]]] = []
        for line_number, line in enumerate(text.splitlines(), start=1):
            stripped = line.strip()
            if not stripped:
                continue
            try:
                record = json.loads(stripped)
            except json.JSONDecodeError as exc:
                raise self.validation_error(f"Invalid JSONL at line {line_number}.") from exc
            if not isinstance(record, dict):
                raise self.validation_error(f"JSONL line {line_number} must be an object.")
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

    def _normalize_import_answer(self, value: Any, has_spans: bool) -> str:
        if value is None or str(value).strip() == "":
            return "accept" if has_spans else "pending"
        normalized = str(value).strip().lower()
        if normalized not in {"accept", "reject", "ignore", "pending"}:
            raise self.validation_error("Imported answer must be accept, reject, ignore, or pending.")
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

    def _build_import_annotation_spec(
        self,
        span: dict[str, Any],
        sentence: dict[str, Any],
        tokens: list[dict[str, Any]],
        document_text: str,
    ) -> dict[str, Any]:
        if not isinstance(span, dict):
            raise self.validation_error("Imported span must be an object.")
        label = self._import_span_label(span)
        start_token_index, end_token_index = self._import_span_token_range(span, sentence, tokens)
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

    def _import_span_label(self, span: dict[str, Any]) -> str:
        label = span.get("label") or span.get("label_id")
        if label is None or str(label).strip() == "":
            raise self.validation_error("Imported span label is required.")
        return str(label).strip()

    def _import_span_token_range(
        self,
        span: dict[str, Any],
        sentence: dict[str, Any],
        tokens: list[dict[str, Any]],
    ) -> tuple[int, int]:
        if not tokens:
            raise self.validation_error("Imported span cannot be mapped because the sentence has no tokens.")
        token_by_index = {token["token_index"]: token for token in tokens}
        start_value = span.get("token_start", span.get("start_token_index"))
        end_value = span.get("token_end", span.get("end_token_index"))
        if start_value is not None and end_value is not None:
            start_index = self._import_int(start_value, "token_start")
            end_index = self._import_int(end_value, "token_end")
            if start_index > end_index or start_index not in token_by_index or end_index not in token_by_index:
                raise self.validation_error("Imported span token range is invalid.")
            return start_index, end_index

        if "start" not in span or "end" not in span:
            raise self.validation_error("Imported span must include token range or character offsets.")
        raw_start = self._import_int(span["start"], "start")
        raw_end = self._import_int(span["end"], "end")
        if raw_start >= raw_end:
            raise self.validation_error("Imported span character range is invalid.")

        sentence_start = sentence["start_char"]
        sentence_end = sentence["end_char"]
        if 0 <= raw_start < raw_end <= len(sentence["text"]):
            start_char = sentence_start + raw_start
            end_char = sentence_start + raw_end
        elif sentence_start <= raw_start < raw_end <= sentence_end:
            start_char = raw_start
            end_char = raw_end
        else:
            raise self.validation_error("Imported span character range is outside the matched sentence.")

        overlapping = [token for token in tokens if token["start_char"] < end_char and token["end_char"] > start_char]
        if not overlapping:
            raise self.validation_error("Imported span character range does not overlap sentence tokens.")
        return overlapping[0]["token_index"], overlapping[-1]["token_index"]

    def _import_int(self, value: Any, field_name: str) -> int:
        try:
            return int(value)
        except (TypeError, ValueError) as exc:
            raise self.validation_error(f"Imported {field_name} must be an integer.") from exc

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
    def _row_dict(row: sqlite3.Row, exclude: set[str] | None = None) -> dict[str, Any]:
        excluded = exclude or set()
        return {key: row[key] for key in row.keys() if key not in excluded}
