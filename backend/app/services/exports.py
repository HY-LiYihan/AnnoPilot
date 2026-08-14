from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable
from io import BytesIO
from typing import Any
from zipfile import ZIP_DEFLATED, ZipFile

from ..hashing import payload_sha256
from .bootstrap_report import GoldsmithBootstrapReportService
from .export_verification import ExportVerificationService


GOLDSMITH_LABEL_TOKEN_PATTERN = re.compile(r"[A-Za-z0-9_]+|[\u4e00-\u9fff]")


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
        list_candidate_run_snapshots: Callable[..., list[dict[str, Any]]],
        list_annotation_imports: Callable[..., dict[str, Any]],
        export_run_provenance: Callable[[str, str], dict[str, Any]],
        now: Callable[[], str],
        task_schema_version: str,
        export_manifest_schema_version: str,
        prodigy_export_schema_version: str,
        prodigy_spans_export_schema_version: str,
        prodigy_labels_schema_version: str,
        tag_schema_version: str,
        event_schema_version: str,
        run_provenance_schema_version: str,
        goldsmith_review_queue_schema_version: str,
        goldsmith_human_choices_schema_version: str,
        goldsmith_hard_examples_schema_version: str,
        goldsmith_boundary_feedback_schema_version: str,
        goldsmith_consistency_scores_schema_version: str,
        goldsmith_candidate_runs_schema_version: str,
        goldsmith_risk_reasons_schema_version: str,
        goldsmith_label_statistics_schema_version: str,
        goldsmith_contrastive_examples_schema_version: str,
        goldsmith_reflection_plans_schema_version: str,
        goldsmith_prompt_package_schema_version: str,
        goldsmith_review_tasks_schema_version: str,
        goldsmith_verification_report_schema_version: str,
        goldsmith_bootstrap_report_schema_version: str,
        medium_confidence_threshold: float,
    ) -> None:
        self.get_document = get_document
        self.get_review_queue = get_review_queue
        self.get_goldsmith_human_choices = get_goldsmith_human_choices
        self.export_event_lines = export_event_lines
        self.audit_project = audit_project
        self.export_tag_schema = export_tag_schema
        self.list_runs = list_runs
        self.list_candidate_run_snapshots = list_candidate_run_snapshots
        self.list_annotation_imports = list_annotation_imports
        self.export_run_provenance = export_run_provenance
        self.now = now
        self.task_schema_version = task_schema_version
        self.export_manifest_schema_version = export_manifest_schema_version
        self.prodigy_export_schema_version = prodigy_export_schema_version
        self.prodigy_spans_export_schema_version = prodigy_spans_export_schema_version
        self.prodigy_labels_schema_version = prodigy_labels_schema_version
        self.tag_schema_version = tag_schema_version
        self.event_schema_version = event_schema_version
        self.run_provenance_schema_version = run_provenance_schema_version
        self.goldsmith_review_queue_schema_version = goldsmith_review_queue_schema_version
        self.goldsmith_human_choices_schema_version = goldsmith_human_choices_schema_version
        self.goldsmith_hard_examples_schema_version = goldsmith_hard_examples_schema_version
        self.goldsmith_boundary_feedback_schema_version = goldsmith_boundary_feedback_schema_version
        self.goldsmith_consistency_scores_schema_version = goldsmith_consistency_scores_schema_version
        self.goldsmith_candidate_runs_schema_version = goldsmith_candidate_runs_schema_version
        self.goldsmith_risk_reasons_schema_version = goldsmith_risk_reasons_schema_version
        self.goldsmith_label_statistics_schema_version = goldsmith_label_statistics_schema_version
        self.goldsmith_contrastive_examples_schema_version = goldsmith_contrastive_examples_schema_version
        self.goldsmith_reflection_plans_schema_version = goldsmith_reflection_plans_schema_version
        self.goldsmith_prompt_package_schema_version = goldsmith_prompt_package_schema_version
        self.goldsmith_review_tasks_schema_version = goldsmith_review_tasks_schema_version
        self.goldsmith_verification_report_schema_version = goldsmith_verification_report_schema_version
        self.export_verification_service = ExportVerificationService(goldsmith_verification_report_schema_version)
        self.goldsmith_bootstrap_report_schema_version = goldsmith_bootstrap_report_schema_version
        self.bootstrap_report_service = GoldsmithBootstrapReportService(goldsmith_bootstrap_report_schema_version)
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

    def export_prodigy_labels(self, project_id: str) -> dict[str, Any]:
        tag_schema = self.export_tag_schema(project_id)
        labels = [tag["name"] for tag in tag_schema["tags"]]
        payload = {
            "schema_version": self.prodigy_labels_schema_version,
            "record_type": "prodigy_labels",
            "generated_at": self.now(),
            "project_id": project_id,
            "tag_schema_sha256": tag_schema["content_sha256"],
            "label_count": len(labels),
            "labels": labels,
            "labels_csv": ",".join(labels),
            "label_definitions": [
                {
                    "id": tag["id"],
                    "name": tag["name"],
                    "description": tag.get("description"),
                    "examples": tag.get("examples", []),
                    "taxonomy": tag.get("taxonomy"),
                    "shortcut": tag.get("shortcut"),
                    "color": tag.get("color"),
                }
                for tag in tag_schema["tags"]
            ],
            "command_templates": {},
        }
        payload["command_templates"] = {
            "ner_manual": f'prodigy ner.manual <dataset> blank:en <document>.prodigy.jsonl --label "{payload["labels_csv"]}"',
            "spans_manual": f'prodigy spans.manual <dataset> blank:en <document>.prodigy.spans.jsonl --label "{payload["labels_csv"]}"',
        }
        payload["content_sha256"] = payload_sha256(self._prodigy_labels_content_payload(payload))
        return payload

    def export_manifest(self, project_id: str, document_id: str) -> dict[str, Any]:
        return self._build_export_manifest_context(project_id, document_id)["manifest"]

    def export_prodigy_bundle_bytes(self, project_id: str, document_id: str) -> bytes:
        context = self._build_export_manifest_context(project_id, document_id)
        manifest = context["manifest"]
        artifacts = manifest["artifacts"]
        artifact_contents = {
            "tasks_jsonl": "".join(context["task_lines"]),
            "prodigy_jsonl": "".join(context["prodigy_lines"]),
            "prodigy_spans_jsonl": "".join(context["prodigy_spans_lines"]),
            "prodigy_labels_json": context["prodigy_labels_line"],
            "events_jsonl": "".join(context["event_lines"]),
            "tag_schema_json": context["tag_schema_line"],
            "goldsmith_review_queue_jsonl": "".join(context["goldsmith_queue_lines"]),
            "goldsmith_human_choices_jsonl": "".join(context["goldsmith_choices_lines"]),
            "goldsmith_hard_examples_jsonl": "".join(context["goldsmith_hard_example_lines"]),
            "goldsmith_boundary_feedback_jsonl": "".join(context["goldsmith_boundary_feedback_lines"]),
            "goldsmith_consistency_scores_jsonl": "".join(context["goldsmith_consistency_score_lines"]),
            "goldsmith_candidate_runs_jsonl": "".join(context["goldsmith_candidate_run_lines"]),
            "goldsmith_risk_reasons_jsonl": "".join(context["goldsmith_risk_reason_lines"]),
            "goldsmith_label_statistics_jsonl": "".join(context["goldsmith_label_statistics_lines"]),
            "goldsmith_contrastive_examples_jsonl": "".join(context["goldsmith_contrastive_example_lines"]),
            "goldsmith_reflection_plans_jsonl": "".join(context["goldsmith_reflection_plan_lines"]),
            "goldsmith_prompt_package_jsonl": "".join(context["goldsmith_prompt_package_lines"]),
            "goldsmith_review_tasks_jsonl": "".join(context["goldsmith_review_task_lines"]),
            "goldsmith_verification_report_jsonl": "".join(context["goldsmith_verification_report_lines"]),
            "goldsmith_bootstrap_report_md": "".join(context["goldsmith_bootstrap_report_lines"]),
        }
        bundle_files: dict[str, str] = {
            "README.txt": self._prodigy_bundle_readme(project_id, document_id, manifest),
            f"{document_id}.manifest.json": json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        }
        bundle_files.update({artifacts[key]["filename"]: content for key, content in artifact_contents.items()})
        bundle_files.update(context["run_provenance_lines"])
        buffer = BytesIO()
        with ZipFile(buffer, "w", compression=ZIP_DEFLATED) as archive:
            for filename, content in bundle_files.items():
                archive.writestr(filename, content.encode("utf-8"))
        return buffer.getvalue()

    def _build_export_manifest_context(self, project_id: str, document_id: str) -> dict[str, Any]:
        document = self.get_document(project_id, document_id)
        task_lines = self.export_document_lines(project_id, document_id)
        prodigy_lines = self.export_prodigy_document_lines(project_id, document_id)
        prodigy_spans_lines = self.export_prodigy_spans_document_lines(project_id, document_id)
        prodigy_labels_payload = self.export_prodigy_labels(project_id)
        prodigy_labels_line = json.dumps(prodigy_labels_payload, ensure_ascii=False, sort_keys=True) + "\n"
        goldsmith_queue_lines = self.export_goldsmith_review_queue_lines(project_id, document_id, order="hybrid", limit=100)
        goldsmith_choices_lines = self.export_goldsmith_human_choices_lines(project_id, document_id)
        goldsmith_hard_example_lines = self.export_goldsmith_hard_examples_lines(project_id, document_id)
        goldsmith_boundary_feedback_lines = self.export_goldsmith_boundary_feedback_lines(project_id, document_id)
        goldsmith_consistency_score_lines = self.export_goldsmith_consistency_scores_lines(project_id, document_id)
        goldsmith_candidate_run_lines = self.export_goldsmith_candidate_runs_lines(project_id, document_id)
        goldsmith_label_statistics_lines = self.export_goldsmith_label_statistics_lines(project_id, document_id)
        goldsmith_contrastive_example_lines = self.export_goldsmith_contrastive_examples_lines(project_id, document_id)
        goldsmith_reflection_plan_lines = self.export_goldsmith_reflection_plan_lines(project_id, document_id)
        goldsmith_prompt_package_lines = self.export_goldsmith_prompt_package_lines(project_id, document_id)
        goldsmith_review_task_lines = self.export_goldsmith_review_task_lines(project_id, document_id)
        goldsmith_risk_reason_lines = self._build_goldsmith_risk_reason_lines(
            project_id=project_id,
            document_id=document_id,
            document=document,
            generated_at=self.now(),
            review_queue_lines=goldsmith_queue_lines,
            hard_example_lines=goldsmith_hard_example_lines,
            boundary_feedback_lines=goldsmith_boundary_feedback_lines,
        )
        event_lines = self.export_event_lines(project_id)
        audit_summary = self.audit_project(project_id)
        tag_schema_payload = self.export_tag_schema(project_id)
        tag_schema_line = json.dumps(tag_schema_payload, ensure_ascii=False, sort_keys=True) + "\n"
        goldsmith_verification_report_lines = self._build_goldsmith_verification_report_lines(
            project_id=project_id,
            document_id=document_id,
            document=document,
            generated_at=self.now(),
            tag_schema=tag_schema_payload,
            prodigy_lines=prodigy_lines,
            prodigy_spans_lines=prodigy_spans_lines,
            goldsmith_candidate_run_lines=goldsmith_candidate_run_lines,
            goldsmith_review_task_lines=goldsmith_review_task_lines,
            goldsmith_prompt_package_lines=goldsmith_prompt_package_lines,
        )
        verification_summary = self._verification_summary_from_lines(goldsmith_verification_report_lines)
        prodigy_readiness = self._prodigy_readiness(document["metrics"], verification_summary=verification_summary)
        goldsmith_bootstrap_report_lines = self._build_goldsmith_bootstrap_report_lines(
            project_id=project_id,
            document_id=document_id,
            document=document,
            generated_at=self.now(),
            prodigy_readiness=prodigy_readiness,
            goldsmith_review_queue_lines=goldsmith_queue_lines,
            goldsmith_consistency_score_lines=goldsmith_consistency_score_lines,
            goldsmith_label_statistics_lines=goldsmith_label_statistics_lines,
            goldsmith_reflection_plan_lines=goldsmith_reflection_plan_lines,
            goldsmith_review_task_lines=goldsmith_review_task_lines,
            goldsmith_verification_report_lines=goldsmith_verification_report_lines,
        )
        runs = self.list_runs(project_id, document_id=document_id, limit=50)
        annotation_imports = self.list_annotation_imports(project_id, document_id=document_id, limit=50)["imports"]
        run_provenance_artifacts: dict[str, dict[str, Any]] = {}
        run_provenance_lines: dict[str, str] = {}
        for run in runs:
            payload = self.export_run_provenance(project_id, run["id"])
            filename = f"{run['id']}.provenance.json"
            line = json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n"
            run_provenance_lines[filename] = line
            run_provenance_artifacts[run["id"]] = self._artifact_summary(
                filename=filename,
                schema_version=self.run_provenance_schema_version,
                lines=[line],
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
            "prodigy_readiness": prodigy_readiness,
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
                "prodigy_labels_json": self._artifact_summary(
                    filename=f"{project_id}-prodigy-labels.json",
                    schema_version=self.prodigy_labels_schema_version,
                    lines=[prodigy_labels_line],
                    content_sha256=prodigy_labels_payload["content_sha256"],
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
                    content_sha256=self._jsonl_content_sha256(goldsmith_queue_lines),
                ),
                "goldsmith_human_choices_jsonl": self._artifact_summary(
                    filename=f"{document_id}.goldsmith.human-choices.jsonl",
                    schema_version=self.goldsmith_human_choices_schema_version,
                    lines=goldsmith_choices_lines,
                    content_sha256=self._jsonl_content_sha256(goldsmith_choices_lines),
                ),
                "goldsmith_hard_examples_jsonl": self._artifact_summary(
                    filename=f"{document_id}.goldsmith.hard-examples.jsonl",
                    schema_version=self.goldsmith_hard_examples_schema_version,
                    lines=goldsmith_hard_example_lines,
                    content_sha256=self._jsonl_content_sha256(goldsmith_hard_example_lines),
                ),
                "goldsmith_boundary_feedback_jsonl": self._artifact_summary(
                    filename=f"{document_id}.goldsmith.boundary-feedback.jsonl",
                    schema_version=self.goldsmith_boundary_feedback_schema_version,
                    lines=goldsmith_boundary_feedback_lines,
                    content_sha256=self._jsonl_content_sha256(goldsmith_boundary_feedback_lines),
                ),
                "goldsmith_consistency_scores_jsonl": self._artifact_summary(
                    filename=f"{document_id}.goldsmith.consistency-scores.jsonl",
                    schema_version=self.goldsmith_consistency_scores_schema_version,
                    lines=goldsmith_consistency_score_lines,
                    content_sha256=self._jsonl_content_sha256(goldsmith_consistency_score_lines),
                ),
                "goldsmith_candidate_runs_jsonl": self._artifact_summary(
                    filename=f"{document_id}.goldsmith.candidate-runs.jsonl",
                    schema_version=self.goldsmith_candidate_runs_schema_version,
                    lines=goldsmith_candidate_run_lines,
                    content_sha256=self._jsonl_content_sha256(goldsmith_candidate_run_lines),
                ),
                "goldsmith_risk_reasons_jsonl": self._artifact_summary(
                    filename=f"{document_id}.goldsmith.risk-reasons.jsonl",
                    schema_version=self.goldsmith_risk_reasons_schema_version,
                    lines=goldsmith_risk_reason_lines,
                    content_sha256=self._jsonl_content_sha256(goldsmith_risk_reason_lines),
                ),
                "goldsmith_label_statistics_jsonl": self._artifact_summary(
                    filename=f"{document_id}.goldsmith.label-statistics.jsonl",
                    schema_version=self.goldsmith_label_statistics_schema_version,
                    lines=goldsmith_label_statistics_lines,
                    content_sha256=self._jsonl_content_sha256(goldsmith_label_statistics_lines),
                ),
                "goldsmith_contrastive_examples_jsonl": self._artifact_summary(
                    filename=f"{document_id}.goldsmith.contrastive-examples.jsonl",
                    schema_version=self.goldsmith_contrastive_examples_schema_version,
                    lines=goldsmith_contrastive_example_lines,
                    content_sha256=self._jsonl_content_sha256(goldsmith_contrastive_example_lines),
                ),
                "goldsmith_reflection_plans_jsonl": self._artifact_summary(
                    filename=f"{document_id}.goldsmith.reflection-plans.jsonl",
                    schema_version=self.goldsmith_reflection_plans_schema_version,
                    lines=goldsmith_reflection_plan_lines,
                    content_sha256=self._jsonl_content_sha256(goldsmith_reflection_plan_lines),
                ),
                "goldsmith_prompt_package_jsonl": self._artifact_summary(
                    filename=f"{document_id}.goldsmith.prompt-package.jsonl",
                    schema_version=self.goldsmith_prompt_package_schema_version,
                    lines=goldsmith_prompt_package_lines,
                    content_sha256=self._jsonl_content_sha256(goldsmith_prompt_package_lines),
                ),
                "goldsmith_review_tasks_jsonl": self._artifact_summary(
                    filename=f"{document_id}.goldsmith.review-tasks.jsonl",
                    schema_version=self.goldsmith_review_tasks_schema_version,
                    lines=goldsmith_review_task_lines,
                    content_sha256=self._jsonl_content_sha256(goldsmith_review_task_lines),
                ),
                "goldsmith_verification_report_jsonl": self._artifact_summary(
                    filename=f"{document_id}.goldsmith.verification-report.jsonl",
                    schema_version=self.goldsmith_verification_report_schema_version,
                    lines=goldsmith_verification_report_lines,
                    content_sha256=self._jsonl_content_sha256(goldsmith_verification_report_lines),
                ),
                "goldsmith_bootstrap_report_md": self._artifact_summary(
                    filename=f"{document_id}.goldsmith.bootstrap-report.md",
                    schema_version=self.goldsmith_bootstrap_report_schema_version,
                    lines=goldsmith_bootstrap_report_lines,
                    content_sha256=self._markdown_content_sha256(goldsmith_bootstrap_report_lines),
                ),
            },
        }
        manifest["content_sha256"] = payload_sha256(self._manifest_content_payload(manifest))
        return {
            "manifest": manifest,
            "task_lines": task_lines,
            "prodigy_lines": prodigy_lines,
            "prodigy_spans_lines": prodigy_spans_lines,
            "prodigy_labels_line": prodigy_labels_line,
            "goldsmith_queue_lines": goldsmith_queue_lines,
            "goldsmith_choices_lines": goldsmith_choices_lines,
            "goldsmith_hard_example_lines": goldsmith_hard_example_lines,
            "goldsmith_boundary_feedback_lines": goldsmith_boundary_feedback_lines,
            "goldsmith_consistency_score_lines": goldsmith_consistency_score_lines,
            "goldsmith_candidate_run_lines": goldsmith_candidate_run_lines,
            "goldsmith_risk_reason_lines": goldsmith_risk_reason_lines,
            "goldsmith_label_statistics_lines": goldsmith_label_statistics_lines,
            "goldsmith_contrastive_example_lines": goldsmith_contrastive_example_lines,
            "goldsmith_reflection_plan_lines": goldsmith_reflection_plan_lines,
            "goldsmith_prompt_package_lines": goldsmith_prompt_package_lines,
            "goldsmith_review_task_lines": goldsmith_review_task_lines,
            "goldsmith_verification_report_lines": goldsmith_verification_report_lines,
            "goldsmith_bootstrap_report_lines": goldsmith_bootstrap_report_lines,
            "event_lines": event_lines,
            "tag_schema_line": tag_schema_line,
            "run_provenance_lines": run_provenance_lines,
        }

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
                "priority": item.get("priority", 0),
                "min_confidence": item["min_confidence"],
                "lexical_risk_score": item.get("lexical_risk_score", 0.0),
                "llm_review_risk_score": item.get("llm_review_risk_score", 0.0),
                "judge_review_risk_score": item.get("judge_review_risk_score", 0.0),
                "candidate_disagreement_score": item.get("candidate_disagreement_score", 0.0),
                "risk_score": item["risk_score"],
                "risk_reason_codes": item.get("risk_reason_codes", []),
                "review_route": item["review_route"],
                "rosetta_route": item.get("rosetta_route", "medium"),
                "action_hint": item.get("action_hint", ""),
                "review_guidance": item.get("review_guidance", {}),
                "first_suggestion": self._export_goldsmith_suggestion(item.get("first_suggestion")),
                "candidate_suggestions": [
                    self._export_goldsmith_suggestion(suggestion)
                    for suggestion in item.get("candidate_suggestions", [])
                ],
                "meta": {
                    "source": "annopilot",
                    "artifact": "human_review_queue.jsonl",
                    "total_queue_items": queue["total"],
                    "rosetta_route_counts": queue.get("rosetta_route_counts", {}),
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
                "risk_reason_codes": choice.get("risk_reason_codes", []),
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
                "risk_reason_codes": choice.get("risk_reason_codes", []),
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

    def export_goldsmith_label_statistics_lines(self, project_id: str, document_id: str) -> list[str]:
        document = self.get_document(project_id, document_id)
        generated_at = self.now()
        context_window = 2
        stats, sentence_count, annotation_count = self._goldsmith_label_statistics(document, context_window=context_window)

        lines = []
        for token, counts in sorted(stats.items()):
            entity_count = int(counts["entity_count"])
            context_count = int(counts["context_count"])
            other_count = int(counts["other_count"])
            total = entity_count + context_count + other_count
            line = {
                "schema_version": self.goldsmith_label_statistics_schema_version,
                "record_type": "token_label_stat",
                "generated_at": generated_at,
                "project_id": project_id,
                "document_id": document_id,
                "token": token,
                "entity_count": entity_count,
                "context_count": context_count,
                "other_count": other_count,
                "total": total,
                "entity_probability": round(self._safe_ratio(entity_count, total), 4),
                "context_probability": round(self._safe_ratio(context_count, total), 4),
                "other_probability": round(self._safe_ratio(other_count, total), 4),
                "label_entity_counts": dict(sorted(counts["label_entity_counts"].items())),
                "meta": {
                    "source": "annopilot",
                    "artifact": "label_statistics.jsonl",
                    "rosetta_reference": "label_statistics.json",
                    "tokenizer": "rosetta_label_statistics_compatible_v1",
                    "context_window": context_window,
                    "sentence_count": sentence_count,
                    "annotation_count": annotation_count,
                },
            }
            lines.append(json.dumps(line, ensure_ascii=False) + "\n")
        return lines

    def export_goldsmith_contrastive_examples_lines(
        self,
        project_id: str,
        document_id: str,
        similar_k: int = 3,
        boundary_k: int = 1,
    ) -> list[str]:
        similar_limit = max(0, min(int(similar_k), 10))
        boundary_limit = max(0, min(int(boundary_k), 10))
        document = self.get_document(project_id, document_id)
        generated_at = self.now()
        samples = [
            self._goldsmith_contrastive_sample(project_id, document_id, sentence)
            for sentence in document["sentences"]
            if sentence.get("annotations")
        ]
        sample_by_id = {sample["id"]: sample for sample in samples}
        lines = []
        for query in samples:
            scored = [
                (candidate, self._lexical_similarity(query["text"], candidate["text"]))
                for candidate in samples
                if candidate["id"] != query["id"]
            ]
            similar_hits = [
                self._contrastive_hit("similar", candidate, score)
                for candidate, score in sorted(scored, key=lambda item: (-item[1], item[0]["id"]))[:similar_limit]
            ]
            similar_ids = {hit["sample_id"] for hit in similar_hits}
            boundary_hits = [
                self._contrastive_hit("boundary", candidate, score)
                for candidate, score in sorted(scored, key=lambda item: (item[1], item[0]["id"]))
                if candidate["id"] not in similar_ids
            ][:boundary_limit]
            line = {
                "schema_version": self.goldsmith_contrastive_examples_schema_version,
                "record_type": "contrastive_selection",
                "generated_at": generated_at,
                "project_id": project_id,
                "document_id": document_id,
                "query_id": query["id"],
                "query": sample_by_id[query["id"]],
                "similar": similar_hits,
                "boundary": boundary_hits,
                "meta": {
                    "source": "annopilot",
                    "artifact": "contrastive_examples.jsonl",
                    "rosetta_reference": "contrastive_retrieval.py",
                    "selection_strategy": "lexical_jaccard_tokens",
                    "tokenizer": "rosetta_contrastive_retrieval_compatible_v1",
                    "similar_k": similar_limit,
                    "boundary_k": boundary_limit,
                    "candidate_sample_count": len(samples),
                },
            }
            lines.append(json.dumps(line, ensure_ascii=False) + "\n")
        return lines

    def export_goldsmith_reflection_plan_lines(self, project_id: str, document_id: str) -> list[str]:
        document = self.get_document(project_id, document_id)
        generated_at = self.now()
        entity_threshold = 0.6
        max_items = 8
        context_window = 2
        lines = []
        for sentence in document["sentences"]:
            tokens = self._goldsmith_label_tokens(sentence["text"])
            if not tokens:
                continue
            stats, stats_sentence_count, stats_annotation_count = self._goldsmith_label_statistics(
                document,
                context_window=context_window,
                exclude_sentence_id=sentence["id"],
                annotated_only=True,
            )
            spans = self._goldsmith_annotation_spans(sentence)
            predicted_token_indices = {
                index
                for index, token in enumerate(tokens)
                if any(self._goldsmith_token_overlaps_span(token, span) for span in spans)
            }
            items: list[dict[str, Any]] = []
            for index, token in enumerate(tokens):
                stat = stats.get(token["token"])
                if stat is None and index not in predicted_token_indices:
                    items.append(
                        {
                            "item_type": "unseen_token",
                            "token": token["token"],
                            "start": token["start"],
                            "end": token["end"],
                            "reason": "token 未出现在 gold/high-confidence 样本统计中，且当前未被标注",
                        }
                    )
                elif stat is not None and index not in predicted_token_indices:
                    entity_probability = self._goldsmith_stat_probability(stat, "entity_count")
                    if entity_probability >= entity_threshold:
                        items.append(
                            {
                                "item_type": "possible_false_negative",
                                "token": token["token"],
                                "start": token["start"],
                                "end": token["end"],
                                "reason": f"历史统计中 entity_probability={entity_probability:.2f}，但当前未标注",
                            }
                        )

            for span in spans:
                edge_tokens = [token for token in tokens if self._goldsmith_token_overlaps_span(token, span)]
                for token in edge_tokens[:1] + edge_tokens[-1:]:
                    stat = stats.get(token["token"])
                    if stat is None:
                        continue
                    entity_probability = self._goldsmith_stat_probability(stat, "entity_count")
                    context_probability = self._goldsmith_stat_probability(stat, "context_count")
                    if context_probability > entity_probability:
                        items.append(
                            {
                                "item_type": "boundary_token",
                                "token": token["token"],
                                "start": token["start"],
                                "end": token["end"],
                                "reason": (
                                    "token 在历史统计中更常作为 context "
                                    f"({context_probability:.2f}) 而非 entity ({entity_probability:.2f})"
                                ),
                            }
                        )

            deduped_items = self._dedupe_reflection_items(items, max_items=max_items)
            if not deduped_items:
                continue
            item_counts: dict[str, int] = {}
            for item in deduped_items:
                item_counts[item["item_type"]] = item_counts.get(item["item_type"], 0) + 1
            line = {
                "schema_version": self.goldsmith_reflection_plans_schema_version,
                "record_type": "reflection_plan",
                "generated_at": generated_at,
                "project_id": project_id,
                "document_id": document_id,
                "sentence_id": sentence["id"],
                "sentence_index": sentence["index"],
                "text": sentence["text"],
                "sample_id": sentence["id"],
                "candidate_id": f"current-annotations:{sentence['id']}",
                "candidate": {
                    "id": f"current-annotations:{sentence['id']}",
                    "source": "current_annotations",
                    "spans": spans,
                    "span_count": len(spans),
                },
                "items": deduped_items,
                "item_counts": dict(sorted(item_counts.items())),
                "meta": {
                    "source": "annopilot",
                    "artifact": "reflection_plans.jsonl",
                    "rosetta_reference": "reflection.py",
                    "tokenizer": "rosetta_label_statistics_compatible_v1",
                    "stats_scope": "leave_one_out_annotated_sentences",
                    "entity_threshold": entity_threshold,
                    "max_items": max_items,
                    "context_window": context_window,
                    "stats_sentence_count": stats_sentence_count,
                    "stats_annotation_count": stats_annotation_count,
                    "stats_token_count": len(stats),
                },
            }
            lines.append(json.dumps(line, ensure_ascii=False) + "\n")
        return lines

    def export_goldsmith_consistency_scores_lines(self, project_id: str, document_id: str) -> list[str]:
        document = self.get_document(project_id, document_id)
        run_candidates_by_sentence = self._candidate_run_snapshots_by_sentence(project_id, document_id)
        generated_at = self.now()
        lines = []
        for sentence in document["sentences"]:
            suggestions = sentence.get("suggestions", [])
            run_candidates = run_candidates_by_sentence.get(sentence["id"], [])
            if run_candidates:
                score = self._goldsmith_run_consistency_score(run_candidates)
                diagnostic_scope = "run_candidate_snapshots"
                scoring_mode = "k_run_self_consistency" if len(run_candidates) > 1 else "single_run_candidate_snapshot"
                candidate_count = len(run_candidates)
                note = "Sentence-level candidate outputs are grouped by immutable annotation run snapshots."
            elif suggestions:
                score = self._goldsmith_consistency_score(suggestions)
                diagnostic_scope = "visible_pending_suggestions"
                scoring_mode = "character_rag_llm_review_proxy"
                candidate_count = len(suggestions)
                note = "Legacy proxy diagnostics from current pending suggestions; generate a new run to enable run-level self-consistency."
            else:
                continue
            line = {
                "schema_version": self.goldsmith_consistency_scores_schema_version,
                "record_type": "consistency_score",
                "generated_at": generated_at,
                "project_id": project_id,
                "document_id": document_id,
                "sentence_id": sentence["id"],
                "sentence_index": sentence["index"],
                "text": sentence["text"],
                "diagnostic_scope": diagnostic_scope,
                "scoring_mode": scoring_mode,
                "score": score["score"],
                "agreement": score["agreement"],
                "pairwise_span_f1": score["pairwise_span_f1"],
                "exact_match_rate": score["exact_match_rate"],
                "consensus_match_rate": score["consensus_match_rate"],
                "average_model_confidence": score["average_model_confidence"],
                "avg_confidence": score["avg_confidence"],
                "avg_rule_risk": score["avg_rule_risk"],
                "uncertainty_score": score["uncertainty_score"],
                "overlap_conflict_rate": score["overlap_conflict_rate"],
                "review_risk": score["review_risk"],
                "review_route": score["review_route"],
                "rosetta_route": score["rosetta_route"],
                "route_reason": score["route_reason"],
                "candidate_count": candidate_count,
                "run_count": len(run_candidates),
                "reviewed_candidate_count": score["reviewed_candidate_count"],
                "review_counts": score["review_counts"],
                "consensus_signature": score["consensus_signature"],
                "candidate_scores": score["candidate_scores"],
                "meta": {
                    "source": "annopilot",
                    "artifact": "consistency_scores.jsonl",
                    "rosetta_reference": "consistency_scores.jsonl",
                    "note": note,
                },
            }
            lines.append(json.dumps(line, ensure_ascii=False) + "\n")
        return lines

    def export_goldsmith_candidate_runs_lines(self, project_id: str, document_id: str) -> list[str]:
        document = self.get_document(project_id, document_id)
        run_candidates_by_sentence = self._candidate_run_snapshots_by_sentence(project_id, document_id)
        generated_at = self.now()
        lines = []
        for sentence in document["sentences"]:
            suggestions = sentence.get("suggestions", [])
            run_candidates = run_candidates_by_sentence.get(sentence["id"], [])
            if run_candidates:
                consistency_score = self._goldsmith_run_consistency_score(run_candidates)
                tokens = [self._export_prodigy_token(token, sentence["text"], sentence["start_char"]) for token in sentence["tokens"]]
                candidate_scores = {
                    candidate_score["candidate_id"]: candidate_score
                    for candidate_score in consistency_score["candidate_scores"]
                }
                for candidate in run_candidates:
                    spans = [
                        {
                            "id": f"T{index}",
                            "start": int(span["start_char"]) - int(sentence["start_char"]),
                            "end": int(span["end_char"]) - int(sentence["start_char"]),
                            "token_start": span["start_token_index"],
                            "token_end": span["end_token_index"],
                            "text": span["text"],
                            "label": span["tag_name"],
                            "label_id": span["tag_id"],
                            "implicit": False,
                        }
                        for index, span in enumerate(candidate["spans"], start=1)
                    ]
                    candidate_score = candidate_scores[candidate["candidate_id"]]
                    lines.append(
                        json.dumps(
                            {
                                "schema_version": self.goldsmith_candidate_runs_schema_version,
                                "record_type": "prodigy_candidate",
                                "generated_at": generated_at,
                                "sample_id": sentence["id"],
                                "candidate_id": candidate["candidate_id"],
                                "text": sentence["text"],
                                "tokens": tokens,
                                "spans": spans,
                                "relations": [],
                                "runtime_annotation": {
                                    "format": "inline_markup.v1",
                                    "annotation_markup": self._inline_spans_markup(sentence["text"], spans),
                                },
                                "answer": None,
                                "explanation": f"Immutable sentence candidate from annotation run {candidate['run_id']}.",
                                "model_confidence": candidate["model_confidence"],
                                "uncertainty_reason": self._run_candidate_uncertainty_reason(consistency_score),
                                "meta": {
                                    "source": "annopilot",
                                    "artifact": "candidate_runs.jsonl",
                                    "rosetta_reference": "candidate_runs.jsonl",
                                    "candidate_order": "sentence_index,run_created_at,run_id",
                                    "project_id": project_id,
                                    "document_id": document_id,
                                    "sentence_id": sentence["id"],
                                    "sentence_index": sentence["index"],
                                    "run_id": candidate["run_id"],
                                    "run_recipe": candidate["recipe"],
                                    "run_created_at": candidate["created_at"],
                                    "rosetta_route": consistency_score["rosetta_route"],
                                    "uncertainty_score": consistency_score["uncertainty_score"],
                                    "candidate_score": candidate_score,
                                    "consistency": self._goldsmith_consistency_export_summary(
                                        consistency_score,
                                        diagnostic_scope="run_candidate_snapshots",
                                        scoring_mode=(
                                            "k_run_self_consistency" if len(run_candidates) > 1 else "single_run_candidate_snapshot"
                                        ),
                                        candidate_count=len(run_candidates),
                                    ),
                                },
                            },
                            ensure_ascii=False,
                        )
                        + "\n"
                    )
                continue
            if not suggestions:
                continue
            sorted_suggestions = sorted(suggestions, key=lambda suggestion: str(suggestion["id"]))
            consistency_score = self._goldsmith_consistency_score(suggestions)
            rosetta_route = consistency_score["rosetta_route"]
            candidate_scores = {
                candidate_score["suggestion_id"]: candidate_score
                for candidate_score in consistency_score["candidate_scores"]
            }
            tokens = [self._export_prodigy_token(token, sentence["text"], sentence["start_char"]) for token in sentence["tokens"]]
            for index, suggestion in enumerate(sorted_suggestions, start=1):
                local_start = int(suggestion["start_char"]) - int(sentence["start_char"])
                local_end = int(suggestion["end_char"]) - int(sentence["start_char"])
                candidate_score = candidate_scores.get(suggestion["id"], {})
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
                        "candidate_order": "sentence_index,candidate_id",
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
                        "rosetta_route": rosetta_route,
                        "uncertainty_score": self._candidate_uncertainty_score(suggestion, candidate_score),
                        "candidate_score": candidate_score,
                        "consistency": {
                            "diagnostic_scope": "visible_pending_suggestions",
                            "scoring_mode": "character_rag_llm_review_proxy",
                            "score": consistency_score["score"],
                            "agreement": consistency_score["agreement"],
                            "pairwise_span_f1": consistency_score["pairwise_span_f1"],
                            "exact_match_rate": consistency_score["exact_match_rate"],
                            "consensus_match_rate": consistency_score["consensus_match_rate"],
                            "average_model_confidence": consistency_score["average_model_confidence"],
                            "avg_confidence": consistency_score["avg_confidence"],
                            "avg_rule_risk": consistency_score["avg_rule_risk"],
                            "uncertainty_score": consistency_score["uncertainty_score"],
                            "overlap_conflict_rate": consistency_score["overlap_conflict_rate"],
                            "review_risk": consistency_score["review_risk"],
                            "review_route": consistency_score["review_route"],
                            "rosetta_route": consistency_score["rosetta_route"],
                            "route_reason": consistency_score["route_reason"],
                            "candidate_count": len(suggestions),
                            "reviewed_candidate_count": consistency_score["reviewed_candidate_count"],
                            "review_counts": consistency_score["review_counts"],
                            "consensus_signature": consistency_score["consensus_signature"],
                        },
                    },
                }
                lines.append(json.dumps(line, ensure_ascii=False) + "\n")
        return lines

    def export_goldsmith_review_task_lines(self, project_id: str, document_id: str) -> list[str]:
        document = self.get_document(project_id, document_id)
        generated_at = self.now()
        tasks = []
        for sentence in document["sentences"]:
            suggestions = sentence.get("suggestions", [])
            if not suggestions:
                continue
            consistency_score = self._goldsmith_consistency_score(suggestions)
            route = consistency_score["rosetta_route"]
            if route == "high":
                continue
            sorted_suggestions = sorted(suggestions, key=lambda suggestion: str(suggestion["id"]))
            options = [
                self._goldsmith_review_option(index, sentence, suggestion, consistency_score)
                for index, suggestion in enumerate(sorted_suggestions)
            ]
            priority = self._goldsmith_review_task_priority(route, consistency_score["uncertainty_score"])
            review_guidance = self._goldsmith_review_guidance(sentence, options, consistency_score)
            tasks.append(
                {
                    "schema_version": self.goldsmith_review_tasks_schema_version,
                    "record_type": "human_review_task",
                    "generated_at": generated_at,
                    "project_id": project_id,
                    "document_id": document_id,
                    "sample_id": sentence["id"],
                    "sentence_id": sentence["id"],
                    "sentence_index": sentence["index"],
                    "route": route,
                    "priority": priority,
                    "prompt": self._goldsmith_review_prompt(sentence, consistency_score),
                    "text": sentence["text"],
                    "candidate_count": len(options),
                    "manual_option_id": "__manual__",
                    "review_guidance": review_guidance,
                    "options": options,
                    "consistency": {
                        "diagnostic_scope": "visible_pending_suggestions",
                        "scoring_mode": "character_rag_llm_review_proxy",
                        "score": consistency_score["score"],
                        "pairwise_span_f1": consistency_score["pairwise_span_f1"],
                        "exact_match_rate": consistency_score["exact_match_rate"],
                        "consensus_match_rate": consistency_score["consensus_match_rate"],
                        "average_model_confidence": consistency_score["average_model_confidence"],
                        "uncertainty_score": consistency_score["uncertainty_score"],
                        "overlap_conflict_rate": consistency_score["overlap_conflict_rate"],
                        "review_risk": consistency_score["review_risk"],
                        "rosetta_route": route,
                        "review_route": consistency_score["review_route"],
                        "candidate_scores": consistency_score["candidate_scores"],
                    },
                    "meta": {
                        "source": "annopilot",
                        "artifact": "review_tasks.jsonl",
                        "rosetta_reference": "human_review_queue.jsonl",
                        "option_order": "candidate_id",
                        "manual_option_note": "Choose __manual__ when every candidate span or label is wrong and the sentence needs direct editing.",
                    },
                }
            )
        tasks.sort(key=lambda task: (-int(task["priority"]), int(task["sentence_index"]), str(task["sentence_id"])))
        return [json.dumps({**task, "rank": rank}, ensure_ascii=False) + "\n" for rank, task in enumerate(tasks, start=1)]

    def export_goldsmith_prompt_package_lines(self, project_id: str, document_id: str) -> list[str]:
        generated_at = self.now()
        tag_schema = self.export_tag_schema(project_id)
        review_tasks = self._jsonl_payloads(self.export_goldsmith_review_task_lines(project_id, document_id))
        hard_examples = self._jsonl_payloads(self.export_goldsmith_hard_examples_lines(project_id, document_id))
        contrastive_examples = self._jsonl_payloads(self.export_goldsmith_contrastive_examples_lines(project_id, document_id))
        reflection_plans = self._jsonl_payloads(self.export_goldsmith_reflection_plan_lines(project_id, document_id))
        hard_by_sentence: dict[str, list[dict[str, Any]]] = {}
        for example in hard_examples:
            sentence_id = str(example.get("sentence_id") or "")
            if sentence_id:
                hard_by_sentence.setdefault(sentence_id, []).append(example)
        contrastive_by_query = {str(item.get("query_id")): item for item in contrastive_examples if item.get("query_id")}
        reflection_by_sentence = {str(item.get("sentence_id")): item for item in reflection_plans if item.get("sentence_id")}

        lines = []
        for task in review_tasks:
            sentence_id = str(task["sentence_id"])
            context_examples = self._goldsmith_prompt_context_examples(
                tag_schema,
                hard_by_sentence.get(sentence_id, []),
                contrastive_by_query.get(sentence_id),
            )
            reflection_plan = reflection_by_sentence.get(sentence_id)
            prompt = self._goldsmith_prompt_package_prompt(
                tag_schema=tag_schema,
                task=task,
                context_examples=context_examples,
                reflection_plan=reflection_plan,
            )
            payload = {
                "schema_version": self.goldsmith_prompt_package_schema_version,
                "record_type": "prompt_task",
                "generated_at": generated_at,
                "project_id": project_id,
                "document_id": document_id,
                "sample_id": task["sample_id"],
                "sentence_id": sentence_id,
                "sentence_index": task["sentence_index"],
                "rank": task.get("rank"),
                "route": task["route"],
                "priority": task["priority"],
                "text": task["text"],
                "prompt": prompt,
                "output_contract": self._goldsmith_prompt_output_contract(),
                "tag_schema": self._goldsmith_prompt_tag_schema(tag_schema),
                "context_examples": context_examples,
                "reflection_items": (reflection_plan or {}).get("items", []),
                "review_task": task,
                "verification": {
                    "annotation_format": "[原文]{标签} / [!隐含义]{标签}",
                    "must_preserve_text": True,
                    "rosetta_reference": "verifier.py",
                    "checks": ["valid_json", "text_match", "valid_annotation_markup", "explicit_span_present_in_text"],
                },
                "meta": {
                    "source": "annopilot",
                    "artifact": "prompt_package.jsonl",
                    "rosetta_reference": "prompting.py",
                    "prompt_builder": "rosetta_prompting_compatible_v1",
                    "review_task_schema_version": self.goldsmith_review_tasks_schema_version,
                    "tag_schema_sha256": tag_schema["content_sha256"],
                    "context_example_count": len(context_examples),
                    "reflection_item_count": len((reflection_plan or {}).get("items", [])),
                },
            }
            lines.append(json.dumps(payload, ensure_ascii=False) + "\n")
        return lines

    def export_goldsmith_verification_report_lines(self, project_id: str, document_id: str) -> list[str]:
        document = self.get_document(project_id, document_id)
        return self._build_goldsmith_verification_report_lines(
            project_id=project_id,
            document_id=document_id,
            document=document,
            generated_at=self.now(),
            tag_schema=self.export_tag_schema(project_id),
            prodigy_lines=self.export_prodigy_document_lines(project_id, document_id),
            prodigy_spans_lines=self.export_prodigy_spans_document_lines(project_id, document_id),
            goldsmith_candidate_run_lines=self.export_goldsmith_candidate_runs_lines(project_id, document_id),
            goldsmith_review_task_lines=self.export_goldsmith_review_task_lines(project_id, document_id),
            goldsmith_prompt_package_lines=self.export_goldsmith_prompt_package_lines(project_id, document_id),
        )

    def export_goldsmith_bootstrap_report_lines(self, project_id: str, document_id: str) -> list[str]:
        document = self.get_document(project_id, document_id)
        goldsmith_verification_report_lines = self.export_goldsmith_verification_report_lines(project_id, document_id)
        return self._build_goldsmith_bootstrap_report_lines(
            project_id=project_id,
            document_id=document_id,
            document=document,
            generated_at=self.now(),
            prodigy_readiness=self._prodigy_readiness(
                document["metrics"],
                verification_summary=self._verification_summary_from_lines(goldsmith_verification_report_lines),
            ),
            goldsmith_review_queue_lines=self.export_goldsmith_review_queue_lines(project_id, document_id, order="hybrid", limit=100),
            goldsmith_consistency_score_lines=self.export_goldsmith_consistency_scores_lines(project_id, document_id),
            goldsmith_label_statistics_lines=self.export_goldsmith_label_statistics_lines(project_id, document_id),
            goldsmith_reflection_plan_lines=self.export_goldsmith_reflection_plan_lines(project_id, document_id),
            goldsmith_review_task_lines=self.export_goldsmith_review_task_lines(project_id, document_id),
            goldsmith_verification_report_lines=goldsmith_verification_report_lines,
        )

    def _build_goldsmith_verification_report_lines(
        self,
        *,
        project_id: str,
        document_id: str,
        document: dict[str, Any],
        generated_at: str,
        tag_schema: dict[str, Any],
        prodigy_lines: list[str],
        prodigy_spans_lines: list[str],
        goldsmith_candidate_run_lines: list[str],
        goldsmith_review_task_lines: list[str],
        goldsmith_prompt_package_lines: list[str],
    ) -> list[str]:
        return self.export_verification_service.build_lines(
            project_id=project_id,
            document_id=document_id,
            document=document,
            generated_at=generated_at,
            tag_schema=tag_schema,
            prodigy_lines=prodigy_lines,
            prodigy_spans_lines=prodigy_spans_lines,
            goldsmith_candidate_run_lines=goldsmith_candidate_run_lines,
            goldsmith_review_task_lines=goldsmith_review_task_lines,
            goldsmith_prompt_package_lines=goldsmith_prompt_package_lines,
        )

    def _build_goldsmith_bootstrap_report_lines(
        self,
        *,
        project_id: str,
        document_id: str,
        document: dict[str, Any],
        generated_at: str,
        prodigy_readiness: dict[str, Any],
        goldsmith_review_queue_lines: list[str],
        goldsmith_consistency_score_lines: list[str],
        goldsmith_label_statistics_lines: list[str],
        goldsmith_reflection_plan_lines: list[str],
        goldsmith_review_task_lines: list[str],
        goldsmith_verification_report_lines: list[str],
    ) -> list[str]:
        return self.bootstrap_report_service.build_lines(
            project_id=project_id,
            document_id=document_id,
            generated_at=generated_at,
            document=document,
            prodigy_readiness=prodigy_readiness,
            goldsmith_review_queue_lines=goldsmith_review_queue_lines,
            goldsmith_consistency_score_lines=goldsmith_consistency_score_lines,
            goldsmith_label_statistics_lines=goldsmith_label_statistics_lines,
            goldsmith_reflection_plan_lines=goldsmith_reflection_plan_lines,
            goldsmith_review_task_lines=goldsmith_review_task_lines,
            goldsmith_verification_report_lines=goldsmith_verification_report_lines,
        )

    @staticmethod
    def _goldsmith_prompt_output_contract() -> list[str]:
        return ["text", "annotation", "explanation", "selected_option_id", "answer"]

    @staticmethod
    def _goldsmith_prompt_tag_schema(tag_schema: dict[str, Any]) -> dict[str, Any]:
        tags = [
            {
                "id": tag["id"],
                "name": tag["name"],
                "description": tag.get("description"),
                "examples": tag.get("examples", []),
                "taxonomy": tag.get("taxonomy"),
            }
            for tag in tag_schema.get("tags", [])
        ]
        return {
            "schema_version": tag_schema["schema_version"],
            "record_type": "tag_schema_context",
            "content_sha256": tag_schema["content_sha256"],
            "tag_count": len(tags),
            "tags": tags,
        }

    def _goldsmith_prompt_context_examples(
        self,
        tag_schema: dict[str, Any],
        hard_examples: list[dict[str, Any]],
        contrastive_selection: dict[str, Any] | None,
    ) -> list[dict[str, Any]]:
        examples: list[dict[str, Any]] = []
        for tag in tag_schema.get("tags", []):
            for index, text in enumerate((tag.get("examples") or [])[:1], start=1):
                examples.append(
                    {
                        "id": f"tag:{tag['id']}:{index}",
                        "example_type": "canonical",
                        "source_artifact": "tag_schema",
                        "text": text,
                        "annotation": self._inline_span_markup(text, 0, len(text), tag["name"]),
                        "explanation": tag.get("description") or f"Lexical seed for {tag['name']}.",
                        "rationale": "Bilingual lexical seed from the current span label schema.",
                    }
                )
                if len(examples) >= 9:
                    break
            if len(examples) >= 9:
                break

        for example in hard_examples[:3]:
            span = example.get("span") or {}
            label = span.get("label") or example.get("tag_name") or "Engagement"
            span_text = span.get("text") or example.get("text") or ""
            examples.append(
                {
                    "id": f"hard:{example.get('suggestion_id')}",
                    "example_type": "hard",
                    "source_artifact": "hard_examples.jsonl",
                    "text": example.get("text") or example.get("sentence_text") or span_text,
                    "annotation": self._inline_span_markup(span_text, 0, len(span_text), label) if span_text else "",
                    "explanation": example.get("failure_note") or "Human or LLM feedback marked this as a boundary case.",
                    "rationale": ", ".join(example.get("hard_example_reasons") or example.get("risk_reason_codes") or []),
                }
            )

        if contrastive_selection:
            for role in ("similar", "boundary"):
                for hit in (contrastive_selection.get(role) or [])[:1]:
                    sample = hit.get("sample") or {}
                    spans = sample.get("spans") or []
                    examples.append(
                        {
                            "id": f"{role}:{hit.get('sample_id')}",
                            "example_type": "canonical" if role == "similar" else "hard",
                            "source_artifact": "contrastive_examples.jsonl",
                            "text": sample.get("text", ""),
                            "annotation": self._sample_annotation_markup(sample),
                            "explanation": f"{role} example selected by lexical overlap score={hit.get('score')}.",
                            "rationale": f"Rosetta contrastive role: {role}; span_count={len(spans)}.",
                        }
                    )
        return examples[:12]

    @classmethod
    def _goldsmith_prompt_package_prompt(
        cls,
        *,
        tag_schema: dict[str, Any],
        task: dict[str, Any],
        context_examples: list[dict[str, Any]],
        reflection_plan: dict[str, Any] | None,
    ) -> str:
        sections = [
            "你正在执行科研标注任务。请先严格根据定义判断，再返回结构化 JSON。",
            "任务名称：Appraisal Engagement span annotation",
            "任务说明：复核当前句的 Engagement span 候选；选择最准确的候选，或在所有候选都错误时给出手动标注。",
            f"操作化定义：{cls._goldsmith_prompt_definition(tag_schema)}",
            cls._format_prompt_rules("纳入标准", cls._goldsmith_prompt_inclusion_rules()),
            cls._format_prompt_rules("排除标准", cls._goldsmith_prompt_exclusion_rules()),
            cls._format_prompt_rules("负向约束", cls._goldsmith_prompt_negative_constraints()),
            cls._format_prompt_examples(context_examples),
            cls._format_prompt_options(task),
            cls._format_prompt_reflection(reflection_plan),
            (
                "输出要求：仅返回 JSON，对象必须包含字段 "
                + ", ".join(cls._goldsmith_prompt_output_contract())
                + "。annotation 必须使用 [原文]{标签} / [!隐含义]{标签} 格式；text 必须与输入句子完全一致；"
                + "selected_option_id 使用 A/B/C 或 __manual__；answer 使用 accept/reject/uncertain。"
            ),
            f"待标注样本（id={task['sample_id']}）：",
            str(task["text"]),
        ]
        return "\n\n".join(section for section in sections if section.strip())

    @staticmethod
    def _goldsmith_prompt_definition(tag_schema: dict[str, Any]) -> str:
        tag_lines = []
        for tag in tag_schema.get("tags", []):
            description = tag.get("description") or "No definition provided."
            taxonomy_path = " > ".join((tag.get("taxonomy") or {}).get("path") or [])
            hierarchy = f" [{taxonomy_path}]" if taxonomy_path else ""
            tag_lines.append(f"- {tag['name']}{hierarchy}: {description}")
        return "Appraisal Theory 的 Engagement 系统用于标注作者如何打开或收缩对话空间。可用 span labels：\n" + "\n".join(tag_lines)

    @staticmethod
    def _goldsmith_prompt_inclusion_rules() -> tuple[str, ...]:
        return (
            "标注显性表达 dialogic positioning 的最小充分文本 span。",
            "英文和中文 cue 都要保留原文边界，不翻译、不改写。",
            "候选之间冲突时，优先选择 label 功能与上下文最一致且边界最窄的候选。",
            "Monogloss 只在没有显性 modality、attribution、denial、countering 或 proclaim cue 的直接断言中使用。",
        )

    @staticmethod
    def _goldsmith_prompt_exclusion_rules() -> tuple[str, ...]:
        return (
            "不要仅因为文本有情绪、价值判断或主题重要就标注。",
            "不要把无关上下文、完整从句或整句并入 span，除非 label 定义明确需要。",
            "不要发明原文中不存在的显性 span；隐含标注只能用于确实需要的抽象义。",
        )

    @staticmethod
    def _goldsmith_prompt_negative_constraints() -> tuple[str, ...]:
        return (
            "如果所有候选都错误，selected_option_id 必须是 __manual__。",
            "如果边界或 label 仍不确定，answer 使用 uncertain，并在 explanation 中说明原因。",
            "返回 JSON 之外不要输出任何额外解释。",
        )

    @staticmethod
    def _format_prompt_rules(title: str, rules: tuple[str, ...]) -> str:
        if not rules:
            return f"{title}：无"
        lines = [f"{title}："]
        lines.extend(f"{index}. {rule}" for index, rule in enumerate(rules, start=1))
        return "\n".join(lines)

    @staticmethod
    def _format_prompt_examples(examples: list[dict[str, Any]]) -> str:
        if not examples:
            return "上下文示例：无"
        lines = ["上下文示例："]
        for index, example in enumerate(examples, start=1):
            label = "典型示例" if example.get("example_type") == "canonical" else "易错示例"
            lines.append(f"示例 {index}（{label}, id={example.get('id')}）:")
            lines.append(
                json.dumps(
                    {
                        "text": example.get("text", ""),
                        "annotation": example.get("annotation", ""),
                        "explanation": example.get("explanation", ""),
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            if example.get("rationale"):
                lines.append(f"审查说明: {example['rationale']}")
        return "\n".join(lines)

    @staticmethod
    def _format_prompt_options(task: dict[str, Any]) -> str:
        lines = ["候选选项："]
        for option in task.get("options") or []:
            span = option.get("span") or {}
            lines.append(
                f"{option.get('option_id')}. {span.get('label')} -> `{span.get('text')}` "
                f"(tokens {span.get('token_start')}-{span.get('token_end')})"
            )
            lines.append(f"   markup: {option.get('annotation_markup')}")
            if option.get("action_hint"):
                lines.append(f"   hint: {option['action_hint']}")
        lines.append("__manual__. 所有候选都不对，需要人工直接修正。")
        return "\n".join(lines)

    @staticmethod
    def _format_prompt_reflection(reflection_plan: dict[str, Any] | None) -> str:
        items = (reflection_plan or {}).get("items") or []
        if not items:
            return "反思检查：暂无自动反思项"
        lines = ["反思检查："]
        for item in items[:5]:
            lines.append(f"- {item.get('item_type')}: `{item.get('token')}` chars {item.get('start')}-{item.get('end')}；{item.get('reason')}")
        return "\n".join(lines)

    @classmethod
    def _sample_annotation_markup(cls, sample: dict[str, Any]) -> str:
        text = str(sample.get("text") or "")
        spans = sorted(sample.get("spans") or [], key=lambda span: (int(span.get("start") or 0), int(span.get("end") or 0)))
        if not text or not spans:
            return text
        offset = 0
        annotated = text
        for span in spans:
            start = int(span.get("start") or 0) + offset
            end = int(span.get("end") or 0) + offset
            label = str(span.get("label") or "Engagement")
            replacement = cls._inline_span_markup(annotated, start, end, label)
            offset += len(replacement) - len(annotated)
            annotated = replacement
        return annotated

    def _goldsmith_review_option(
        self,
        index: int,
        sentence: dict[str, Any],
        suggestion: dict[str, Any],
        consistency_score: dict[str, Any],
    ) -> dict[str, Any]:
        candidate_scores = {
            candidate_score["suggestion_id"]: candidate_score
            for candidate_score in consistency_score["candidate_scores"]
        }
        local_start = int(suggestion["start_char"]) - int(sentence["start_char"])
        local_end = int(suggestion["end_char"]) - int(sentence["start_char"])
        candidate_score = candidate_scores.get(suggestion["id"], {})
        return {
            "option_id": chr(ord("A") + index),
            "candidate_id": suggestion["id"],
            "annotation_markup": self._inline_span_markup(sentence["text"], local_start, local_end, suggestion["tag_name"]),
            "explanation": self._candidate_explanation(suggestion),
            "action_hint": self._goldsmith_review_option_action_hint(suggestion, candidate_score),
            "model_confidence": suggestion["confidence"],
            "risk_reason_codes": suggestion.get("risk_reason_codes") or self._suggestion_risk_reason_codes(suggestion),
            "span": {
                "label": suggestion["tag_name"],
                "label_id": suggestion["tag_id"],
                "text": suggestion["text"],
                "start": local_start,
                "end": local_end,
                "token_start": suggestion["start_token_index"],
                "token_end": suggestion["end_token_index"],
            },
            "candidate_score": candidate_score,
            "latest_review": suggestion.get("latest_review"),
        }

    @staticmethod
    def _goldsmith_review_task_priority(route: str, uncertainty_score: float) -> int:
        base = {"low": 100, "medium": 50, "high": 10}.get(route, 0)
        return base + int(round(float(uncertainty_score or 0.0) * 10))

    def _goldsmith_review_guidance(
        self,
        sentence: dict[str, Any],
        options: list[dict[str, Any]],
        consistency_score: dict[str, Any],
    ) -> dict[str, Any]:
        labels = sorted({str(option["span"]["label"]) for option in options})
        token_ranges = sorted({(int(option["span"]["token_start"]), int(option["span"]["token_end"])) for option in options})
        risk_reason_codes = {code for option in options for code in option.get("risk_reason_codes", [])}
        if len(labels) > 1 or len(token_ranges) > 1 or float(consistency_score["overlap_conflict_rate"]) > 0:
            risk_reason_codes.add("candidate_conflict")
        route = str(consistency_score["rosetta_route"])
        primary_action = "expert_boundary_review" if route == "low" else "compare_candidates"
        if not options:
            primary_action = "manual_review"
        return {
            "domain": "appraisal_engagement",
            "task_goal": "Pick the candidate that best captures the Engagement cue, or choose __manual__ if none is correct.",
            "primary_action": primary_action,
            "route_reason": consistency_score["route_reason"],
            "risk_reason_codes": sorted(risk_reason_codes),
            "span_conflict_summary": {
                "candidate_count": len(options),
                "label_count": len(labels),
                "labels": labels,
                "unique_token_ranges": [
                    {"token_start": start, "token_end": end}
                    for start, end in token_ranges
                ],
                "has_label_conflict": len(labels) > 1,
                "has_boundary_conflict": len(token_ranges) > 1,
                "overlap_conflict_rate": consistency_score["overlap_conflict_rate"],
            },
            "boundary_checks": [
                "Choose the smallest text span that explicitly signals dialogic positioning.",
                "Judge the label by Engagement function, not by sentiment polarity alone.",
                "Use __manual__ when candidates miss a cue, include extra context, or assign the wrong Engagement label.",
            ],
            "sentence_locator": {
                "sentence_id": sentence["id"],
                "sentence_index": sentence["index"],
            },
        }

    def _goldsmith_review_option_action_hint(self, suggestion: dict[str, Any], candidate_score: dict[str, Any]) -> str:
        latest_review = suggestion.get("latest_review") or {}
        recommendation = latest_review.get("recommendation")
        if recommendation == "reject":
            return "Likely false positive: compare against the label definition before accepting."
        if recommendation == "uncertain":
            return "Boundary case: inspect whether the cue is explicit enough for this Engagement label."
        if int(candidate_score.get("overlap_conflict_count") or 0) > 0:
            return "Conflicting candidate: compare label and token boundary with the other options."
        if float(candidate_score.get("span_f1_to_consensus") or 1.0) < 1.0:
            return "Boundary differs from the consensus span; verify start/end tokens carefully."
        if float(suggestion.get("confidence") or 0.0) < self.medium_confidence_threshold:
            return "Low confidence: accept only if the span is an explicit Engagement cue."
        return "Accept if the highlighted span and label are both exact; otherwise choose __manual__."

    @staticmethod
    def _goldsmith_review_prompt(sentence: dict[str, Any], consistency_score: dict[str, Any]) -> str:
        return (
            "请选择最接近正确 Engagement 标注的候选；如果都不对，请选择手动修正。 "
            f"句子 #{int(sentence['index']) + 1}，route={consistency_score['rosetta_route']}，"
            f"span-F1={consistency_score['pairwise_span_f1']}，exact={consistency_score['exact_match_rate']}。"
        )

    def export_goldsmith_risk_reason_lines(self, project_id: str, document_id: str) -> list[str]:
        document = self.get_document(project_id, document_id)
        return self._build_goldsmith_risk_reason_lines(
            project_id=project_id,
            document_id=document_id,
            document=document,
            generated_at=self.now(),
            review_queue_lines=self.export_goldsmith_review_queue_lines(project_id, document_id, order="hybrid", limit=100),
            hard_example_lines=self.export_goldsmith_hard_examples_lines(project_id, document_id),
            boundary_feedback_lines=self.export_goldsmith_boundary_feedback_lines(project_id, document_id),
        )

    def _build_goldsmith_risk_reason_lines(
        self,
        *,
        project_id: str,
        document_id: str,
        document: dict[str, Any],
        generated_at: str,
        review_queue_lines: list[str],
        hard_example_lines: list[str],
        boundary_feedback_lines: list[str],
    ) -> list[str]:
        metrics = document.get("metrics") or {}
        goldsmith_curve = (metrics.get("review_efficiency_curves") or {}).get("goldsmith") or {}
        summaries: dict[str, dict[str, Any]] = {}

        def summary_for(reason_code: str) -> dict[str, Any]:
            return summaries.setdefault(
                reason_code,
                {
                    "reason_code": reason_code,
                    "calibrated_count": 0,
                    "disagreement_count": 0,
                    "queue_count": 0,
                    "hard_example_count": 0,
                    "boundary_feedback_count": 0,
                    "first_examples": [],
                },
            )

        for reason_code, count in (goldsmith_curve.get("reason_counts") or {}).items():
            summary_for(reason_code)["calibrated_count"] = int(count)
        for reason_code, count in (goldsmith_curve.get("disagreement_reason_counts") or {}).items():
            summary_for(reason_code)["disagreement_count"] = int(count)

        artifact_sources = [
            (
                "review_queue",
                "queue_count",
                review_queue_lines,
            ),
            (
                "hard_examples",
                "hard_example_count",
                hard_example_lines,
            ),
            (
                "boundary_feedback",
                "boundary_feedback_count",
                boundary_feedback_lines,
            ),
        ]
        for source_artifact, count_key, lines in artifact_sources:
            for payload in self._jsonl_payloads(lines):
                for reason_code in payload.get("risk_reason_codes") or []:
                    summary = summary_for(reason_code)
                    summary[count_key] += 1
                    if len(summary["first_examples"]) < 3:
                        summary["first_examples"].append(self._risk_reason_example(source_artifact, payload))

        lines = []
        sorted_summaries = sorted(
            summaries.values(),
            key=lambda item: (
                -int(item["disagreement_count"]),
                -self._risk_reason_total_count(item),
                str(item["reason_code"]),
            ),
        )
        for rank, summary in enumerate(sorted_summaries, start=1):
            total_count = self._risk_reason_total_count(summary)
            line = {
                "schema_version": self.goldsmith_risk_reasons_schema_version,
                "record_type": "risk_reason_summary",
                "generated_at": generated_at,
                "project_id": project_id,
                "document_id": document_id,
                "rank": rank,
                "reason_code": summary["reason_code"],
                "total_count": total_count,
                "calibrated_count": summary["calibrated_count"],
                "disagreement_count": summary["disagreement_count"],
                "queue_count": summary["queue_count"],
                "hard_example_count": summary["hard_example_count"],
                "boundary_feedback_count": summary["boundary_feedback_count"],
                "first_examples": summary["first_examples"],
                "meta": {
                    "source": "annopilot",
                    "artifact": "risk_reasons.jsonl",
                    "curve_order": "goldsmith",
                    "queue_order": "hybrid",
                    "reviewed_count": goldsmith_curve.get("reviewed_count", 0),
                    "early_reviewed_count": goldsmith_curve.get("early_reviewed_count", 0),
                },
            }
            lines.append(json.dumps(line, ensure_ascii=False) + "\n")
        return lines

    @staticmethod
    def _risk_reason_total_count(summary: dict[str, Any]) -> int:
        return (
            int(summary.get("calibrated_count") or 0)
            + int(summary.get("queue_count") or 0)
            + int(summary.get("hard_example_count") or 0)
            + int(summary.get("boundary_feedback_count") or 0)
        )

    @staticmethod
    def _jsonl_payloads(lines: list[str]) -> list[dict[str, Any]]:
        payloads = []
        for line in lines:
            if not line.strip():
                continue
            payload = json.loads(line)
            if isinstance(payload, dict):
                payloads.append(payload)
        return payloads

    @staticmethod
    def _risk_reason_example(source_artifact: str, payload: dict[str, Any]) -> dict[str, Any]:
        suggestion = payload.get("first_suggestion") or payload.get("suggestion") or {}
        return {
            "source_artifact": source_artifact,
            "sentence_id": payload.get("sentence_id"),
            "sentence_index": payload.get("sentence_index"),
            "suggestion_id": payload.get("suggestion_id") or suggestion.get("id"),
            "text": payload.get("text"),
            "span_text": (payload.get("span") or {}).get("text") or suggestion.get("text"),
            "label": (payload.get("span") or {}).get("label") or suggestion.get("tag_name"),
        }

    def _candidate_run_snapshots_by_sentence(
        self,
        project_id: str,
        document_id: str,
    ) -> dict[str, list[dict[str, Any]]]:
        grouped: dict[str, list[dict[str, Any]]] = {}
        for candidate in self.list_candidate_run_snapshots(project_id, document_id, limit=5):
            grouped.setdefault(str(candidate["sentence_id"]), []).append(candidate)
        return grouped

    def _goldsmith_run_consistency_score(self, candidates: list[dict[str, Any]]) -> dict[str, Any]:
        signatures = [self._run_candidate_signature(candidate) for candidate in candidates]
        signature_counts: dict[str, int] = {}
        for signature in signatures:
            signature_counts[signature] = signature_counts.get(signature, 0) + 1
        consensus_signature = max(signature_counts, key=lambda key: (signature_counts[key], key))
        consensus_match_rate = signature_counts[consensus_signature] / len(candidates)
        exact_match_rate = signature_counts[signatures[0]] / len(candidates)
        pairwise_span_f1 = self._run_candidate_pairwise_span_f1(candidates)
        confidence_values = [
            float(candidate["model_confidence"])
            for candidate in candidates
            if candidate.get("model_confidence") is not None
        ]
        avg_confidence = sum(confidence_values) / len(confidence_values) if confidence_values else None
        overlap_conflict_rate = self._run_candidate_overlap_conflict_rate(candidates)
        uncertainty_score = self._rosetta_uncertainty_score(pairwise_span_f1, avg_confidence)
        rosetta_route = self._rosetta_consistency_route(pairwise_span_f1, exact_match_rate, avg_confidence)
        review_route = {
            "high": "high_confidence_sample",
            "medium": "light_review",
            "low": "expert_review",
        }[rosetta_route]
        score = max(0.0, min(1.0, 1.0 - uncertainty_score))
        candidate_scores = []
        for candidate in candidates:
            candidate_scores.append(
                {
                    "candidate_id": candidate["candidate_id"],
                    "suggestion_id": candidate["candidate_id"],
                    "run_id": candidate["run_id"],
                    "span_signature": self._run_candidate_signature(candidate),
                    "span_count": len(candidate["spans"]),
                    "span_f1_to_consensus": self._run_candidate_span_f1_to_signature(candidate, consensus_signature),
                    "pairwise_span_f1": self._single_run_candidate_pairwise_span_f1(candidate, candidates),
                    "model_confidence": candidate.get("model_confidence"),
                    "review_recommendation": None,
                    "review_risk": 0.0,
                    "overlap_conflict_count": self._run_candidate_conflict_count(candidate, candidates),
                    "rule_risk": 0.0,
                }
            )
        return {
            "score": round(score, 4),
            "agreement": pairwise_span_f1,
            "pairwise_span_f1": pairwise_span_f1,
            "exact_match_rate": round(exact_match_rate, 4),
            "consensus_match_rate": round(consensus_match_rate, 4),
            "average_model_confidence": round(avg_confidence, 4) if avg_confidence is not None else None,
            "avg_confidence": round(avg_confidence, 4) if avg_confidence is not None else None,
            "avg_rule_risk": round(overlap_conflict_rate, 4),
            "uncertainty_score": uncertainty_score,
            "overlap_conflict_rate": round(overlap_conflict_rate, 4),
            "review_risk": 0.0,
            "review_route": review_route,
            "rosetta_route": rosetta_route,
            "route_reason": self._consistency_route_reason(review_route),
            "reviewed_candidate_count": 0,
            "review_counts": {"accept": 0, "reject": 0, "uncertain": 0},
            "consensus_signature": consensus_signature,
            "candidate_scores": candidate_scores,
        }

    @classmethod
    def _run_candidate_pairwise_span_f1(cls, candidates: list[dict[str, Any]]) -> float:
        if len(candidates) <= 1:
            return 1.0 if candidates else 0.0
        scores = []
        for left_index, left in enumerate(candidates):
            for right in candidates[left_index + 1 :]:
                scores.append(cls._run_candidate_span_f1(left, right))
        return round(sum(scores) / len(scores), 4) if scores else 0.0

    @classmethod
    def _single_run_candidate_pairwise_span_f1(
        cls,
        candidate: dict[str, Any],
        candidates: list[dict[str, Any]],
    ) -> float:
        peers = [peer for peer in candidates if peer["candidate_id"] != candidate["candidate_id"]]
        if not peers:
            return 1.0
        return round(sum(cls._run_candidate_span_f1(candidate, peer) for peer in peers) / len(peers), 4)

    @classmethod
    def _run_candidate_span_f1(cls, left: dict[str, Any], right: dict[str, Any]) -> float:
        left_keys = cls._run_candidate_span_keys(left)
        right_keys = cls._run_candidate_span_keys(right)
        if not left_keys and not right_keys:
            return 1.0
        if not left_keys or not right_keys:
            return 0.0
        overlap = len(left_keys & right_keys)
        precision = overlap / len(left_keys)
        recall = overlap / len(right_keys)
        return round((2 * precision * recall) / (precision + recall), 4) if precision + recall else 0.0

    @classmethod
    def _run_candidate_signature(cls, candidate: dict[str, Any]) -> str:
        return json.dumps(sorted(cls._run_candidate_span_keys(candidate)), ensure_ascii=False, separators=(",", ":"))

    @staticmethod
    def _run_candidate_span_keys(candidate: dict[str, Any]) -> set[tuple[str, int, int, str]]:
        return {
            (
                str(span["tag_id"]),
                int(span["start_char"]),
                int(span["end_char"]),
                str(span["text"]),
            )
            for span in candidate.get("spans", [])
        }

    @classmethod
    def _run_candidate_span_f1_to_signature(cls, candidate: dict[str, Any], signature: str) -> float:
        raw_keys = json.loads(signature)
        consensus = {tuple(item) for item in raw_keys}
        current = cls._run_candidate_span_keys(candidate)
        if not current and not consensus:
            return 1.0
        if not current or not consensus:
            return 0.0
        overlap = len(current & consensus)
        precision = overlap / len(current)
        recall = overlap / len(consensus)
        return round((2 * precision * recall) / (precision + recall), 4) if precision + recall else 0.0

    @classmethod
    def _run_candidate_overlap_conflict_rate(cls, candidates: list[dict[str, Any]]) -> float:
        if len(candidates) <= 1:
            return 0.0
        pair_count = 0
        conflict_count = 0
        for left_index, left in enumerate(candidates):
            for right in candidates[left_index + 1 :]:
                pair_count += 1
                if cls._run_candidates_conflict(left, right):
                    conflict_count += 1
        return conflict_count / pair_count if pair_count else 0.0

    @classmethod
    def _run_candidate_conflict_count(cls, candidate: dict[str, Any], candidates: list[dict[str, Any]]) -> int:
        return sum(
            1
            for peer in candidates
            if peer["candidate_id"] != candidate["candidate_id"] and cls._run_candidates_conflict(candidate, peer)
        )

    @classmethod
    def _run_candidates_conflict(cls, left: dict[str, Any], right: dict[str, Any]) -> bool:
        for left_span in left.get("spans", []):
            for right_span in right.get("spans", []):
                if (
                    int(left_span["start_token_index"]) <= int(right_span["end_token_index"])
                    and int(left_span["end_token_index"]) >= int(right_span["start_token_index"])
                    and (
                        left_span["tag_id"],
                        left_span["start_token_index"],
                        left_span["end_token_index"],
                    )
                    != (
                        right_span["tag_id"],
                        right_span["start_token_index"],
                        right_span["end_token_index"],
                    )
                ):
                    return True
        return False

    @staticmethod
    def _goldsmith_consistency_export_summary(
        score: dict[str, Any],
        *,
        diagnostic_scope: str,
        scoring_mode: str,
        candidate_count: int,
    ) -> dict[str, Any]:
        return {
            "diagnostic_scope": diagnostic_scope,
            "scoring_mode": scoring_mode,
            "score": score["score"],
            "agreement": score["agreement"],
            "pairwise_span_f1": score["pairwise_span_f1"],
            "exact_match_rate": score["exact_match_rate"],
            "consensus_match_rate": score["consensus_match_rate"],
            "average_model_confidence": score["average_model_confidence"],
            "avg_confidence": score["avg_confidence"],
            "avg_rule_risk": score["avg_rule_risk"],
            "uncertainty_score": score["uncertainty_score"],
            "overlap_conflict_rate": score["overlap_conflict_rate"],
            "review_risk": score["review_risk"],
            "review_route": score["review_route"],
            "rosetta_route": score["rosetta_route"],
            "route_reason": score["route_reason"],
            "candidate_count": candidate_count,
            "reviewed_candidate_count": score["reviewed_candidate_count"],
            "review_counts": score["review_counts"],
            "consensus_signature": score["consensus_signature"],
        }

    @staticmethod
    def _run_candidate_uncertainty_reason(score: dict[str, Any]) -> str:
        if score["rosetta_route"] == "high":
            return "Run-level candidate spans are stable; retain a small calibration sample."
        if score["rosetta_route"] == "medium":
            return "Run-level candidate spans differ moderately; perform light human review."
        return "Run-level candidate spans disagree; prioritize expert review."

    def _goldsmith_consistency_score(self, suggestions: list[dict[str, Any]]) -> dict[str, Any]:
        signatures = [self._suggestion_signature(suggestion) for suggestion in suggestions]
        signature_counts: dict[str, int] = {}
        for signature in signatures:
            signature_counts[signature] = signature_counts.get(signature, 0) + 1
        consensus_signature = max(signature_counts, key=lambda key: (signature_counts[key], key))
        consensus_match_rate = signature_counts[consensus_signature] / len(suggestions)
        rosetta_reference_signature = signatures[0]
        exact_match_rate = signature_counts[rosetta_reference_signature] / len(suggestions)

        confidences = [float(suggestion.get("confidence") or 0.0) for suggestion in suggestions]
        avg_confidence = sum(confidences) / len(confidences)
        pairwise_span_f1 = self._pairwise_span_f1(suggestions)
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
                    "pairwise_span_f1": self._candidate_pairwise_span_f1(suggestion, suggestions),
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
        uncertainty_score = self._rosetta_uncertainty_score(pairwise_span_f1, avg_confidence)
        rosetta_route = self._rosetta_consistency_route(pairwise_span_f1, exact_match_rate, avg_confidence)
        return {
            "score": round(score, 4),
            "agreement": round(agreement, 4),
            "pairwise_span_f1": pairwise_span_f1,
            "exact_match_rate": round(exact_match_rate, 4),
            "consensus_match_rate": round(consensus_match_rate, 4),
            "average_model_confidence": round(avg_confidence, 4),
            "avg_confidence": round(avg_confidence, 4),
            "avg_rule_risk": round(avg_rule_risk, 4),
            "uncertainty_score": uncertainty_score,
            "overlap_conflict_rate": round(overlap_conflict_rate, 4),
            "review_risk": round(review_risk, 4),
            "review_route": review_route,
            "rosetta_route": rosetta_route,
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

    @classmethod
    def _pairwise_span_f1(cls, suggestions: list[dict[str, Any]]) -> float:
        if len(suggestions) <= 1:
            return 1.0 if suggestions else 0.0
        scores = []
        for left_index, left in enumerate(suggestions):
            for right in suggestions[left_index + 1 :]:
                scores.append(cls._span_f1_between_suggestions(left, right))
        return round(sum(scores) / len(scores), 4) if scores else 0.0

    @classmethod
    def _candidate_pairwise_span_f1(cls, suggestion: dict[str, Any], suggestions: list[dict[str, Any]]) -> float:
        peers = [peer for peer in suggestions if peer["id"] != suggestion["id"]]
        if not peers:
            return 1.0
        scores = [cls._span_f1_between_suggestions(suggestion, peer) for peer in peers]
        return round(sum(scores) / len(scores), 4)

    @classmethod
    def _span_f1_between_suggestions(cls, left: dict[str, Any], right: dict[str, Any]) -> float:
        if cls._suggestion_signature(left) == cls._suggestion_signature(right):
            return 1.0
        return 0.0

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
                str(suggestion.get("text") or ""),
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
    def _rosetta_uncertainty_score(pairwise_span_f1: float, avg_confidence: float | None) -> float:
        disagreement = 1 - pairwise_span_f1
        if avg_confidence is None:
            return round(disagreement, 4)
        confidence_penalty = 1 - avg_confidence
        return round((disagreement * 0.7) + (confidence_penalty * 0.3), 4)

    @staticmethod
    def _rosetta_consistency_route(pairwise_span_f1: float, exact_match_rate: float, avg_confidence: float | None) -> str:
        confidence_ok = avg_confidence is None or avg_confidence >= 0.7
        if (
            pairwise_span_f1 >= 0.95
            and exact_match_rate >= 0.8
            and confidence_ok
        ):
            return "high"
        if pairwise_span_f1 >= 0.6:
            return "medium"
        return "low"

    @classmethod
    def _candidate_uncertainty_score(cls, suggestion: dict[str, Any], candidate_score: dict[str, Any]) -> float:
        span_f1 = float(candidate_score.get("span_f1_to_consensus", 1.0))
        confidence = float(suggestion.get("confidence") or 0.0)
        review_risk = float(candidate_score.get("review_risk", cls._review_recommendation_risk((suggestion.get("latest_review") or {}).get("recommendation"))))
        uncertainty = ((1.0 - span_f1) * 0.5) + ((1.0 - confidence) * 0.3) + (review_risk * 0.2)
        return round(max(0.0, min(1.0, uncertainty)), 4)

    @staticmethod
    def _inline_span_markup(text: str, start: int, end: int, label: str) -> str:
        return f"{text[:start]}[{text[start:end]}]{{{label}}}{text[end:]}"

    @staticmethod
    def _inline_spans_markup(text: str, spans: list[dict[str, Any]]) -> str:
        markup = text
        ordered = sorted(spans, key=lambda span: (int(span["start"]), int(span["end"])), reverse=True)
        for span in ordered:
            start = int(span["start"])
            end = int(span["end"])
            markup = f"{markup[:start]}[{markup[start:end]}]{{{span['label']}}}{markup[end:]}"
        return markup

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
        tag_schema_context = self._prodigy_tag_schema_context(project_id)
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
                    "tag_schema": tag_schema_context,
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

    def _prodigy_tag_schema_context(self, project_id: str) -> dict[str, Any]:
        payload = self.export_tag_schema(project_id)
        return {
            "schema_version": payload["schema_version"],
            "content_sha256": payload["content_sha256"],
            "tag_count": payload["tag_count"],
            "labels": [
                {
                    "id": tag["id"],
                    "name": tag["name"],
                    "description": tag.get("description"),
                    "examples": tag.get("examples", []),
                    "taxonomy": tag.get("taxonomy"),
                    "shortcut": tag.get("shortcut"),
                    "color": tag.get("color"),
                }
                for tag in payload["tags"]
            ],
        }

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
    def _prodigy_labels_content_payload(payload: dict[str, Any]) -> dict[str, Any]:
        return json.loads(
            json.dumps(
                {key: value for key, value in payload.items() if key not in {"generated_at", "content_sha256"}},
                ensure_ascii=False,
            )
        )

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
            "risk_reason_codes": suggestion.get("risk_reason_codes") or self._suggestion_risk_reason_codes(suggestion),
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

    def _suggestion_risk_reason_codes(self, suggestion: dict[str, Any]) -> list[str]:
        codes: list[str] = []
        latest_review = suggestion.get("latest_review") or {}
        judge = latest_review.get("judge") or {}
        recommendation = latest_review.get("recommendation")
        if recommendation == "reject":
            codes.append("llm_reject")
        elif recommendation == "uncertain":
            codes.append("llm_uncertain")
        if isinstance(judge, dict):
            error_types = set(judge.get("error_types") or [])
            risk_flags = set(judge.get("risk_flags") or [])
            boundary_score = self._float_or_default(judge.get("boundary_score"), 1.0)
            missed_span_risk = self._float_or_default(judge.get("missed_span_risk"), 0.0)
            extra_span_risk = self._float_or_default(judge.get("extra_span_risk"), 0.0)
            overall_score = self._float_or_default(judge.get("overall_score"), 1.0)
            if judge.get("needs_review") is True:
                codes.append("judge_needs_review")
            if boundary_score <= 0.65 or {"boundary_too_wide", "boundary_too_narrow"} & error_types:
                codes.append("judge_boundary")
            if missed_span_risk >= 0.5 or "missed_span" in error_types or "possible_under_annotation" in risk_flags:
                codes.append("judge_missing_span")
            if extra_span_risk >= 0.5 or "extra_span" in error_types or "possible_over_annotation" in risk_flags:
                codes.append("judge_extra_span")
            if overall_score <= 0.75:
                codes.append("judge_low_score")
        if float(suggestion.get("confidence") or 0.0) < self.medium_confidence_threshold:
            codes.append("low_confidence")
        return list(dict.fromkeys(codes))

    @staticmethod
    def _float_or_default(value: Any, default: float) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _goldsmith_label_tokens(text: str) -> list[dict[str, Any]]:
        return [
            {"token": match.group(0).lower(), "start": match.start(), "end": match.end()}
            for match in GOLDSMITH_LABEL_TOKEN_PATTERN.finditer(text)
        ]

    @classmethod
    def _goldsmith_label_statistics(
        cls,
        document: dict[str, Any],
        *,
        context_window: int = 2,
        exclude_sentence_id: str | None = None,
        annotated_only: bool = False,
    ) -> tuple[dict[str, dict[str, Any]], int, int]:
        stats: dict[str, dict[str, Any]] = {}
        sentence_count = 0
        annotation_count = 0
        for sentence in document["sentences"]:
            if exclude_sentence_id is not None and sentence["id"] == exclude_sentence_id:
                continue
            if annotated_only and not sentence.get("annotations"):
                continue
            tokens = cls._goldsmith_label_tokens(sentence["text"])
            if not tokens:
                continue
            sentence_count += 1
            spans = cls._goldsmith_annotation_spans(sentence)
            annotation_count += len(spans)
            entity_indices: set[int] = set()
            labels_by_index: dict[int, set[str]] = {}
            for index, token in enumerate(tokens):
                for span in spans:
                    if cls._goldsmith_token_overlaps_span(token, span):
                        entity_indices.add(index)
                        labels_by_index.setdefault(index, set()).add(span["label"])
            context_indices: set[int] = set()
            for index in entity_indices:
                for offset in range(1, context_window + 1):
                    if index - offset >= 0:
                        context_indices.add(index - offset)
                    if index + offset < len(tokens):
                        context_indices.add(index + offset)
            context_indices -= entity_indices

            for index, token in enumerate(tokens):
                bucket = stats.setdefault(
                    token["token"],
                    {
                        "entity_count": 0,
                        "context_count": 0,
                        "other_count": 0,
                        "label_entity_counts": {},
                    },
                )
                if index in entity_indices:
                    bucket["entity_count"] += 1
                    for label in sorted(labels_by_index.get(index, set())):
                        label_counts = bucket["label_entity_counts"]
                        label_counts[label] = int(label_counts.get(label, 0)) + 1
                elif index in context_indices:
                    bucket["context_count"] += 1
                else:
                    bucket["other_count"] += 1
        return stats, sentence_count, annotation_count

    @staticmethod
    def _goldsmith_annotation_spans(sentence: dict[str, Any]) -> list[dict[str, Any]]:
        sentence_start = int(sentence["start_char"])
        spans = []
        for index, annotation in enumerate(sentence.get("annotations") or []):
            spans.append(
                {
                    "id": f"T{index + 1}",
                    "start": int(annotation["start_char"]) - sentence_start,
                    "end": int(annotation["end_char"]) - sentence_start,
                    "text": annotation["text"],
                    "label": annotation["tag_name"],
                    "implicit": False,
                    "token_start": annotation["start_token_index"],
                    "token_end": annotation["end_token_index"],
                }
            )
        return spans

    @staticmethod
    def _goldsmith_token_overlaps_span(token: dict[str, Any], span: dict[str, Any]) -> bool:
        if span.get("implicit"):
            return False
        return int(token["start"]) < int(span["end"]) and int(token["end"]) > int(span["start"])

    @classmethod
    def _goldsmith_stat_probability(cls, stat: dict[str, Any], bucket_name: str) -> float:
        total = int(stat.get("entity_count") or 0) + int(stat.get("context_count") or 0) + int(stat.get("other_count") or 0)
        return cls._safe_ratio(int(stat.get(bucket_name) or 0), total)

    @staticmethod
    def _dedupe_reflection_items(items: list[dict[str, Any]], *, max_items: int) -> list[dict[str, Any]]:
        deduped = []
        seen = set()
        for item in items:
            key = (item["item_type"], item["start"], item["end"], item["token"])
            if key in seen:
                continue
            seen.add(key)
            deduped.append(item)
            if len(deduped) >= max_items:
                break
        return deduped

    @staticmethod
    def _goldsmith_contrastive_sample(project_id: str, document_id: str, sentence: dict[str, Any]) -> dict[str, Any]:
        spans = ExportService._goldsmith_annotation_spans(sentence)
        return {
            "schema_version": "rosetta.prodigy_jsonl.v1",
            "id": sentence["id"],
            "text": sentence["text"],
            "tokens": [],
            "spans": spans,
            "relations": [],
            "answer": sentence.get("answer", "accept" if sentence.get("completed") else "pending"),
            "meta": {
                "source": "annopilot",
                "project_id": project_id,
                "document_id": document_id,
                "sentence_id": sentence["id"],
                "sentence_index": sentence["index"],
            },
        }

    @staticmethod
    def _contrastive_hit(role: str, sample: dict[str, Any], score: float) -> dict[str, Any]:
        return {
            "role": role,
            "sample_id": sample["id"],
            "score": score,
            "sample": sample,
        }

    @classmethod
    def _lexical_similarity(cls, left: str, right: str) -> float:
        left_tokens = cls._goldsmith_contrastive_token_set(left)
        right_tokens = cls._goldsmith_contrastive_token_set(right)
        if not left_tokens or not right_tokens:
            return 0.0
        union = left_tokens | right_tokens
        return round(len(left_tokens & right_tokens) / len(union), 4) if union else 0.0

    @staticmethod
    def _goldsmith_contrastive_token_set(text: str) -> set[str]:
        return {match.group(0).lower() for match in GOLDSMITH_LABEL_TOKEN_PATTERN.finditer(text)}

    @staticmethod
    def _safe_ratio(numerator: int, denominator: int) -> float:
        if denominator <= 0:
            return 0.0
        return numerator / denominator

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

    def _prodigy_readiness(
        self,
        metrics: dict[str, Any],
        *,
        verification_summary: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        sentence_count = int(metrics.get("sentence_count") or 0)
        completed_count = int(metrics.get("completed_count") or 0)
        annotation_count = int(metrics.get("annotation_count") or 0)
        progress = float(metrics.get("progress") or 0.0)
        annotation_label_counts = metrics.get("annotation_label_counts") or []
        suggestion_label_counts = metrics.get("suggestion_label_counts") or []
        suggestion_status_counts = metrics.get("suggestion_status_counts") or {}
        pending_suggestion_count = int(
            suggestion_status_counts.get("pending")
            if suggestion_status_counts.get("pending") is not None
            else sum(int(item.get("count") or 0) for item in suggestion_label_counts)
        )
        covered_label_count = sum(1 for item in annotation_label_counts if int(item.get("count") or 0) > 0)
        total_label_count = len(annotation_label_counts)
        blockers = []
        if sentence_count == 0:
            blockers.append("no_sentences")
        if completed_count < sentence_count:
            blockers.append("incomplete_sentences")
        if annotation_count == 0:
            blockers.append("no_annotations")
        if pending_suggestion_count > 0:
            blockers.append("pending_suggestions")
        verification_status = str((verification_summary or {}).get("status") or "unknown")
        verification_issue_count = int((verification_summary or {}).get("issue_count") or 0)
        verification_error_count = int((verification_summary or {}).get("error_count") or 0)
        verification_warning_count = int((verification_summary or {}).get("warning_count") or 0)
        if verification_error_count > 0 or verification_status == "error":
            blockers.append("verification_errors")
        elif verification_warning_count > 0 or verification_status == "warning":
            blockers.append("verification_warnings")

        return {
            "ready": not blockers,
            "status": "ready" if not blockers else "needs_attention",
            "blockers": blockers,
            "sentence_count": sentence_count,
            "completed_sentence_count": completed_count,
            "progress": progress,
            "annotation_count": annotation_count,
            "covered_label_count": covered_label_count,
            "total_label_count": total_label_count,
            "pending_suggestion_count": pending_suggestion_count,
            "verification_status": verification_status,
            "verification_issue_count": verification_issue_count,
            "verification_error_count": verification_error_count,
            "verification_warning_count": verification_warning_count,
            "formats": {
                "ner_manual": self.prodigy_export_schema_version,
                "spans_manual": self.prodigy_spans_export_schema_version,
            },
        }

    @classmethod
    def _verification_summary_from_lines(cls, lines: list[str]) -> dict[str, Any]:
        payloads = cls._jsonl_payloads(lines)
        if not payloads:
            return {"status": "unknown", "issue_count": 0, "error_count": 0, "warning_count": 0}
        summary = payloads[0].get("summary") or {}
        return {
            "status": str(summary.get("status") or "unknown"),
            "issue_count": int(summary.get("issue_count") or 0),
            "error_count": int(summary.get("error_count") or 0),
            "warning_count": int(summary.get("warning_count") or 0),
        }

    @staticmethod
    def _prodigy_bundle_readme(project_id: str, document_id: str, manifest: dict[str, Any]) -> str:
        readiness = manifest.get("prodigy_readiness", {})
        artifacts = manifest.get("artifacts", {})
        labels_artifact = manifest.get("artifacts", {}).get("prodigy_labels_json", {})

        def artifact_line(key: str, description: str) -> str:
            artifact = artifacts.get(key, {})
            filename = artifact.get("filename", key)
            schema_version = artifact.get("schema_version", "unknown")
            line_count = artifact.get("line_count", "unknown")
            return f"- {filename}: {description} ({schema_version}, {line_count} lines)."

        return "\n".join(
            [
                "AnnoPilot Prodigy Export Bundle",
                "================================",
                f"Project: {project_id}",
                f"Document: {document_id}",
                f"Manifest content_sha256: {manifest.get('content_sha256', '')}",
                f"Prodigy readiness: {readiness.get('status', 'unknown')}",
                f"Pending suggestions: {readiness.get('pending_suggestion_count', 'unknown')}",
                f"Labels: {labels_artifact.get('filename', '')}",
                "",
                "Recommended Prodigy entrypoints:",
                f"- {document_id}.prodigy.jsonl for ner.manual-style review.",
                f"- {document_id}.prodigy.spans.jsonl for spans.manual-style review.",
                "- The Prodigy labels JSON contains label definitions and command templates.",
                "",
                "Goldsmith/Rosetta review artifacts:",
                artifact_line("goldsmith_bootstrap_report_md", "human-readable bootstrap summary and recommended review actions"),
                artifact_line("goldsmith_review_tasks_jsonl", "human review tasks with candidate options and manual fallback"),
                artifact_line("goldsmith_review_queue_jsonl", "ranked queue with risk scores and route reasons"),
                artifact_line("goldsmith_risk_reasons_jsonl", "aggregated risk reason summaries for review planning"),
                artifact_line("goldsmith_candidate_runs_jsonl", "Rosetta-style candidate span records"),
                artifact_line("goldsmith_consistency_scores_jsonl", "sentence-level agreement and route diagnostics"),
                artifact_line("goldsmith_label_statistics_jsonl", "Rosetta-style token label statistics for seed and negative-example optimization"),
                artifact_line("goldsmith_contrastive_examples_jsonl", "Rosetta-style similar and boundary examples for prompt and guideline calibration"),
                artifact_line("goldsmith_prompt_package_jsonl", "Rosetta-style prompt tasks for LLM or expert review"),
                artifact_line("goldsmith_verification_report_jsonl", "verifier.py-style checks for export offsets, labels, markup, and prompt contracts"),
                artifact_line("goldsmith_reflection_plans_jsonl", "Rosetta-style reflection plans for missed-span and boundary review"),
                artifact_line("goldsmith_boundary_feedback_jsonl", "boundary feedback from hard examples and LLM review"),
                artifact_line("goldsmith_hard_examples_jsonl", "human-disagreed or risky examples for guideline refinement"),
                artifact_line("goldsmith_human_choices_jsonl", "accepted/rejected human decisions for calibration"),
                "",
                "Audit and reproducibility:",
                artifact_line("events_jsonl", "project event log for rebuild/audit"),
                artifact_line("tag_schema_json", "label schema definitions and lexical examples"),
                "- The manifest file records artifact hashes, readiness blockers, and audit state.",
                "- If readiness is needs_attention, resolve pending suggestions or incomplete sentences before treating exports as final gold.",
                "",
            ]
        )

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
        if sources == {"auto_monogloss"}:
            return "auto-monogloss"
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

    @classmethod
    def _jsonl_content_sha256(cls, lines: list[str]) -> str:
        records = [cls._without_volatile_export_fields(payload) for payload in cls._jsonl_payloads(lines)]
        return payload_sha256({"records": records})

    @classmethod
    def _markdown_content_sha256(cls, lines: list[str]) -> str:
        stable_lines = [line for line in lines if not line.startswith("- Generated at:")]
        return payload_sha256({"lines": stable_lines})

    @classmethod
    def _without_volatile_export_fields(cls, value: Any) -> Any:
        if isinstance(value, dict):
            return {
                key: cls._without_volatile_export_fields(item)
                for key, item in value.items()
                if key != "generated_at"
            }
        if isinstance(value, list):
            return [cls._without_volatile_export_fields(item) for item in value]
        return value

    @staticmethod
    def _stable_hash(payload: dict[str, Any]) -> int:
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        value = int.from_bytes(hashlib.blake2b(encoded, digest_size=4).digest(), byteorder="big", signed=False)
        if value >= 2**31:
            return value - 2**32
        return value
