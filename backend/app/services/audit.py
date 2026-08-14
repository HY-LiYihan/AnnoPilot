from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from pathlib import Path
from typing import Any


class AuditService:
    """Audit-log reads and event-derived project diagnostics."""

    def __init__(
        self,
        connect: Callable[[], sqlite3.Connection],
        data_root: Path,
        *,
        flush_event_outbox: Callable[[str], int],
        event_replay_issue: Callable[[dict[str, Any]], str | None],
    ) -> None:
        self.connect = connect
        self.data_root = data_root
        self.flush_event_outbox = flush_event_outbox
        self.event_replay_issue = event_replay_issue

    def export_event_lines(self, project_id: str) -> list[str]:
        self.flush_event_outbox(project_id)
        event_path = self.data_root / project_id / "events.jsonl"
        if not event_path.exists():
            return []
        return event_path.read_text(encoding="utf-8").splitlines(keepends=True)

    def audit_project(self, project_id: str) -> dict[str, Any]:
        self.flush_event_outbox(project_id)
        with self.connect() as conn:
            pending_outbox_count = conn.execute(
                "SELECT COUNT(*) AS count FROM event_outbox WHERE project_id = ? AND flushed_at IS NULL",
                (project_id,),
            ).fetchone()["count"]

        event_count = 0
        invalid_event_count = 0
        legacy_event_count = 0
        non_replayable_event_count = 0
        replay_issue_counts: dict[str, int] = {}
        replay_issues: list[dict[str, Any]] = []
        schema_versions: set[str] = set()
        event_types: dict[str, int] = {}
        actor_type_counts: dict[str, int] = {}
        actor_id_counts: dict[str, int] = {}
        last_event_type: str | None = None
        last_event_ts: str | None = None

        for line_number, line in enumerate(self.export_event_lines(project_id), start=1):
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                invalid_event_count += 1
                self._record_replay_issue(
                    replay_issue_counts,
                    replay_issues,
                    line_number=line_number,
                    event_id=None,
                    event_type=None,
                    message="invalid_json",
                )
                continue
            event_count += 1
            event_type = str(event.get("type", "unknown"))
            if event.get("record_type") != "event" or not event.get("event_id"):
                if event_type != "unknown":
                    legacy_event_count += 1
                    schema_versions.add("legacy")
                else:
                    invalid_event_count += 1
            if event.get("schema_version"):
                schema_versions.add(str(event["schema_version"]))
            actor_type = str(event.get("actor_type") or "unknown")
            actor_id = str(event.get("actor_id") or "unknown")
            actor_type_counts[actor_type] = actor_type_counts.get(actor_type, 0) + 1
            actor_id_counts[actor_id] = actor_id_counts.get(actor_id, 0) + 1
            replay_issue = self.event_replay_issue(event)
            if replay_issue:
                non_replayable_event_count += 1
                self._record_replay_issue(
                    replay_issue_counts,
                    replay_issues,
                    line_number=line_number,
                    event_id=event.get("event_id"),
                    event_type=event_type,
                    message=replay_issue,
                )
            event_types[event_type] = event_types.get(event_type, 0) + 1
            last_event_type = event_type
            last_event_ts = str(event.get("ts", "")) or None

        rebuild_status = (
            "ready"
            if pending_outbox_count == 0 and invalid_event_count == 0 and legacy_event_count == 0 and non_replayable_event_count == 0
            else "needs_attention"
        )
        return {
            "project_id": project_id,
            "event_count": event_count,
            "pending_outbox_count": pending_outbox_count,
            "invalid_event_count": invalid_event_count,
            "legacy_event_count": legacy_event_count,
            "non_replayable_event_count": non_replayable_event_count,
            "replay_issue_counts": dict(sorted(replay_issue_counts.items())),
            "replay_issues": replay_issues,
            "schema_versions": sorted(schema_versions),
            "event_types": dict(sorted(event_types.items())),
            "actor_type_counts": dict(sorted(actor_type_counts.items())),
            "actor_id_counts": dict(sorted(actor_id_counts.items())),
            "last_event_type": last_event_type,
            "last_event_ts": last_event_ts,
            "rebuild_status": rebuild_status,
        }

    def list_annotation_imports(
        self,
        project_id: str,
        document_id: str | None = None,
        limit: int = 5,
    ) -> dict[str, Any]:
        safe_limit = max(1, min(limit, 50))
        imports: list[dict[str, Any]] = []
        for line in self.export_event_lines(project_id):
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if event.get("type") != "annotations.imported":
                continue
            if document_id and event.get("document_id") != document_id:
                continue
            if not event.get("document_id") or not event.get("filename"):
                continue
            source_record_results = event.get("source_record_results") if isinstance(event.get("source_record_results"), list) else []
            imports.append(
                {
                    "event_id": event.get("event_id"),
                    "document_id": event["document_id"],
                    "filename": event["filename"],
                    "record_count": self._event_int(event.get("record_count")),
                    "matched_count": self._event_int(event.get("matched_count")),
                    "skipped_count": self._event_int(event.get("skipped_count")),
                    "skip_reason_counts": self._annotation_import_skip_reason_counts(event, source_record_results),
                    "created_tag_count": self._event_int(event.get("created_tag_count")),
                    "created_annotation_count": self._event_int(event.get("created_annotation_count")),
                    "deleted_annotation_count": self._event_int(event.get("deleted_annotation_count")),
                    "completed_sentence_count": self._event_int(event.get("completed_sentence_count")),
                    "source_sha256": str(event.get("source_sha256", "")),
                    "source_record_results": source_record_results,
                    "actor_id": event.get("actor_id"),
                    "ts": event.get("ts"),
                }
            )
        return {"imports": list(reversed(imports))[:safe_limit]}

    @classmethod
    def _annotation_import_skip_reason_counts(
        cls,
        event: dict[str, Any],
        source_record_results: list[dict[str, Any]],
    ) -> dict[str, int]:
        raw_counts = event.get("skip_reason_counts")
        if isinstance(raw_counts, dict):
            return {
                str(reason): cls._event_int(count)
                for reason, count in sorted(raw_counts.items())
                if cls._event_int(count) > 0
            }
        counts: dict[str, int] = {}
        for result in source_record_results:
            if not isinstance(result, dict) or result.get("status") != "skipped":
                continue
            reason = str(result.get("reason") or "unknown")
            counts[reason] = counts.get(reason, 0) + 1
        return dict(sorted(counts.items()))

    @staticmethod
    def _record_replay_issue(
        issue_counts: dict[str, int],
        issue_samples: list[dict[str, Any]],
        *,
        line_number: int,
        event_id: str | None,
        event_type: str | None,
        message: str,
    ) -> None:
        issue_counts[message] = issue_counts.get(message, 0) + 1
        if len(issue_samples) >= 5:
            return
        issue_samples.append(
            {
                "line_number": line_number,
                "event_id": event_id,
                "event_type": event_type,
                "message": message,
            }
        )

    @staticmethod
    def _event_int(value: Any) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return 0
