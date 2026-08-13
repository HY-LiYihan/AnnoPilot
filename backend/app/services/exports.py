from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from typing import Any


class ExportService:
    """Build document, Prodigy, and manifest export payloads."""

    def __init__(
        self,
        *,
        get_document: Callable[[str, str], dict[str, Any]],
        get_review_queue: Callable[[str, str, int, str], dict[str, Any]],
        get_goldsmith_human_choices: Callable[[str, str], list[dict[str, Any]]],
        export_event_lines: Callable[[str], list[str]],
        audit_project: Callable[[str], dict[str, Any]],
        export_tag_schema: Callable[[str], dict[str, Any]],
        list_runs: Callable[..., list[dict[str, Any]]],
        list_annotation_imports: Callable[..., dict[str, Any]],
        export_run_provenance: Callable[[str, str], dict[str, Any]],
        now: Callable[[], str],
        task_schema_version: str,
        export_manifest_schema_version: str,
        prodigy_export_schema_version: str,
        prodigy_spans_export_schema_version: str,
        tag_schema_version: str,
        event_schema_version: str,
        run_provenance_schema_version: str,
        goldsmith_review_queue_schema_version: str,
        goldsmith_human_choices_schema_version: str,
        goldsmith_hard_examples_schema_version: str,
        goldsmith_boundary_feedback_schema_version: str,
        goldsmith_consistency_scores_schema_version: str,
        goldsmith_candidate_runs_schema_version: str,
        medium_confidence_threshold: float,
    ) -> None:
        self.get_document = get_document
        self.get_review_queue = get_review_queue
        self.get_goldsmith_human_choices = get_goldsmith_human_choices
        self.export_event_lines = export_event_lines
        self.audit_project = audit_project
        self.export_tag_schema = export_tag_schema
        self.list_runs = list_runs
        self.list_annotation_imports = list_annotation_imports
        self.export_run_provenance = export_run_provenance
        self.now = now
        self.task_schema_version = task_schema_version
        self.export_manifest_schema_version = export_manifest_schema_version
        self.prodigy_export_schema_version = prodigy_export_schema_version
        self.prodigy_spans_export_schema_version = prodigy_spans_export_schema_version
        self.tag_schema_version = tag_schema_version
        self.event_schema_version = event_schema_version
        self.run_provenance_schema_version = run_provenance_schema_version
        self.goldsmith_review_queue_schema_version = goldsmith_review_queue_schema_version
        self.goldsmith_human_choices_schema_version = goldsmith_human_choices_schema_version
        self.goldsmith_hard_examples_schema_version = goldsmith_hard_examples_schema_version
        self.goldsmith_boundary_feedback_schema_version = goldsmith_boundary_feedback_schema_version
        self.goldsmith_consistency_scores_schema_version = goldsmith_consistency_scores_schema_version
        self.goldsmith_candidate_runs_schema_version = goldsmith_candidate_runs_schema_version
        self.medium_confidence_threshold = medium_confidence_threshold

    def export_document_lines(self, project_id: str, document_id: str) -> list[str]:
        document = self.get_document(project_id, document_id)
        lines = []
        for sentence in document["sentences"]:
            spans = [self._export_span(annotation, source=annotation.get("source", "human")) for annotation in sentence["annotations"]]
            suggestions = [self._export_suggestion(suggestion) for suggestion in sentence["suggestions"]]
            line = {
                "schema_version": self.task_schema_version,
                "record_type": "annotation_task",
                "project_id": project_id,
                "document_id": document_id,
                "sentence_id": sentence["id"],
                "sentence_index": sentence["index"],
                "text": sentence["text"],
                "document": {
                    "id": document["document"]["id"],
                    "filename": document["document"]["filename"],
                    "created_at": document["document"]["created_at"],
                },
                "tokens": [self._export_token(token) for token in sentence["tokens"]],
                "spans": spans,
                "annotations": sentence["annotations"],
                "suggestions": suggestions,
                "answer": sentence.get("answer", "accept" if sentence["completed"] else "pending"),
                "completed": sentence["completed"],
                "_view_id": "spans_manual",
                "_session_id": self._export_prodigy_session_id(project_id, document_id, sentence["annotations"]),
                "_annotator_id": self._export_prodigy_annotator_id(sentence["annotations"]),
                "_input_hash": self._stable_hash({"text": sentence["text"]}),
                "_task_hash": self._stable_hash(
                    {
                        "document_id": document_id,
                        "sentence_id": sentence["id"],
                        "text": sentence["text"],
                        "spans": spans,
                        "suggestions": suggestions,
                    }
                ),
                "meta": {
                    "storage": "sqlite_runtime_jsonl_export",
                    "span_count": len(sentence["annotations"]),
                    "suggestion_count": len(sentence["suggestions"]),
                    "session_id": self._export_prodigy_session_id(project_id, document_id, sentence["annotations"]),
                    "annotator_id": self._export_prodigy_annotator_id(sentence["annotations"]),
                },
            }
            lines.append(json.dumps(line, ensure_ascii=False) + "\n")
        return lines

    def export_prodigy_document_lines(self, project_id: str, document_id: str) -> list[str]:
        return self._export_prodigy_document_lines(project_id, document_id, view_id="ner_manual")

    def export_prodigy_spans_document_lines(self, project_id: str, document_id: str) -> list[str]:
        return self._export_prodigy_document_lines(project_id, document_id, view_id="spans_manual")

    def export_manifest(self, project_id: str, document_id: str) -> dict[str, Any]:
        document = self.get_document(project_id, document_id)
        task_lines = self.export_document_lines(project_id, document_id)
        prodigy_lines = self.export_prodigy_document_lines(project_id, document_id)
        prodigy_spans_lines = self.export_prodigy_spans_document_lines(project_id, document_id)
        goldsmith_queue_lines = self.export_goldsmith_review_queue_lines(project_id, document_id, order="hybrid", limit=100)
        goldsmith_choices_lines = self.export_goldsmith_human_choices_lines(project_id, document_id)
        goldsmith_hard_example_lines = self.export_goldsmith_hard_examples_lines(project_id, document_id)
        goldsmith_boundary_feedback_lines = self.export_goldsmith_boundary_feedback_lines(project_id, document_id)
        goldsmith_consistency_score_lines = self.export_goldsmith_consistency_scores_lines(project_id, document_id)
        goldsmith_candidate_run_lines = self.export_goldsmith_candidate_runs_lines(project_id, document_id)
        event_lines = self.export_event_lines(project_id)
        audit_summary = self.audit_project(project_id)
        tag_schema_payload = self.export_tag_schema(project_id)
        tag_schema_line = json.dumps(tag_schema_payload, ensure_ascii=False, sort_keys=True) + "\n"
        runs = self.list_runs(project_id, document_id=document_id, limit=50)
        annotation_imports = self.list_annotation_imports(project_id, document_id=document_id, limit=50)["imports"]
        run_provenance_artifacts: dict[str, dict[str, Any]] = {}
        for run in runs:
            payload = self.export_run_provenance(project_id, run["id"])
            run_provenance_artifacts[run["id"]] = self._artifact_summary(
                filename=f"{run['id']}.provenance.json",
                schema_version=self.run_provenance_schema_version,
                lines=[json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n"],
                content_sha256=payload["content_sha256"],
            )
        source_counts: dict[str, int] = {}
        for sentence in document["sentences"]:
            for annotation in sentence["annotations"]:
                source = annotation.get("source", "human")
                source_counts[source] = source_counts.get(source, 0) + 1

        manifest = {
            "schema_version": self.export_manifest_schema_version,
            "record_type": "export_manifest",
            "generated_at": self.now(),
            "project_id": project_id,
            "document": document["document"],
            "metrics": document["metrics"],
            "tag_count": len(document["tags"]),
            "annotation_source_counts": dict(sorted(source_counts.items())),
            "source_run_ids": [run["id"] for run in runs],
            "runs": runs,
            "annotation_imports": annotation_imports,
            "event_audit": self._manifest_event_audit(audit_summary),
            "run_provenance_artifacts": run_provenance_artifacts,
            "artifacts": {
                "tasks_jsonl": self._artifact_summary(
                    filename=f"{document_id}.jsonl",
                    schema_version=self.task_schema_version,
                    lines=task_lines,
                ),
                "prodigy_jsonl": self._artifact_summary(
                    filename=f"{document_id}.prodigy.jsonl",
                    schema_version=self.prodigy_export_schema_version,
                    lines=prodigy_lines,
                ),
                "prodigy_spans_jsonl": self._artifact_summary(
                    filename=f"{document_id}.prodigy.spans.jsonl",
                    schema_version=self.prodigy_spans_export_schema_version,
                    lines=prodigy_spans_lines,
                ),
                "events_jsonl": self._artifact_summary(
                    filename=f"{project_id}-events.jsonl",
                    schema_version=self.event_schema_version,
                    lines=event_lines,
                ),
                "tag_schema_json": self._artifact_summary(
                    filename=f"{project_id}-tag-schema.json",
                    schema_version=self.tag_schema_version,
                    lines=[tag_schema_line],
                    content_sha256=tag_schema_payload["content_sha256"],
                ),
                "goldsmith_review_queue_jsonl": self._artifact_summary(
                    filename=f"{document_id}.goldsmith.review-queue.jsonl",
                    schema_version=self.goldsmith_review_queue_schema_version,
                    lines=goldsmith_queue_lines,
                ),
                "goldsmith_human_choices_jsonl": self._artifact_summary(
                    filename=f"{document_id}.goldsmith.human-choices.jsonl",
                    schema_version=self.goldsmith_human_choices_schema_version,
                    lines=goldsmith_choices_lines,
                ),
                "goldsmith_hard_examples_jsonl": self._artifact_summary(
                    filename=f"{document_id}.goldsmith.hard-examples.jsonl",
                    schema_version=self.goldsmith_hard_examples_schema_version,
                    lines=goldsmith_hard_example_lines,
                ),
                "goldsmith_boundary_feedback_jsonl": self._artifact_summary(
                    filename=f"{document_id}.goldsmith.boundary-feedback.jsonl",
                    schema_version=self.goldsmith_boundary_feedback_schema_version,
                    lines=goldsmith_boundary_feedback_lines,
                ),
                "goldsmith_consistency_scores_jsonl": self._artifact_summary(
                    filename=f"{document_id}.goldsmith.consistency-scores.jsonl",
                    schema_version=self.goldsmith_consistency_scores_schema_version,
                    lines=goldsmith_consistency_score_lines,
                ),
                "goldsmith_candidate_runs_jsonl": self._artifact_summary(
                    filename=f"{document_id}.goldsmith.candidate-runs.jsonl",
                    schema_version=self.goldsmith_candidate_runs_schema_version,
                    lines=goldsmith_candidate_run_lines,
                ),
            },
        }
        manifest["content_sha256"] = self._payload_sha256(self._manifest_content_payload(manifest))
        return manifest

    def export_goldsmith_review_queue_lines(
        self,
        project_id: str,
        document_id: str,
        *,
        order: str = "hybrid",
        limit: int = 100,
    ) -> list[str]:
        queue = self.get_review_queue(project_id, document_id, limit, order)
        generated_at = self.now()
        lines = []
        for rank, item in enumerate(queue["items"], start=1):
            line = {
                "schema_version": self.goldsmith_review_queue_schema_version,
                "record_type": "human_review_queue_item",
                "generated_at": generated_at,
                "project_id": project_id,
                "document_id": document_id,
                "queue_order": order,
                "rank": rank,
                "sentence_id": item["id"],
                "sentence_index": item["index"],
                "text": item["text"],
                "suggestion_count": item["suggestion_count"],
                "min_confidence": item["min_confidence"],
                "lexical_risk_score": item.get("lexical_risk_score", 0.0),
                "llm_review_risk_score": item.get("llm_review_risk_score", 0.0),
                "risk_score": item["risk_score"],
                "review_route": item["review_route"],
                "first_suggestion": self._export_goldsmith_suggestion(item.get("first_suggestion")),
                "meta": {
                    "source": "annopilot",
                    "artifact": "human_review_queue.jsonl",
                    "total_queue_items": queue["total"],
                },
            }
            lines.append(json.dumps(line, ensure_ascii=False) + "\n")
        return lines

    def export_goldsmith_hard_examples_lines(self, project_id: str, document_id: str) -> list[str]:
        choices = self.get_goldsmith_human_choices(project_id, document_id)
        generated_at = self.now()
        lines = []
        rank = 0
        for choice in choices:
            reasons = self._hard_example_reasons(choice)
            if not reasons:
                continue
            rank += 1
            line = {
                "schema_version": self.goldsmith_hard_examples_schema_version,
                "record_type": "hard_example",
                "generated_at": generated_at,
                "project_id": project_id,
                "document_id": document_id,
                "rank": rank,
                "sentence_id": choice["sentence_id"],
                "sentence_index": choice["sentence_index"],
                "text": choice["sentence_text"],
                "suggestion_id": choice["id"],
                "run_id": choice.get("run_id"),
                "hard_example_reasons": reasons,
                "failure_note": self._hard_example_failure_note(choice, reasons),
                "human_decision": choice["human_decision"],
                "disagreement": choice["disagreement"],
                "span": {
                    "label": choice["tag_name"],
                    "label_id": choice["tag_id"],
                    "text": choice["text"],
                    "start": choice["start_char"],
                    "end": choice["end_char"],
                    "token_start": choice["start_token_index"],
                    "token_end": choice["end_token_index"],
                },
                "suggestion": self._export_goldsmith_suggestion(choice),
                "latest_review": choice.get("latest_review"),
                "meta": {
                    "source": "annopilot",
                    "artifact": "hard_examples.jsonl",
                    "match_key": choice.get("match_key"),
                    "evidence_match_key": choice.get("evidence_match_key"),
                },
            }
            lines.append(json.dumps(line, ensure_ascii=False) + "\n")
        return lines

    def export_goldsmith_boundary_feedback_lines(self, project_id: str, document_id: str) -> list[str]:
        generated_at = self.now()
        lines = []
        rank = 0
        seen_suggestion_ids: set[str] = set()

        for choice in self.get_goldsmith_human_choices(project_id, document_id):
            reasons = self._hard_example_reasons(choice)
            if not reasons:
                continue
            rank += 1
            seen_suggestion_ids.add(choice["id"])
            lines.append(
                json.dumps(
                    self._boundary_feedback_line(
                        project_id=project_id,
                        document_id=document_id,
                        generated_at=generated_at,
                        rank=rank,
                        source_type="human_choice",
                        suggestion=choice,
                        sentence_index=choice["sentence_index"],
                        sentence_text=choice["sentence_text"],
                        reasons=reasons,
                        human_decision=choice["human_decision"],
                        failure_note=self._hard_example_failure_note(choice, reasons),
                    ),
                    ensure_ascii=False,
                )
                + "\n"
            )

        document = self.get_document(project_id, document_id)
        for sentence in document["sentences"]:
            for suggestion in sentence["suggestions"]:
                if suggestion["id"] in seen_suggestion_ids:
                    continue
                reasons = self._pending_boundary_feedback_reasons(suggestion)
                if not reasons:
                    continue
                latest_review = suggestion.get("latest_review") or {}
                rank += 1
                lines.append(
                    json.dumps(
                        self._boundary_feedback_line(
                            project_id=project_id,
                            document_id=document_id,
                            generated_at=generated_at,
                            rank=rank,
                            source_type="llm_reviewed_pending_suggestion",
                            suggestion=suggestion,
                            sentence_index=sentence["index"],
                            sentence_text=sentence["text"],
                            reasons=reasons,
                            human_decision=None,
                            failure_note=self._pending_boundary_feedback_note(suggestion, reasons),
                            feedback_polarity="negative" if latest_review.get("recommendation") == "reject" else "uncertain",
                        ),
                        ensure_ascii=False,
                    )
                    + "\n"
                )
                seen_suggestion_ids.add(suggestion["id"])
        return lines

    def export_goldsmith_human_choices_lines(self, project_id: str, document_id: str) -> list[str]:
        choices = self.get_goldsmith_human_choices(project_id, document_id)
        generated_at = self.now()
        lines = []
        for choice in choices:
            line = {
                "schema_version": self.goldsmith_human_choices_schema_version,
                "record_type": "human_choice",
                "generated_at": generated_at,
                "project_id": project_id,
                "document_id": document_id,
                "sentence_id": choice["sentence_id"],
                "sentence_index": choice["sentence_index"],
                "text": choice["sentence_text"],
                "suggestion_id": choice["id"],
                "run_id": choice.get("run_id"),
                "human_decision": choice["human_decision"],
                "suggestion_status": choice["status"],
                "disagreement": choice["disagreement"],
                "span": {
                    "label": choice["tag_name"],
                    "label_id": choice["tag_id"],
                    "text": choice["text"],
                    "start": choice["start_char"],
                    "end": choice["end_char"],
                    "token_start": choice["start_token_index"],
                    "token_end": choice["end_token_index"],
                },
                "suggestion": self._export_goldsmith_suggestion(choice),
                "latest_review": choice.get("latest_review"),
                "meta": {
                    "source": "annopilot",
                    "artifact": "human_choices.jsonl",
                    "match_key": choice.get("match_key"),
                    "evidence_match_key": choice.get("evidence_match_key"),
                },
            }
            lines.append(json.dumps(line, ensure_ascii=False) + "\n")
        return lines

    def export_goldsmith_consistency_scores_lines(self, project_id: str, document_id: str) -> list[str]:
        document = self.get_document(project_id, document_id)
        generated_at = self.now()
        lines = []
        for sentence in document["sentences"]:
            suggestions = sentence.get("suggestions", [])
            if not suggestions:
                continue
            score = self._goldsmith_consistency_score(suggestions)
            line = {
                "schema_version": self.goldsmith_consistency_scores_schema_version,
                "record_type": "consistency_score",
                "generated_at": generated_at,
                "project_id": project_id,
                "document_id": document_id,
                "sentence_id": sentence["id"],
                "sentence_index": sentence["index"],
                "text": sentence["text"],
                "diagnostic_scope": "visible_pending_suggestions",
                "scoring_mode": "character_rag_llm_review_proxy",
                "score": score["score"],
                "agreement": score["agreement"],
                "exact_match_rate": score["exact_match_rate"],
                "avg_confidence": score["avg_confidence"],
                "avg_rule_risk": score["avg_rule_risk"],
                "overlap_conflict_rate": score["overlap_conflict_rate"],
                "review_risk": score["review_risk"],
                "review_route": score["review_route"],
                "route_reason": score["route_reason"],
                "candidate_count": len(suggestions),
                "reviewed_candidate_count": score["reviewed_candidate_count"],
                "review_counts": score["review_counts"],
                "consensus_signature": score["consensus_signature"],
                "candidate_scores": score["candidate_scores"],
                "meta": {
                    "source": "annopilot",
                    "artifact": "consistency_scores.jsonl",
                    "rosetta_reference": "consistency_scores.jsonl",
                    "note": "Proxy diagnostics from current pending suggestions; full k-run self-consistency can replace this artifact without changing downstream consumers.",
                },
            }
            lines.append(json.dumps(line, ensure_ascii=False) + "\n")
        return lines

    def export_goldsmith_candidate_runs_lines(self, project_id: str, document_id: str) -> list[str]:
        document = self.get_document(project_id, document_id)
        generated_at = self.now()
        lines = []
        for sentence in document["sentences"]:
            tokens = [self._export_prodigy_token(token, sentence["text"], sentence["start_char"]) for token in sentence["tokens"]]
            for index, suggestion in enumerate(sentence.get("suggestions", []), start=1):
                local_start = int(suggestion["start_char"]) - int(sentence["start_char"])
                local_end = int(suggestion["end_char"]) - int(sentence["start_char"])
                span = {
                    "id": f"T{index}",
                    "start": local_start,
                    "end": local_end,
                    "token_start": suggestion["start_token_index"],
                    "token_end": suggestion["end_token_index"],
                    "text": sentence["text"][local_start:local_end],
                    "label": suggestion["tag_name"],
                    "label_id": suggestion["tag_id"],
                    "implicit": False,
                }
                latest_review = suggestion.get("latest_review") or {}
                line = {
                    "schema_version": self.goldsmith_candidate_runs_schema_version,
                    "record_type": "prodigy_candidate",
                    "generated_at": generated_at,
                    "sample_id": sentence["id"],
                    "candidate_id": suggestion["id"],
                    "text": sentence["text"],
                    "tokens": tokens,
                    "spans": [span],
                    "relations": [],
                    "runtime_annotation": {
                        "format": "inline_markup.v1",
                        "annotation_markup": self._inline_span_markup(sentence["text"], local_start, local_end, suggestion["tag_name"]),
                    },
                    "answer": None,
                    "explanation": self._candidate_explanation(suggestion),
                    "model_confidence": suggestion["confidence"],
                    "uncertainty_reason": self._candidate_uncertainty_reason(suggestion),
                    "meta": {
                        "source": "annopilot",
                        "artifact": "candidate_runs.jsonl",
                        "rosetta_reference": "candidate_runs.jsonl",
                        "project_id": project_id,
                        "document_id": document_id,
                        "sentence_id": sentence["id"],
                        "sentence_index": sentence["index"],
                        "suggestion_id": suggestion["id"],
                        "run_id": suggestion.get("run_id"),
                        "tag_id": suggestion["tag_id"],
                        "candidate_source": suggestion.get("source"),
                        "evidence_text": suggestion.get("evidence_text"),
                        "match_key": suggestion.get("match_key"),
                        "evidence_match_key": suggestion.get("evidence_match_key"),
                        "latest_review": latest_review or None,
                    },
                }
                lines.append(json.dumps(line, ensure_ascii=False) + "\n")
        return lines

    def _goldsmith_consistency_score(self, suggestions: list[dict[str, Any]]) -> dict[str, Any]:
        signatures = [self._suggestion_signature(suggestion) for suggestion in suggestions]
        signature_counts: dict[str, int] = {}
        for signature in signatures:
            signature_counts[signature] = signature_counts.get(signature, 0) + 1
        consensus_signature = max(signature_counts, key=lambda key: (signature_counts[key], key))
        exact_match_rate = signature_counts[consensus_signature] / len(suggestions)

        confidences = [float(suggestion.get("confidence") or 0.0) for suggestion in suggestions]
        avg_confidence = sum(confidences) / len(confidences)
        conflicts_by_id = self._suggestion_conflicts_by_id(suggestions)
        total_pairs = len(suggestions) * (len(suggestions) - 1) / 2
        conflict_pair_count = sum(conflicts_by_id.values()) / 2
        overlap_conflict_rate = conflict_pair_count / total_pairs if total_pairs else 0.0
        review_counts = {"accept": 0, "reject": 0, "uncertain": 0}
        review_risk_sum = 0.0
        reviewed_candidate_count = 0
        candidate_scores = []

        for suggestion in suggestions:
            latest_review = suggestion.get("latest_review") or {}
            recommendation = latest_review.get("recommendation")
            review_risk = self._review_recommendation_risk(recommendation)
            if recommendation in review_counts:
                review_counts[recommendation] += 1
                reviewed_candidate_count += 1
            review_risk_sum += review_risk
            candidate_conflicts = conflicts_by_id.get(suggestion["id"], 0)
            candidate_scores.append(
                {
                    "suggestion_id": suggestion["id"],
                    "span_signature": self._suggestion_signature(suggestion),
                    "span_f1_to_consensus": self._span_f1_to_signature(suggestion, consensus_signature),
                    "model_confidence": suggestion.get("confidence"),
                    "review_recommendation": recommendation,
                    "review_risk": round(review_risk, 4),
                    "overlap_conflict_count": candidate_conflicts,
                    "rule_risk": round(min(1.0, review_risk + (0.25 * candidate_conflicts)), 4),
                }
            )

        review_risk = review_risk_sum / len(suggestions)
        agreement = 1.0 - overlap_conflict_rate
        avg_rule_risk = min(1.0, (overlap_conflict_rate * 0.55) + (review_risk * 0.45))
        raw_score = (agreement * 0.5) + (avg_confidence * 0.25) + ((1.0 - review_risk) * 0.15) + (exact_match_rate * 0.1)
        score = max(0.0, min(raw_score, 1.0 - (avg_rule_risk * 0.35)))
        review_route = self._consistency_review_route(score, overlap_conflict_rate, review_risk)
        return {
            "score": round(score, 4),
            "agreement": round(agreement, 4),
            "exact_match_rate": round(exact_match_rate, 4),
            "avg_confidence": round(avg_confidence, 4),
            "avg_rule_risk": round(avg_rule_risk, 4),
            "overlap_conflict_rate": round(overlap_conflict_rate, 4),
            "review_risk": round(review_risk, 4),
            "review_route": review_route,
            "route_reason": self._consistency_route_reason(review_route),
            "reviewed_candidate_count": reviewed_candidate_count,
            "review_counts": review_counts,
            "consensus_signature": consensus_signature,
            "candidate_scores": candidate_scores,
        }

    @classmethod
    def _suggestion_conflicts_by_id(cls, suggestions: list[dict[str, Any]]) -> dict[str, int]:
        conflicts = {suggestion["id"]: 0 for suggestion in suggestions}
        for left_index, left in enumerate(suggestions):
            for right in suggestions[left_index + 1 :]:
                if not cls._suggestions_overlap(left, right):
                    continue
                if cls._suggestion_signature(left) == cls._suggestion_signature(right):
                    continue
                conflicts[left["id"]] += 1
                conflicts[right["id"]] += 1
        return conflicts

    @staticmethod
    def _suggestions_overlap(left: dict[str, Any], right: dict[str, Any]) -> bool:
        return int(left["start_token_index"]) <= int(right["end_token_index"]) and int(left["end_token_index"]) >= int(right["start_token_index"])

    @staticmethod
    def _suggestion_signature(suggestion: dict[str, Any]) -> str:
        return ":".join(
            [
                str(suggestion["tag_id"]),
                str(suggestion["start_token_index"]),
                str(suggestion["end_token_index"]),
                str(suggestion.get("match_key") or suggestion.get("text") or ""),
            ]
        )

    @classmethod
    def _span_f1_to_signature(cls, suggestion: dict[str, Any], signature: str) -> float:
        tag_id, start, end, *_ = signature.split(":", 3)
        if str(suggestion["tag_id"]) != tag_id:
            return 0.0
        left = set(range(int(suggestion["start_token_index"]), int(suggestion["end_token_index"]) + 1))
        right = set(range(int(start), int(end) + 1))
        if not left and not right:
            return 1.0
        if not left or not right:
            return 0.0
        overlap = len(left & right)
        precision = overlap / len(left)
        recall = overlap / len(right)
        return round((2 * precision * recall) / (precision + recall), 4) if precision + recall else 0.0

    @staticmethod
    def _review_recommendation_risk(recommendation: str | None) -> float:
        if recommendation == "reject":
            return 1.0
        if recommendation == "uncertain":
            return 0.6
        return 0.0

    @staticmethod
    def _consistency_review_route(score: float, overlap_conflict_rate: float, review_risk: float) -> str:
        if review_risk >= 0.6 or overlap_conflict_rate >= 0.25 or score < 0.7:
            return "expert_review"
        if score < 0.9 or review_risk > 0:
            return "light_review"
        return "high_confidence_sample"

    @staticmethod
    def _consistency_route_reason(route: str) -> str:
        reasons = {
            "expert_review": "High conflict, low score, or LLM reject/uncertain signal; prioritize for human calibration.",
            "light_review": "Moderate confidence or minor risk; review after expert-priority items.",
            "high_confidence_sample": "High confidence with no overlap conflict or negative LLM review signal; sample for audit.",
        }
        return reasons[route]

    @staticmethod
    def _inline_span_markup(text: str, start: int, end: int, label: str) -> str:
        return f"{text[:start]}[{text[start:end]}]{{{label}}}{text[end:]}"

    @staticmethod
    def _candidate_explanation(suggestion: dict[str, Any]) -> str:
        parts = [f"AnnoPilot candidate from {suggestion.get('source', 'unknown')} retrieval."]
        if suggestion.get("evidence_text"):
            parts.append(f"Matched evidence: {suggestion['evidence_text']}.")
        latest_review = suggestion.get("latest_review") or {}
        if latest_review.get("recommendation"):
            parts.append(f"Latest LLM review: {latest_review['recommendation']}.")
        if latest_review.get("rationale"):
            parts.append(str(latest_review["rationale"]))
        return " ".join(parts)

    @staticmethod
    def _candidate_uncertainty_reason(suggestion: dict[str, Any]) -> str:
        latest_review = suggestion.get("latest_review") or {}
        recommendation = latest_review.get("recommendation")
        if recommendation == "reject":
            return "Latest LLM review rejects this candidate; route for expert calibration."
        if recommendation == "uncertain":
            return "Latest LLM review is uncertain; use as a boundary case."
        confidence = float(suggestion.get("confidence") or 0.0)
        if confidence < 0.75:
            return "Character RAG confidence is below the medium threshold."
        return "Candidate has no negative LLM review signal."

    def _export_prodigy_document_lines(self, project_id: str, document_id: str, view_id: str) -> list[str]:
        document = self.get_document(project_id, document_id)
        lines = []
        document_meta = document["document"]
        for sentence in document["sentences"]:
            spans = [self._export_prodigy_span(annotation, sentence["start_char"]) for annotation in sentence["annotations"]]
            line = {
                "text": sentence["text"],
                "tokens": [
                    self._export_prodigy_token(token, sentence["text"], sentence["start_char"])
                    for token in sentence["tokens"]
                ],
                "spans": spans,
                "answer": self._export_prodigy_answer(sentence),
                "_view_id": view_id,
                "_session_id": self._export_prodigy_session_id(project_id, document_id, sentence["annotations"]),
                "_annotator_id": self._export_prodigy_annotator_id(sentence["annotations"]),
                "_input_hash": self._stable_hash({"text": sentence["text"]}),
                "_task_hash": self._stable_hash(
                    {
                        "document_id": document_id,
                        "sentence_id": sentence["id"],
                        "text": sentence["text"],
                        "spans": spans,
                    }
                ),
                "meta": {
                    "source": "annopilot",
                    "project_id": project_id,
                    "document_id": document_id,
                    "sentence_id": sentence["id"],
                    "sentence_index": sentence["index"],
                    "filename": document_meta["filename"],
                    "completed": sentence["completed"],
                    "answer": sentence.get("answer", "accept" if sentence["completed"] else "pending"),
                    "suggestion_count": len(sentence["suggestions"]),
                    "annotation_sources": [
                        self._export_prodigy_annotation_source(annotation)
                        for annotation in sentence["annotations"]
                    ],
                },
            }
            lines.append(json.dumps(line, ensure_ascii=False) + "\n")
        return lines

    @staticmethod
    def _manifest_event_audit(audit_summary: dict[str, Any]) -> dict[str, Any]:
        return {
            "project_id": audit_summary["project_id"],
            "event_count": audit_summary["event_count"],
            "pending_outbox_count": audit_summary["pending_outbox_count"],
            "invalid_event_count": audit_summary["invalid_event_count"],
            "legacy_event_count": audit_summary.get("legacy_event_count", 0),
            "non_replayable_event_count": audit_summary.get("non_replayable_event_count", 0),
            "replay_issue_counts": audit_summary.get("replay_issue_counts", {}),
            "schema_versions": audit_summary["schema_versions"],
            "event_types": audit_summary["event_types"],
            "actor_type_counts": audit_summary.get("actor_type_counts", {}),
            "actor_id_counts": audit_summary.get("actor_id_counts", {}),
            "last_event_type": audit_summary["last_event_type"],
            "last_event_ts": audit_summary["last_event_ts"],
            "rebuild_status": audit_summary["rebuild_status"],
        }

    @staticmethod
    def _manifest_content_payload(manifest: dict[str, Any]) -> dict[str, Any]:
        payload = json.loads(
            json.dumps(
                {key: value for key, value in manifest.items() if key not in {"generated_at", "content_sha256"}},
                ensure_ascii=False,
            )
        )
        for group_name in ("artifacts", "run_provenance_artifacts"):
            group = payload.get(group_name)
            if not isinstance(group, dict):
                continue
            for artifact in group.values():
                if isinstance(artifact, dict) and artifact.get("content_sha256"):
                    artifact.pop("sha256", None)
        return payload

    @staticmethod
    def _export_goldsmith_suggestion(suggestion: dict[str, Any] | None) -> dict[str, Any] | None:
        if not suggestion:
            return None
        return {
            "id": suggestion["id"],
            "run_id": suggestion.get("run_id"),
            "tag_id": suggestion["tag_id"],
            "tag_name": suggestion["tag_name"],
            "text": suggestion["text"],
            "confidence": suggestion["confidence"],
            "source": suggestion["source"],
            "evidence_text": suggestion.get("evidence_text"),
            "start_token_index": suggestion["start_token_index"],
            "end_token_index": suggestion["end_token_index"],
            "start_char": suggestion["start_char"],
            "end_char": suggestion["end_char"],
            "context_before": suggestion.get("context_before"),
            "context_after": suggestion.get("context_after"),
            "latest_review": suggestion.get("latest_review"),
        }

    def _hard_example_reasons(self, choice: dict[str, Any]) -> list[str]:
        reasons: list[str] = []
        latest_review = choice.get("latest_review") or {}
        if choice.get("disagreement"):
            reasons.append("llm_human_disagreement")
        if choice.get("human_decision") == "reject":
            reasons.append("human_rejected_suggestion")
        if float(choice.get("confidence") or 0.0) < self.medium_confidence_threshold:
            reasons.append("low_character_rag_confidence")
        if latest_review.get("recommendation") == "uncertain":
            reasons.append("llm_uncertain")
        return reasons

    @staticmethod
    def _hard_example_failure_note(choice: dict[str, Any], reasons: list[str]) -> str:
        notes = []
        latest_review = choice.get("latest_review") or {}
        if "llm_human_disagreement" in reasons:
            notes.append(
                f"LLM recommended {latest_review.get('recommendation')} but human chose {choice.get('human_decision')}; inspect guideline boundary."
            )
        if "human_rejected_suggestion" in reasons:
            notes.append("Human rejected this suggestion; keep it as a negative example for the same label/text boundary.")
        if "low_character_rag_confidence" in reasons:
            notes.append("Character RAG confidence was below the medium threshold; review lexical seed quality and span boundary.")
        if "llm_uncertain" in reasons:
            notes.append("LLM marked the case uncertain; clarify label definition or add bilingual examples.")
        return " ".join(notes) or "Hard example selected for guideline calibration."

    def _boundary_feedback_line(
        self,
        *,
        project_id: str,
        document_id: str,
        generated_at: str,
        rank: int,
        source_type: str,
        suggestion: dict[str, Any],
        sentence_index: int,
        sentence_text: str,
        reasons: list[str],
        human_decision: str | None,
        failure_note: str,
        feedback_polarity: str | None = None,
    ) -> dict[str, Any]:
        polarity = feedback_polarity or self._boundary_feedback_polarity(human_decision, suggestion.get("latest_review"))
        return {
            "schema_version": self.goldsmith_boundary_feedback_schema_version,
            "record_type": "boundary_feedback",
            "generated_at": generated_at,
            "project_id": project_id,
            "document_id": document_id,
            "rank": rank,
            "source_type": source_type,
            "feedback_polarity": polarity,
            "sentence_id": suggestion["sentence_id"],
            "sentence_index": sentence_index,
            "text": sentence_text,
            "suggestion_id": suggestion["id"],
            "run_id": suggestion.get("run_id"),
            "hard_example_reasons": reasons,
            "failure_note": failure_note,
            "human_decision": human_decision,
            "suggestion_status": suggestion.get("status"),
            "span": {
                "label": suggestion["tag_name"],
                "label_id": suggestion["tag_id"],
                "text": suggestion["text"],
                "start": suggestion["start_char"],
                "end": suggestion["end_char"],
                "token_start": suggestion["start_token_index"],
                "token_end": suggestion["end_token_index"],
            },
            "suggestion": self._export_goldsmith_suggestion(suggestion),
            "latest_review": suggestion.get("latest_review"),
            "meta": {
                "source": "annopilot",
                "artifact": "boundary_feedback.jsonl",
                "match_key": suggestion.get("match_key"),
                "evidence_match_key": suggestion.get("evidence_match_key"),
            },
        }

    def _pending_boundary_feedback_reasons(self, suggestion: dict[str, Any]) -> list[str]:
        latest_review = suggestion.get("latest_review") or {}
        recommendation = latest_review.get("recommendation")
        if recommendation not in {"reject", "uncertain"}:
            return []
        reasons = ["llm_rejected_pending_suggestion" if recommendation == "reject" else "llm_uncertain"]
        if float(suggestion.get("confidence") or 0.0) < self.medium_confidence_threshold:
            reasons.append("low_character_rag_confidence")
        return reasons

    @staticmethod
    def _pending_boundary_feedback_note(suggestion: dict[str, Any], reasons: list[str]) -> str:
        latest_review = suggestion.get("latest_review") or {}
        notes = []
        if "llm_rejected_pending_suggestion" in reasons:
            notes.append("LLM rejected this still-pending suggestion; use it as boundary feedback before human resolution.")
        if "llm_uncertain" in reasons:
            notes.append("LLM marked this still-pending suggestion uncertain; route it for guideline or bilingual example calibration.")
        if "low_character_rag_confidence" in reasons:
            notes.append("Character RAG confidence was below the medium threshold; inspect lexical seed and span boundary.")
        if latest_review.get("rationale"):
            notes.append(f"Latest LLM rationale: {latest_review['rationale']}")
        return " ".join(notes) or "Pending reviewed suggestion selected for boundary feedback."

    @staticmethod
    def _boundary_feedback_polarity(human_decision: str | None, latest_review: dict[str, Any] | None) -> str:
        if human_decision == "reject":
            return "negative"
        if human_decision == "accept":
            return "positive"
        recommendation = (latest_review or {}).get("recommendation")
        if recommendation == "reject":
            return "negative"
        if recommendation == "uncertain":
            return "uncertain"
        return "mixed"

    @staticmethod
    def _export_token(token: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": token["id"],
            "text": token["text"],
            "index": token["token_index"],
            "start": token["start_char"],
            "end": token["end_char"],
        }

    @staticmethod
    def _export_span(annotation: dict[str, Any], source: str) -> dict[str, Any]:
        span = {
            "id": annotation["id"],
            "label": annotation["tag_name"],
            "label_id": annotation["tag_id"],
            "text": annotation["text"],
            "start": annotation["start_char"],
            "end": annotation["end_char"],
            "token_start": annotation["start_token_index"],
            "token_end": annotation["end_token_index"],
            "source": source,
        }
        if annotation.get("source_suggestion_id"):
            span["source_suggestion_id"] = annotation["source_suggestion_id"]
        return span

    @classmethod
    def _export_suggestion(cls, suggestion: dict[str, Any]) -> dict[str, Any]:
        return {
            **cls._export_span(suggestion, source="character_rag"),
            "run_id": suggestion.get("run_id"),
            "confidence": suggestion["confidence"],
            "match_source": suggestion["source"],
            "evidence_text": suggestion.get("evidence_text"),
            "match_key": suggestion.get("match_key"),
            "evidence_match_key": suggestion.get("evidence_match_key"),
            "context_before": suggestion.get("context_before"),
            "context_after": suggestion.get("context_after"),
            "status": suggestion["status"],
            "latest_review": suggestion.get("latest_review"),
        }

    @staticmethod
    def _export_prodigy_token(token: dict[str, Any], sentence_text: str, sentence_start_char: int) -> dict[str, Any]:
        local_start = token["start_char"] - sentence_start_char
        local_end = token["end_char"] - sentence_start_char
        return {
            "text": token["text"],
            "start": local_start,
            "end": local_end,
            "id": token["token_index"],
            "ws": local_end < len(sentence_text) and sentence_text[local_end].isspace(),
        }

    @staticmethod
    def _export_prodigy_span(annotation: dict[str, Any], sentence_start_char: int) -> dict[str, Any]:
        return {
            "start": annotation["start_char"] - sentence_start_char,
            "end": annotation["end_char"] - sentence_start_char,
            "token_start": annotation["start_token_index"],
            "token_end": annotation["end_token_index"],
            "label": annotation["tag_name"],
        }

    @staticmethod
    def _export_prodigy_annotation_source(annotation: dict[str, Any]) -> dict[str, Any]:
        source = {
            "annotation_id": annotation["id"],
            "label_id": annotation["tag_id"],
            "source": annotation.get("source", "human"),
        }
        if annotation.get("source_suggestion_id"):
            source["source_suggestion_id"] = annotation["source_suggestion_id"]
        return source

    @staticmethod
    def _export_prodigy_answer(sentence: dict[str, Any]) -> str:
        answer = sentence.get("answer") or ("accept" if sentence.get("completed") else "pending")
        if answer == "pending" and sentence.get("annotations"):
            return "accept"
        return "ignore" if answer == "pending" else answer

    @classmethod
    def _export_prodigy_session_id(cls, project_id: str, document_id: str, annotations: list[dict[str, Any]]) -> str:
        return f"annopilot-{project_id}-{document_id}-{cls._export_prodigy_annotation_channel(annotations)}"

    @classmethod
    def _export_prodigy_annotator_id(cls, annotations: list[dict[str, Any]]) -> str:
        return f"annopilot-{cls._export_prodigy_annotation_channel(annotations)}"

    @staticmethod
    def _export_prodigy_annotation_channel(annotations: list[dict[str, Any]]) -> str:
        sources = {annotation.get("source", "human") for annotation in annotations}
        if not sources:
            return "unannotated"
        if sources == {"human"}:
            return "human"
        if sources == {"accepted_suggestion"}:
            return "character-rag"
        return "mixed"

    @staticmethod
    def _artifact_summary(
        filename: str,
        schema_version: str,
        lines: list[str],
        content_sha256: str | None = None,
    ) -> dict[str, Any]:
        content = "".join(lines)
        encoded = content.encode("utf-8")
        summary = {
            "filename": filename,
            "schema_version": schema_version,
            "line_count": len(lines),
            "byte_count": len(encoded),
            "sha256": hashlib.sha256(encoded).hexdigest(),
        }
        if content_sha256 is not None:
            summary["content_sha256"] = content_sha256
        return summary

    @staticmethod
    def _payload_sha256(payload: dict[str, Any]) -> str:
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    @staticmethod
    def _stable_hash(payload: dict[str, Any]) -> int:
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        value = int.from_bytes(hashlib.blake2b(encoded, digest_size=4).digest(), byteorder="big", signed=False)
        if value >= 2**31:
            return value - 2**32
        return value
