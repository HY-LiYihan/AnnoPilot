from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable

from .schema import create_base_schema, ensure_column, ensure_legacy_columns


CURRENT_SCHEMA_VERSION = 3


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    apply: Callable[[sqlite3.Connection], None]


MIGRATIONS = (
    Migration(version=1, name="baseline_schema", apply=create_base_schema),
    Migration(version=2, name="ensure_legacy_columns", apply=ensure_legacy_columns),
    Migration(version=3, name="ensure_review_judge_json", apply=lambda conn: ensure_column(conn, "annotation_suggestion_reviews", "judge_json", "TEXT")),
)


def migrate_database(conn: sqlite3.Connection) -> None:
    _ensure_schema_version_table(conn)
    applied_version = current_schema_version(conn)
    for migration in MIGRATIONS:
        if migration.version <= applied_version:
            continue
        migration.apply(conn)
        _record_schema_version(conn, migration)


def current_schema_version(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT COALESCE(MAX(version), 0) AS version FROM schema_version").fetchone()
    return int(row[0] if row is not None else 0)


def _ensure_schema_version_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_version (
          version INTEGER PRIMARY KEY,
          name TEXT NOT NULL,
          applied_at TEXT NOT NULL
        )
        """
    )


def _record_schema_version(conn: sqlite3.Connection, migration: Migration) -> None:
    conn.execute(
        """
        INSERT INTO schema_version (version, name, applied_at)
        VALUES (?, ?, ?)
        ON CONFLICT(version) DO UPDATE SET
          name = excluded.name,
          applied_at = excluded.applied_at
        """,
        (migration.version, migration.name, datetime.now(timezone.utc).isoformat()),
    )
