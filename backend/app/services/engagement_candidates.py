from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from typing import Any

from ..engagement_candidates import (
    ENGAGEMENT_CANDIDATE_SCHEMA_VERSION,
    ENGAGEMENT_VERIFIER_SCHEMA_VERSION,
    build_engagement_generation_prompt,
    parse_engagement_candidate,
    score_candidate_consistency,
)
from ..hashing import payload_sha256


class EngagementCandidateService:
    """Run K same-config Engagement candidates and persist a verifier gate."""

    def __init__(
        self,
        connect: Callable[[], sqlite3.Connection],
        *,
        new_id: Callable[[str], str],
        now: Callable[[], str],
        enqueue_event: Callable[[sqlite3.Connection, str, dict[str, Any]], dict[str, Any]],
        flush_event_outbox: Callable[[str], int],
        get_tags: Callable[[sqlite3.Connection, str], list[dict[str, Any]]],
        generator: Any | None,
        not_found_error: type[Exception],
        validation_error: type[Exception],
    ) -> None:
        self.connect = connect
        self.new_id = new_id
        self.now = now
        self.enqueue_event = enqueue_event
        self.flush_event_outbox = flush_event_outbox
        self.get_tags = get_tags
        self.generator = generator
        self.not_found_error = not_found_error
        self.validation_error = validation_error

    def generate(
        self,
        project_id: str,
        document_id: str,
        *,
        candidate_count: int = 3,
        temperature: float = 0.7,
        sentence_id: str | None = None,
        generator: Any | None = None,
    ) -> dict[str, Any]:
        if candidate_count < 3 or candidate_count > 7:
            raise self.validation_error("candidate_count must be between 3 and 7.")
        temperature = max(0.0, min(float(temperature), 1.5))
        run_id = self.new_id("run")
        now = self.now()
        all_groups: list[dict[str, Any]] = []
        suggestion_ids: list[str] = []
        sentence_prompt_hashes: dict[str, str] = {}
        active_generator = generator or self.generator
        if active_generator is None:
            raise self.validation_error("Engagement candidate generator is not configured.")
        model = str(getattr(active_generator, "model", "unknown"))

        with self.connect() as conn:
            document = conn.execute(
                "SELECT id FROM documents WHERE id = ? AND project_id = ?",
                (document_id, project_id),
            ).fetchone()
            if document is None:
                raise self.not_found_error("Document not found.")
            tags = self.get_tags(conn, project_id)
            if not tags:
                raise self.validation_error("At least one tag is required before generating Engagement candidates.")
            label_to_id = self._label_map(tags)
            sentence_rows = self._sentence_rows(conn, document_id, sentence_id)
            if sentence_id is not None and not sentence_rows:
                raise self.not_found_error("Sentence not found.")
            if not sentence_rows:
                return {
                    "run_id": run_id,
                    "candidate_count": candidate_count,
                    "sentence_count": 0,
                    "groups": [],
                    "suggestions": [],
                }

            token_rows = self._token_rows(conn, [row["id"] for row in sentence_rows])
            tokens_by_sentence: dict[str, list[dict[str, Any]]] = {}
            for row in token_rows:
                tokens_by_sentence.setdefault(row["sentence_id"], []).append(self._row_dict(row))
            annotation_examples_by_tag = self._annotation_rows(conn, [row["id"] for row in sentence_rows])
            examples_by_tag = self._examples_by_tag(tags, annotation_examples_by_tag)
            language = self._language_mode([row["text"] for row in sentence_rows])
            run_config = {
                "schema_version": ENGAGEMENT_CANDIDATE_SCHEMA_VERSION,
                "verifier_schema_version": ENGAGEMENT_VERIFIER_SCHEMA_VERSION,
                "recipe": "llm_engagement_consistency",
                "candidate_count": candidate_count,
                "temperature": temperature,
                "model": model,
                "language_mode": language,
                "same_prompt_config_per_sentence": True,
                "auto_accept_policy": "only_high_consistency_and_verifier_pass",
            }
            conn.execute(
                """
                INSERT INTO annotation_runs (
                  id, project_id, document_id, recipe, config_json, input_count, suggestion_count, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, 0, ?)
                """,
                (run_id, project_id, document_id, "llm_engagement_consistency", json.dumps(run_config, ensure_ascii=False), len(sentence_rows), now),
            )
            for sentence in sentence_rows:
                conn.execute(
                    "INSERT INTO annotation_run_sentences (run_id, sentence_id) VALUES (?, ?)",
                    (run_id, sentence["id"]),
                )

                sentence_tokens = tokens_by_sentence.get(sentence["id"], [])
                local_tokens = [
                    {**token, "start_char": token["start_char"] - sentence["start_char"], "end_char": token["end_char"] - sentence["start_char"]}
                    for token in sentence_tokens
                ]
                prompt = build_engagement_generation_prompt(
                    sentence["text"],
                    tags,
                    language=language,
                    examples_by_tag=examples_by_tag,
                )
                prompt_sha256 = payload_sha256({"prompt": prompt, "temperature": temperature, "model": model})
                sentence_prompt_hashes[sentence["id"]] = prompt_sha256
                sentence_groups: list[dict[str, Any]] = []
                for candidate_index in range(candidate_count):
                    raw_response = active_generator.generate(prompt, temperature)
                    candidate, issues = parse_engagement_candidate(
                        raw_response,
                        source_text=sentence["text"],
                        label_to_id=label_to_id,
                        tokens=local_tokens,
                    )
                    has_error = any(issue.get("severity") == "error" for issue in issues)
                    if not candidate.get("spans"):
                        has_error = True
                    group_id = self.new_id("candidate")
                    group = {
                        "id": group_id,
                        "run_id": run_id,
                        "sentence_id": sentence["id"],
                        "candidate_index": candidate_index,
                        "model": model,
                        "temperature": temperature,
                        "prompt_sha256": prompt_sha256,
                        "source_text": sentence["text"],
                        "raw_response": candidate.get("raw_response", ""),
                        "explanation": candidate.get("explanation", ""),
                        "spans": candidate.get("spans", []),
                        "verifier_status": "failed" if has_error else "passed",
                        "verifier_issues": issues,
                        "consistency": {},
                        "created_at": now,
                    }
                    sentence_groups.append(group)
                    all_groups.append(group)
                    conn.execute(
                        """
                        INSERT INTO annotation_candidate_groups (
                          id, run_id, sentence_id, candidate_index, model, temperature, prompt_sha256,
                          source_text, raw_response, explanation, spans_json, verifier_status,
                          verifier_issues_json, consistency_json, created_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            group["id"], group["run_id"], group["sentence_id"], group["candidate_index"], group["model"],
                            group["temperature"], group["prompt_sha256"], group["source_text"], group["raw_response"],
                            group["explanation"], json.dumps(group["spans"], ensure_ascii=False), group["verifier_status"],
                            json.dumps(group["verifier_issues"], ensure_ascii=False), "{}", now,
                        ),
                    )

                consistency = score_candidate_consistency(sentence_groups, candidate_count)
                for group in sentence_groups:
                    group["consistency"] = consistency.to_dict()
                    conn.execute(
                        "UPDATE annotation_candidate_groups SET consistency_json = ? WHERE id = ?",
                        (json.dumps(group["consistency"], ensure_ascii=False), group["id"]),
                    )
                    if group["verifier_status"] != "passed":
                        continue
                    for span in group["spans"]:
                        suggestion_id = self.new_id("sug")
                        suggestion_ids.append(suggestion_id)
                        start_char = sentence["start_char"] + int(span["start"])
                        end_char = sentence["start_char"] + int(span["end"])
                        start_token_index, end_token_index = self._token_range(local_tokens, int(span["start"]), int(span["end"]))
                        conn.execute(
                            """
                            INSERT INTO annotation_suggestions (
                              id, run_id, sentence_id, tag_id, start_token_index, end_token_index,
                              start_char, end_char, text, confidence, source, evidence_text,
                              context_before, context_after, candidate_group_id, status, created_at
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?)
                            """,
                            (
                                suggestion_id, run_id, sentence["id"], span["label"], start_token_index, end_token_index,
                                start_char, end_char, span["text"], float(span["confidence"]), "llm_engagement", span["text"],
                                sentence["text"][: int(span["start"])][-48:], sentence["text"][int(span["end"]) :][:48], group["id"], now,
                            ),
                        )
                        self._snapshot_span(
                            conn,
                            run_id,
                            group["id"],
                            sentence,
                            span,
                            start_token_index,
                            end_token_index,
                            tags,
                            now,
                        )

            conn.execute("UPDATE annotation_runs SET config_json = ?, suggestion_count = ?, snapshot_complete = 1 WHERE id = ?", (
                json.dumps({**run_config, "prompt_sha256_by_sentence": sentence_prompt_hashes}, ensure_ascii=False),
                len(suggestion_ids),
                run_id,
            ))
            self.enqueue_event(
                conn,
                project_id,
                {
                    "type": "engagement.candidates.generated",
                    "document_id": document_id,
                    "run_id": run_id,
                    "candidate_count": candidate_count,
                    "sentence_count": len(sentence_rows),
                    "suggestion_count": len(suggestion_ids),
                    "groups": [self._public_group(group) for group in all_groups],
                },
            )

        self.flush_event_outbox(project_id)
        return {
            "run_id": run_id,
            "candidate_count": candidate_count,
            "sentence_count": len(sentence_rows),
            "groups": [self._public_group(group) for group in all_groups],
            "suggestions": self._get_suggestions(project_id, suggestion_ids),
        }

    def _get_suggestions(self, project_id: str, suggestion_ids: list[str]) -> list[dict[str, Any]]:
        if not suggestion_ids:
            return []
        placeholders = ",".join("?" for _ in suggestion_ids)
        with self.connect() as conn:
            rows = conn.execute(
                f"""
                SELECT sg.id, sg.run_id, sg.sentence_id, sg.tag_id, tags.name AS tag_name, tags.color AS tag_color,
                       sg.start_token_index, sg.end_token_index, sg.start_char, sg.end_char, sg.text, sg.confidence,
                       sg.source, sg.evidence_text, sg.status, sg.created_at, sg.candidate_group_id,
                       cg.candidate_index, cg.verifier_status, cg.consistency_json
                FROM annotation_suggestions sg
                JOIN sentences s ON s.id = sg.sentence_id
                JOIN documents d ON d.id = s.document_id
                JOIN tags ON tags.id = sg.tag_id AND tags.project_id = d.project_id
                LEFT JOIN annotation_candidate_groups cg ON cg.id = sg.candidate_group_id
                WHERE d.project_id = ? AND sg.id IN ({placeholders})
                ORDER BY s.sentence_index, sg.start_token_index, cg.candidate_index, sg.id
                """,
                (project_id, *suggestion_ids),
            ).fetchall()
        return [
            {
                "id": row["id"], "run_id": row["run_id"], "sentence_id": row["sentence_id"], "tag_id": row["tag_id"],
                "tag_name": row["tag_name"], "tag_color": row["tag_color"], "start_token_index": row["start_token_index"],
                "end_token_index": row["end_token_index"], "start_char": row["start_char"], "end_char": row["end_char"],
                "text": row["text"], "confidence": row["confidence"], "source": row["source"],
                "evidence_text": row["evidence_text"], "status": row["status"], "created_at": row["created_at"],
                "candidate_group_id": row["candidate_group_id"], "candidate_index": row["candidate_index"],
                "verifier_status": row["verifier_status"],
                "consistency_route": self._consistency_route(row["consistency_json"]),
                "auto_accept_eligible": self._consistency_auto_accept(row["consistency_json"]),
            }
            for row in rows
        ]

    @staticmethod
    def _sentence_rows(conn: sqlite3.Connection, document_id: str, sentence_id: str | None) -> list[sqlite3.Row]:
        if sentence_id:
            return conn.execute(
                "SELECT id, sentence_index, text, start_char, end_char FROM sentences WHERE document_id = ? AND id = ?",
                (document_id, sentence_id),
            ).fetchall()
        return conn.execute(
            "SELECT id, sentence_index, text, start_char, end_char FROM sentences WHERE document_id = ? AND completed = 0 ORDER BY sentence_index",
            (document_id,),
        ).fetchall()

    @staticmethod
    def _token_rows(conn: sqlite3.Connection, sentence_ids: list[str]) -> list[sqlite3.Row]:
        if not sentence_ids:
            return []
        placeholders = ",".join("?" for _ in sentence_ids)
        return conn.execute(
            f"SELECT id, sentence_id, token_index, text, start_char, end_char FROM tokens WHERE sentence_id IN ({placeholders}) ORDER BY sentence_id, token_index",
            tuple(sentence_ids),
        ).fetchall()

    @staticmethod
    def _annotation_rows(conn: sqlite3.Connection, sentence_ids: list[str]) -> dict[str, list[str]]:
        if not sentence_ids:
            return {}
        placeholders = ",".join("?" for _ in sentence_ids)
        rows = conn.execute(
            f"SELECT tag_id, text FROM annotations WHERE sentence_id IN ({placeholders}) ORDER BY created_at",
            tuple(sentence_ids),
        ).fetchall()
        result: dict[str, list[str]] = {}
        for row in rows:
            result.setdefault(row["tag_id"], []).append(row["text"])
        return result

    @staticmethod
    def _examples_by_tag(tags: list[dict[str, Any]], annotations: dict[str, list[str]]) -> dict[str, list[str]]:
        result: dict[str, list[str]] = {}
        for tag in tags:
            tag_id = str(tag["id"])
            result[tag_id] = [str(value) for value in (tag.get("examples") or []) if str(value).strip()]
            result[tag_id].extend(str(value) for value in annotations.get(tag_id, []) if str(value).strip())
        return result

    @staticmethod
    def _label_map(tags: list[dict[str, Any]]) -> dict[str, str]:
        result: dict[str, str] = {}
        for tag in tags:
            tag_id, name = str(tag["id"]), str(tag["name"])
            result[tag_id] = tag_id
            result[name] = tag_id
            result[name.casefold()] = tag_id
        return result

    @staticmethod
    def _language_mode(texts: list[str]) -> str:
        combined = "".join(texts)
        has_cjk = any("\u3400" <= char <= "\u9fff" for char in combined)
        has_latin = any(char.isascii() and char.isalpha() for char in combined)
        return "bilingual" if has_cjk and has_latin else "zh" if has_cjk else "en"

    @staticmethod
    def _token_range(tokens: list[dict[str, Any]], start: int, end: int) -> tuple[int, int]:
        matching = [token["token_index"] for token in tokens if int(token["start_char"]) >= start and int(token["end_char"]) <= end]
        if not matching:
            raise ValueError("Verified Engagement span does not map to tokens.")
        return int(min(matching)), int(max(matching))

    @staticmethod
    def _snapshot_span(
        conn: sqlite3.Connection,
        run_id: str,
        candidate_group_id: str,
        sentence: sqlite3.Row,
        span: dict[str, Any],
        start_token: int,
        end_token: int,
        tags: list[dict[str, Any]],
        created_at: str,
    ) -> None:
        tag = next((item for item in tags if item["id"] == span["label"]), None)
        conn.execute(
            """
            INSERT INTO annotation_run_candidate_spans (
              id, run_id, sentence_id, candidate_group_id, tag_id, tag_name, start_token_index, end_token_index,
              start_char, end_char, text, confidence, source, evidence_text, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                f"{candidate_group_id}:{span['start']}:{span['end']}:{span['label']}:{start_token}", run_id, sentence["id"], candidate_group_id, span["label"],
                tag["name"] if tag else span["label"], start_token, end_token, sentence["start_char"] + int(span["start"]),
                sentence["start_char"] + int(span["end"]), span["text"], float(span["confidence"]), "llm_engagement", span["text"], created_at,
            ),
        )

    @staticmethod
    def _public_group(group: dict[str, Any]) -> dict[str, Any]:
        return {
            key: group[key]
            for key in ("id", "run_id", "sentence_id", "candidate_index", "model", "temperature", "prompt_sha256", "explanation", "spans", "verifier_status", "verifier_issues", "consistency", "created_at")
        }

    @staticmethod
    def _row_dict(row: sqlite3.Row) -> dict[str, Any]:
        return {key: row[key] for key in row.keys()}

    @staticmethod
    def _consistency_route(value: str | None) -> str:
        try:
            return str(json.loads(value or "{}").get("route") or "")
        except (TypeError, ValueError, json.JSONDecodeError):
            return ""

    @staticmethod
    def _consistency_auto_accept(value: str | None) -> bool:
        try:
            return bool(json.loads(value or "{}").get("auto_accept_eligible", False))
        except (TypeError, ValueError, json.JSONDecodeError):
            return False
