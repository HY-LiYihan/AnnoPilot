from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .events import apply_replay_event, event_replay_issue
from .storage import AnnotationStorage


@dataclass
class RebuildIssue:
    line_number: int
    event_id: str | None
    event_type: str | None
    message: str


@dataclass
class RebuildResult:
    project_id: str
    event_count: int = 0
    documents: int = 0
    sentences: int = 0
    tokens: int = 0
    tags: int = 0
    annotations: int = 0
    suggestions: int = 0
    suggestion_reviews: int = 0
    runs: int = 0
    issues: list[RebuildIssue] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.issues

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["ok"] = self.ok
        return payload


def rebuild_project_from_events(
    *,
    project_id: str,
    event_path: Path,
    database_path: Path,
    data_root: Path,
    force: bool = False,
) -> RebuildResult:
    if not event_path.exists():
        raise FileNotFoundError(f"Event log does not exist: {event_path}")
    if database_path.exists() and not force:
        raise FileExistsError(f"Refusing to overwrite existing database: {database_path}")

    _remove_database_files(database_path)
    storage = AnnotationStorage(database_path=database_path, data_root=data_root)
    storage.initialize()

    result = RebuildResult(project_id=project_id)
    with storage.connect() as conn:
        conn.execute("BEGIN")
        for line_number, line in enumerate(event_path.read_text(encoding="utf-8").splitlines(), start=1):
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError as exc:
                result.issues.append(RebuildIssue(line_number, None, None, f"invalid_json: {exc.msg}"))
                continue

            if event.get("project_id") != project_id:
                continue

            result.event_count += 1
            replay_issue = event_replay_issue(event)
            if replay_issue:
                result.issues.append(
                    RebuildIssue(line_number, event.get("event_id"), event.get("type"), replay_issue)
                )
                continue

            try:
                apply_replay_event(conn, project_id, event)
            except Exception as exc:  # pragma: no cover - defensive context for corrupted logs.
                result.issues.append(
                    RebuildIssue(line_number, event.get("event_id"), event.get("type"), f"apply_failed: {exc}")
                )
        conn.commit()

    _populate_counts(storage, result)
    return result


def _populate_counts(storage: AnnotationStorage, result: RebuildResult) -> None:
    with storage.connect() as conn:
        result.documents = conn.execute("SELECT COUNT(*) AS count FROM documents WHERE project_id = ?", (result.project_id,)).fetchone()["count"]
        result.sentences = conn.execute(
            "SELECT COUNT(*) AS count FROM sentences s JOIN documents d ON d.id = s.document_id WHERE d.project_id = ?",
            (result.project_id,),
        ).fetchone()["count"]
        result.tokens = conn.execute(
            "SELECT COUNT(*) AS count FROM tokens t JOIN sentences s ON s.id = t.sentence_id JOIN documents d ON d.id = s.document_id WHERE d.project_id = ?",
            (result.project_id,),
        ).fetchone()["count"]
        result.tags = conn.execute("SELECT COUNT(*) AS count FROM tags WHERE project_id = ?", (result.project_id,)).fetchone()["count"]
        result.annotations = conn.execute(
            "SELECT COUNT(*) AS count FROM annotations a JOIN sentences s ON s.id = a.sentence_id JOIN documents d ON d.id = s.document_id WHERE d.project_id = ?",
            (result.project_id,),
        ).fetchone()["count"]
        result.suggestions = conn.execute(
            "SELECT COUNT(*) AS count FROM annotation_suggestions sg JOIN sentences s ON s.id = sg.sentence_id JOIN documents d ON d.id = s.document_id WHERE d.project_id = ?",
            (result.project_id,),
        ).fetchone()["count"]
        result.suggestion_reviews = conn.execute(
            "SELECT COUNT(*) AS count FROM annotation_suggestion_reviews rev JOIN annotation_suggestions sg ON sg.id = rev.suggestion_id JOIN sentences s ON s.id = sg.sentence_id JOIN documents d ON d.id = s.document_id WHERE d.project_id = ?",
            (result.project_id,),
        ).fetchone()["count"]
        result.runs = conn.execute("SELECT COUNT(*) AS count FROM annotation_runs WHERE project_id = ?", (result.project_id,)).fetchone()["count"]


def _remove_database_files(database_path: Path) -> None:
    for path in (database_path, database_path.with_name(database_path.name + "-wal"), database_path.with_name(database_path.name + "-shm")):
        if path.exists():
            path.unlink()


def main() -> None:
    parser = argparse.ArgumentParser(description="Rebuild an AnnoPilot SQLite database from project events.jsonl.")
    parser.add_argument("--project", default="default")
    parser.add_argument("--events", required=True, type=Path)
    parser.add_argument("--database", required=True, type=Path)
    parser.add_argument("--data-root", required=True, type=Path)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    result = rebuild_project_from_events(
        project_id=args.project,
        event_path=args.events,
        database_path=args.database,
        data_root=args.data_root,
        force=args.force,
    )
    print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
    if not result.ok:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
