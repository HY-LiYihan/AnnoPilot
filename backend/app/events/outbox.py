from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any


class EventOutbox:
    """SQLite outbox writer and JSONL flusher for project audit events."""

    def __init__(
        self,
        connect: Callable[[], sqlite3.Connection],
        data_root: Path,
        *,
        event_lock: threading.Lock,
        new_id: Callable[[str], str],
        now: Callable[[], str],
        event_schema_version: str,
        human_actor_id: str,
        system_actor_id: str,
    ) -> None:
        self.connect = connect
        self.data_root = data_root
        self.event_lock = event_lock
        self.new_id = new_id
        self.now = now
        self.event_schema_version = event_schema_version
        self.human_actor_id = human_actor_id
        self.system_actor_id = system_actor_id

    def enqueue(self, conn: sqlite3.Connection, project_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        actor = self._event_actor(payload)
        event = {
            "schema_version": self.event_schema_version,
            "record_type": "event",
            "event_id": self.new_id("evt"),
            "ts": self.now(),
            "project_id": project_id,
            **actor,
            **payload,
        }
        conn.execute(
            """
            INSERT INTO event_outbox (id, project_id, event_json, created_at, flushed_at)
            VALUES (?, ?, ?, ?, NULL)
            """,
            (event["event_id"], project_id, json.dumps(event, ensure_ascii=False), event["ts"]),
        )
        return event

    def flush(self, project_id: str) -> int:
        with self.event_lock:
            with self.connect() as conn:
                rows = conn.execute(
                    """
                    SELECT id, event_json
                    FROM event_outbox
                    WHERE project_id = ? AND flushed_at IS NULL
                    ORDER BY created_at, id
                    """,
                    (project_id,),
                ).fetchall()
            if not rows:
                return 0

            project_dir = self.data_root / project_id
            project_dir.mkdir(parents=True, exist_ok=True)
            event_path = project_dir / "events.jsonl"
            flushed_ids = [row["id"] for row in rows]
            with event_path.open("a", encoding="utf-8") as handle:
                for row in rows:
                    handle.write(row["event_json"] + "\n")

            placeholders = ", ".join("?" for _ in flushed_ids)
            with self.connect() as conn:
                conn.execute(
                    f"UPDATE event_outbox SET flushed_at = ? WHERE id IN ({placeholders})",
                    (self.now(), *flushed_ids),
                )
            return len(flushed_ids)

    def _event_actor(self, payload: dict[str, Any]) -> dict[str, str]:
        event_type = payload.get("type")
        if event_type == "suggestion.llm_reviewed":
            return {"actor_type": "llm", "actor_id": str(payload.get("model") or "unknown")}
        if event_type == "suggestions.generated" or (
            event_type == "annotation.created" and payload.get("source") == "accepted_suggestion"
        ):
            return {"actor_type": "system", "actor_id": self.system_actor_id}
        return {"actor_type": "human", "actor_id": self.human_actor_id}
