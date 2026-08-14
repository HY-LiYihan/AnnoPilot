from __future__ import annotations

import json
import re
from typing import Any

from ..hashing import payload_sha256


INLINE_MARKUP_PATTERN = re.compile(r"\[(!?)([^\[\]]+)\]\{([^{}]+)\}")
LEGACY_MARKUP_PATTERN = re.compile(r"\[[^\]]+\]\([^)]+\)")


class ExportVerificationService:
    """Verifier.py-style checks for exported Goldsmith/Rosetta artifacts."""

    def __init__(self, schema_version: str) -> None:
        self.schema_version = schema_version

    def build_lines(
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
        allowed_labels = {str(tag["name"]) for tag in tag_schema.get("tags", [])}
        issues: list[dict[str, Any]] = []

        payloads = {
            "prodigy_jsonl": self._jsonl_payloads(prodigy_lines, "prodigy_jsonl", issues),
            "prodigy_spans_jsonl": self._jsonl_payloads(prodigy_spans_lines, "prodigy_spans_jsonl", issues),
            "goldsmith_candidate_runs_jsonl": self._jsonl_payloads(
                goldsmith_candidate_run_lines,
                "goldsmith_candidate_runs_jsonl",
                issues,
            ),
            "goldsmith_review_tasks_jsonl": self._jsonl_payloads(
                goldsmith_review_task_lines,
                "goldsmith_review_tasks_jsonl",
                issues,
            ),
            "goldsmith_prompt_package_jsonl": self._jsonl_payloads(
                goldsmith_prompt_package_lines,
                "goldsmith_prompt_package_jsonl",
                issues,
            ),
        }

        self._verify_prodigy_records(payloads["prodigy_jsonl"], "prodigy_jsonl", allowed_labels, issues)
        self._verify_prodigy_records(payloads["prodigy_spans_jsonl"], "prodigy_spans_jsonl", allowed_labels, issues)
        self._verify_candidate_runs(payloads["goldsmith_candidate_runs_jsonl"], allowed_labels, issues)
        self._verify_review_tasks(payloads["goldsmith_review_tasks_jsonl"], allowed_labels, issues)
        self._verify_prompt_packages(payloads["goldsmith_prompt_package_jsonl"], issues)

        summary = self._issue_summary(issues)
        record = {
            "schema_version": self.schema_version,
            "record_type": "export_verification_report",
            "generated_at": generated_at,
            "project_id": project_id,
            "document_id": document_id,
            "scope": "document_export",
            "summary": {
                "status": self._status(summary),
                "checked_records": {artifact: len(records) for artifact, records in payloads.items()},
                "sentence_count": document.get("metrics", {}).get("sentence_count", 0),
                "annotation_count": document.get("metrics", {}).get("annotation_count", 0),
                "issue_count": len(issues),
                "error_count": summary["severity_counts"].get("error", 0),
                "warning_count": summary["severity_counts"].get("warning", 0),
                "issue_counts_by_artifact": summary["artifact_counts"],
                "issue_counts_by_code": summary["code_counts"],
            },
            "checks": [
                self._check(
                    "prodigy_span_offsets",
                    "prodigy_jsonl",
                    "Prodigy spans align with exported sentence text",
                    issues,
                ),
                self._check(
                    "prodigy_spans_offsets",
                    "prodigy_spans_jsonl",
                    "Prodigy spans.manual spans align with exported sentence text",
                    issues,
                ),
                self._check(
                    "candidate_run_offsets",
                    "goldsmith_candidate_runs_jsonl",
                    "Candidate run spans and inline markup align with source text",
                    issues,
                ),
                self._check(
                    "review_task_options",
                    "goldsmith_review_tasks_jsonl",
                    "Review task options use valid span labels and markup",
                    issues,
                ),
                self._check(
                    "prompt_package_contract",
                    "goldsmith_prompt_package_jsonl",
                    "Prompt package carries output contract and verifier metadata",
                    issues,
                ),
            ],
            "issues": issues[:100],
            "truncated_issue_count": max(0, len(issues) - 100),
            "meta": {
                "source": "annopilot",
                "artifact": "verification_report.jsonl",
                "rosetta_reference": "verifier.py",
                "annotation_format": "[原文]{标签} / [!隐含义]{标签}",
                "tag_schema_sha256": tag_schema.get("content_sha256"),
            },
        }
        record["content_sha256"] = payload_sha256(self._without_volatile_fields(record))
        return [json.dumps(record, ensure_ascii=False) + "\n"]

    @classmethod
    def _jsonl_payloads(cls, lines: list[str], artifact: str, issues: list[dict[str, Any]]) -> list[dict[str, Any]]:
        payloads: list[dict[str, Any]] = []
        for line_number, line in enumerate(lines, start=1):
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError as exc:
                cls._issue(
                    issues,
                    artifact=artifact,
                    code="invalid_json",
                    severity="error",
                    message=f"JSONL line {line_number} cannot be parsed: {exc.msg}",
                    line_number=line_number,
                )
                continue
            if not isinstance(payload, dict):
                cls._issue(
                    issues,
                    artifact=artifact,
                    code="invalid_record",
                    severity="error",
                    message=f"JSONL line {line_number} is not an object",
                    line_number=line_number,
                )
                continue
            payloads.append(payload)
        return payloads

    @classmethod
    def _verify_prodigy_records(
        cls,
        records: list[dict[str, Any]],
        artifact: str,
        allowed_labels: set[str],
        issues: list[dict[str, Any]],
    ) -> None:
        for record in records:
            text = str(record.get("text") or "")
            record_id = cls._record_id(record)
            answer = record.get("answer")
            sentence_id = (record.get("meta") or {}).get("sentence_id")
            if answer not in {"accept", "reject", "ignore"}:
                cls._issue(
                    issues,
                    artifact=artifact,
                    code="invalid_answer",
                    severity="warning",
                    message=f"Prodigy answer `{answer}` is not one of accept/reject/ignore",
                    record_id=record_id,
                    sentence_id=sentence_id,
                )
            spans = record.get("spans") or []
            for span_index, span in enumerate(spans):
                cls._verify_span(artifact, text, span, allowed_labels, issues, record_id, span_index, sentence_id)
            cls._verify_non_overlapping_spans(artifact, spans, issues, record_id, sentence_id)

    @classmethod
    def _verify_candidate_runs(
        cls,
        records: list[dict[str, Any]],
        allowed_labels: set[str],
        issues: list[dict[str, Any]],
    ) -> None:
        artifact = "goldsmith_candidate_runs_jsonl"
        for record in records:
            text = str(record.get("text") or "")
            record_id = cls._record_id(record)
            sentence_id = (record.get("meta") or {}).get("sentence_id")
            if not str(record.get("explanation") or "").strip():
                cls._issue(
                    issues,
                    artifact=artifact,
                    code="missing_explanation",
                    severity="error",
                    message="Candidate run explanation is empty",
                    record_id=record_id,
                    sentence_id=sentence_id,
                )
            for span_index, span in enumerate(record.get("spans") or []):
                cls._verify_span(artifact, text, span, allowed_labels, issues, record_id, span_index, sentence_id)
            cls._verify_markup(
                artifact=artifact,
                markup=str((record.get("runtime_annotation") or {}).get("annotation_markup") or ""),
                source_text=text,
                allowed_labels=allowed_labels,
                issues=issues,
                record_id=record_id,
                sentence_id=sentence_id,
            )

    @classmethod
    def _verify_review_tasks(
        cls,
        records: list[dict[str, Any]],
        allowed_labels: set[str],
        issues: list[dict[str, Any]],
    ) -> None:
        artifact = "goldsmith_review_tasks_jsonl"
        for record in records:
            text = str(record.get("text") or "")
            record_id = cls._record_id(record)
            sentence_id = record.get("sentence_id")
            options = record.get("options") or []
            if int(record.get("candidate_count") or 0) != len(options):
                cls._issue(
                    issues,
                    artifact=artifact,
                    code="candidate_count_mismatch",
                    severity="warning",
                    message="candidate_count does not match options length",
                    record_id=record_id,
                    sentence_id=sentence_id,
                )
            for option in options:
                option_id = str(option.get("option_id") or "")
                cls._verify_span(
                    artifact,
                    text,
                    option.get("span") or {},
                    allowed_labels,
                    issues,
                    record_id,
                    option_id,
                    sentence_id,
                )
                cls._verify_markup(
                    artifact=artifact,
                    markup=str(option.get("annotation_markup") or ""),
                    source_text=text,
                    allowed_labels=allowed_labels,
                    issues=issues,
                    record_id=f"{record_id}:{option_id}",
                    sentence_id=sentence_id,
                )

    @classmethod
    def _verify_prompt_packages(cls, records: list[dict[str, Any]], issues: list[dict[str, Any]]) -> None:
        artifact = "goldsmith_prompt_package_jsonl"
        required_contract = {"text", "annotation", "explanation", "selected_option_id", "answer"}
        required_checks = {"valid_json", "text_match", "valid_annotation_markup", "explicit_span_present_in_text"}
        for record in records:
            record_id = cls._record_id(record)
            sentence_id = record.get("sentence_id")
            missing_contract = sorted(required_contract - set(record.get("output_contract") or []))
            if missing_contract:
                cls._issue(
                    issues,
                    artifact=artifact,
                    code="missing_output_contract_fields",
                    severity="error",
                    message=f"Prompt package output_contract is missing: {', '.join(missing_contract)}",
                    record_id=record_id,
                    sentence_id=sentence_id,
                )
            verification = record.get("verification") or {}
            missing_checks = sorted(required_checks - set(verification.get("checks") or []))
            if verification.get("rosetta_reference") != "verifier.py" or missing_checks:
                cls._issue(
                    issues,
                    artifact=artifact,
                    code="missing_verifier_metadata",
                    severity="warning",
                    message="Prompt package does not fully declare verifier.py checks",
                    record_id=record_id,
                    sentence_id=sentence_id,
                )
            if (record.get("review_task") or {}).get("sentence_id") != sentence_id:
                cls._issue(
                    issues,
                    artifact=artifact,
                    code="review_task_mismatch",
                    severity="error",
                    message="Embedded review_task sentence_id does not match prompt package sentence_id",
                    record_id=record_id,
                    sentence_id=sentence_id,
                )

    @classmethod
    def _verify_span(
        cls,
        artifact: str,
        text: str,
        span: dict[str, Any],
        allowed_labels: set[str],
        issues: list[dict[str, Any]],
        record_id: str,
        span_index: int | str,
        sentence_id: Any,
    ) -> None:
        label = str(span.get("label") or "")
        if label not in allowed_labels:
            cls._issue(
                issues,
                artifact=artifact,
                code="unknown_label",
                severity="error",
                message=f"Span label `{label}` is not in current tag schema",
                record_id=record_id,
                sentence_id=sentence_id,
                span_index=span_index,
            )
        try:
            start = int(span.get("start"))
            end = int(span.get("end"))
        except (TypeError, ValueError):
            cls._issue(
                issues,
                artifact=artifact,
                code="invalid_span_offsets",
                severity="error",
                message="Span start/end are not integers",
                record_id=record_id,
                sentence_id=sentence_id,
                span_index=span_index,
            )
            return
        if start < 0 or end < start or end > len(text):
            cls._issue(
                issues,
                artifact=artifact,
                code="span_out_of_bounds",
                severity="error",
                message=f"Span offsets {start}:{end} are outside text length {len(text)}",
                record_id=record_id,
                sentence_id=sentence_id,
                span_index=span_index,
            )
            return
        if span.get("text") is not None and text[start:end] != span.get("text"):
            cls._issue(
                issues,
                artifact=artifact,
                code="span_text_mismatch",
                severity="error",
                message=f"Span text `{span.get('text')}` does not match source slice `{text[start:end]}`",
                record_id=record_id,
                sentence_id=sentence_id,
                span_index=span_index,
            )

    @classmethod
    def _verify_non_overlapping_spans(
        cls,
        artifact: str,
        spans: list[dict[str, Any]],
        issues: list[dict[str, Any]],
        record_id: str,
        sentence_id: Any,
    ) -> None:
        parsed_spans: list[tuple[int, int, int, str]] = []
        for span_index, span in enumerate(spans):
            try:
                start = int(span.get("start"))
                end = int(span.get("end"))
            except (AttributeError, TypeError, ValueError):
                continue
            parsed_spans.append((start, end, span_index, str(span.get("label") or "")))

        sorted_spans = sorted(parsed_spans)
        for left, right in zip(sorted_spans, sorted_spans[1:]):
            left_start, left_end, left_index, left_label = left
            right_start, right_end, right_index, right_label = right
            if left_end <= right_start:
                continue
            cls._issue(
                issues,
                artifact=artifact,
                code="overlapping_spans",
                severity="warning",
                message=(
                    "Exported Prodigy spans overlap; review in spans.manual or resolve boundary conflicts before ner.manual."
                ),
                record_id=record_id,
                sentence_id=sentence_id,
                span_index=right_index,
                overlapping_span_index=left_index,
                left_span={"start": left_start, "end": left_end, "label": left_label},
                right_span={"start": right_start, "end": right_end, "label": right_label},
            )

    @classmethod
    def _verify_markup(
        cls,
        *,
        artifact: str,
        markup: str,
        source_text: str,
        allowed_labels: set[str],
        issues: list[dict[str, Any]],
        record_id: str,
        sentence_id: Any,
    ) -> None:
        if not markup.strip():
            cls._issue(
                issues,
                artifact=artifact,
                code="missing_annotation",
                severity="error",
                message="Inline annotation markup is empty",
                record_id=record_id,
                sentence_id=sentence_id,
            )
            return
        if LEGACY_MARKUP_PATTERN.search(markup):
            cls._issue(
                issues,
                artifact=artifact,
                code="legacy_annotation_markup",
                severity="error",
                message="Annotation uses legacy [text](label) markup instead of [text]{label}",
                record_id=record_id,
                sentence_id=sentence_id,
            )
        matches = list(INLINE_MARKUP_PATTERN.finditer(markup))
        if not matches:
            cls._issue(
                issues,
                artifact=artifact,
                code="invalid_annotation",
                severity="error",
                message="Annotation does not contain valid [text]{label} markup",
                record_id=record_id,
                sentence_id=sentence_id,
            )
            return
        for match in matches:
            implicit = match.group(1) == "!"
            span_text = match.group(2)
            label = match.group(3)
            if label not in allowed_labels:
                cls._issue(
                    issues,
                    artifact=artifact,
                    code="unknown_label",
                    severity="error",
                    message=f"Inline label `{label}` is not in current tag schema",
                    record_id=record_id,
                    sentence_id=sentence_id,
                )
            if not implicit and span_text not in source_text:
                cls._issue(
                    issues,
                    artifact=artifact,
                    code="span_not_found",
                    severity="error",
                    message=f"Explicit inline span `{span_text}` was not found in source text",
                    record_id=record_id,
                    sentence_id=sentence_id,
                )

    @staticmethod
    def _record_id(record: dict[str, Any]) -> str:
        meta = record.get("meta") or {}
        for key in ("candidate_id", "sample_id", "sentence_id", "id"):
            if record.get(key):
                return str(record[key])
        return str(
            meta.get("suggestion_id")
            or meta.get("sentence_id")
            or record.get("_task_hash")
            or "unknown"
        )

    @staticmethod
    def _issue(
        issues: list[dict[str, Any]],
        *,
        artifact: str,
        code: str,
        severity: str,
        message: str,
        **context: Any,
    ) -> None:
        issue = {"artifact": artifact, "code": code, "severity": severity, "message": message}
        issue.update({key: value for key, value in context.items() if value is not None})
        issues.append(issue)

    @staticmethod
    def _issue_summary(issues: list[dict[str, Any]]) -> dict[str, dict[str, int]]:
        summary = {"artifact_counts": {}, "code_counts": {}, "severity_counts": {}}
        for issue in issues:
            for summary_key, issue_key in (
                ("artifact_counts", "artifact"),
                ("code_counts", "code"),
                ("severity_counts", "severity"),
            ):
                value = str(issue.get(issue_key) or "unknown")
                bucket = summary[summary_key]
                bucket[value] = bucket.get(value, 0) + 1
        return {key: dict(sorted(value.items())) for key, value in summary.items()}

    @staticmethod
    def _status(summary: dict[str, dict[str, int]]) -> str:
        if summary["severity_counts"].get("error", 0):
            return "error"
        if summary["severity_counts"].get("warning", 0):
            return "warning"
        return "ok"

    @staticmethod
    def _check(name: str, artifact: str, description: str, issues: list[dict[str, Any]]) -> dict[str, Any]:
        artifact_issues = [issue for issue in issues if issue.get("artifact") == artifact]
        error_count = sum(1 for issue in artifact_issues if issue.get("severity") == "error")
        warning_count = sum(1 for issue in artifact_issues if issue.get("severity") == "warning")
        return {
            "name": name,
            "artifact": artifact,
            "description": description,
            "status": "error" if error_count else "warning" if warning_count else "ok",
            "issue_count": len(artifact_issues),
            "error_count": error_count,
            "warning_count": warning_count,
        }

    @classmethod
    def _without_volatile_fields(cls, value: Any) -> Any:
        if isinstance(value, dict):
            return {
                key: cls._without_volatile_fields(item)
                for key, item in value.items()
                if key not in {"generated_at", "content_sha256"}
            }
        if isinstance(value, list):
            return [cls._without_volatile_fields(item) for item in value]
        return value
