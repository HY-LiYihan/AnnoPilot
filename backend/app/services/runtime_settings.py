from __future__ import annotations

import sqlite3
from collections.abc import Callable
from typing import Any


class RuntimeSettingsService:
    """Runtime key/value settings persisted in SQLite."""

    def __init__(self, connect: Callable[[], sqlite3.Connection], *, now: Callable[[], str]) -> None:
        self.connect = connect
        self.now = now

    def get_runtime_setting(self, key: str) -> str | None:
        with self.connect() as conn:
            row = conn.execute("SELECT value FROM runtime_settings WHERE key = ?", (key,)).fetchone()
            return row["value"] if row else None

    def set_runtime_setting(self, key: str, value: str) -> dict[str, Any]:
        now = self.now()
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO runtime_settings (key, value, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                  value = excluded.value,
                  updated_at = excluded.updated_at
                """,
                (key, value, now),
            )
        return {"key": key, "value": value, "updated_at": now}
