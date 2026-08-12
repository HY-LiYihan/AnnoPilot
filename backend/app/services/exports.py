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
    ) -> None:
        self.get_document = get_document
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
            },
        }
        manifest["content_sha256"] = self._payload_sha256(self._manifest_content_payload(manifest))
        return manifest

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
