from __future__ import annotations

import sqlite3
from collections.abc import Callable
from typing import Any

from ..events import clear_project_runtime_rows


class ProjectService:
    """Project-level runtime workflows that span multiple domain tables."""

    def __init__(
        self,
        connect: Callable[[], sqlite3.Connection],
        *,
        now: Callable[[], str],
        enqueue_event: Callable[[sqlite3.Connection, str, dict[str, Any]], dict[str, Any]],
        flush_event_outbox: Callable[[str], int],
        seed_tags: Callable[[sqlite3.Connection, str], None],
    ) -> None:
        self.connect = connect
        self.now = now
        self.enqueue_event = enqueue_event
        self.flush_event_outbox = flush_event_outbox
        self.seed_tags = seed_tags

    def reset_project(self, project_id: str) -> dict[str, Any]:
        self.flush_event_outbox(project_id)
        reset_at = self.now()
        with self.connect() as conn:
            conn.execute("BEGIN")
            self.seed_tags(conn, project_id)
            counts = self._count_project_runtime_rows(conn, project_id)
            clear_project_runtime_rows(conn, project_id)
            self.enqueue_event(
                conn,
                project_id,
                {
                    "type": "project.reset",
                    "reset_at": reset_at,
                    **counts,
                },
            )
            conn.commit()

        self.flush_event_outbox(project_id)
        return {"project_id": project_id, "reset_at": reset_at, **counts}

    @classmethod
    def _count_project_runtime_rows(cls, conn: sqlite3.Connection, project_id: str) -> dict[str, int]:
        return {
            "deleted_documents": cls._count_rows(conn, "SELECT COUNT(*) AS count FROM documents WHERE project_id = ?", (project_id,)),
            "deleted_sentences": cls._count_rows(
                conn,
                """
                SELECT COUNT(*) AS count
                FROM sentences s
                JOIN documents d ON d.id = s.document_id
                WHERE d.project_id = ?
                """,
                (project_id,),
            ),
            "deleted_tokens": cls._count_rows(
                conn,
                """
                SELECT COUNT(*) AS count
                FROM tokens t
                JOIN sentences s ON s.id = t.sentence_id
                JOIN documents d ON d.id = s.document_id
                WHERE d.project_id = ?
                """,
                (project_id,),
            ),
            "deleted_annotations": cls._count_rows(
                conn,
                """
                SELECT COUNT(*) AS count
                FROM annotations a
                JOIN sentences s ON s.id = a.sentence_id
                JOIN documents d ON d.id = s.document_id
                WHERE d.project_id = ?
                """,
                (project_id,),
            ),
            "deleted_suggestions": cls._count_rows(
                conn,
                """
                SELECT COUNT(*) AS count
                FROM annotation_suggestions sg
                JOIN sentences s ON s.id = sg.sentence_id
                JOIN documents d ON d.id = s.document_id
                WHERE d.project_id = ?
                """,
                (project_id,),
            ),
            "deleted_suggestion_reviews": cls._count_rows(
                conn,
                """
                SELECT COUNT(*) AS count
                FROM annotation_suggestion_reviews rev
                JOIN annotation_suggestions sg ON sg.id = rev.suggestion_id
                JOIN sentences s ON s.id = sg.sentence_id
                JOIN documents d ON d.id = s.document_id
                WHERE d.project_id = ?
                """,
                (project_id,),
            ),
            "deleted_runs": cls._count_rows(conn, "SELECT COUNT(*) AS count FROM annotation_runs WHERE project_id = ?", (project_id,)),
            "deleted_sessions": cls._count_rows(conn, "SELECT COUNT(*) AS count FROM annotation_sessions WHERE project_id = ?", (project_id,)),
        }

    @staticmethod
    def _count_rows(conn: sqlite3.Connection, query: str, params: tuple[Any, ...]) -> int:
        return int(conn.execute(query, params).fetchone()["count"])
