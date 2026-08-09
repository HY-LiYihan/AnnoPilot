from __future__ import annotations

import sqlite3
from pathlib import Path

from backend.app.db.migrations import CURRENT_SCHEMA_VERSION, current_schema_version, migrate_database
from backend.app.storage import AnnotationStorage


def test_storage_initialize_records_schema_version(tmp_path: Path) -> None:
    storage = AnnotationStorage(
        database_path=tmp_path / "runtime" / "annopilot.sqlite",
        data_root=tmp_path / "projects",
    )
    storage.initialize()

    with storage.connect() as conn:
        assert current_schema_version(conn) == CURRENT_SCHEMA_VERSION
        tables = {row["name"] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}

    assert {"schema_version", "documents", "sentences", "tokens", "annotations", "event_outbox"} <= tables


def test_migration_backfills_legacy_columns(tmp_path: Path) -> None:
    database_path = tmp_path / "legacy.sqlite"
    conn = sqlite3.connect(database_path)
    try:
        conn.executescript(
            """
            CREATE TABLE tags (
              id TEXT NOT NULL,
              project_id TEXT NOT NULL,
              name TEXT NOT NULL,
              shortcut TEXT NOT NULL,
              color TEXT NOT NULL,
              PRIMARY KEY (project_id, id)
            );

            CREATE TABLE sentences (
              id TEXT PRIMARY KEY,
              document_id TEXT NOT NULL,
              sentence_index INTEGER NOT NULL,
              text TEXT NOT NULL,
              start_char INTEGER NOT NULL,
              end_char INTEGER NOT NULL,
              completed INTEGER NOT NULL DEFAULT 0
            );

            CREATE TABLE annotations (
              id TEXT PRIMARY KEY,
              sentence_id TEXT NOT NULL,
              tag_id TEXT NOT NULL,
              start_token_index INTEGER NOT NULL,
              end_token_index INTEGER NOT NULL,
              start_char INTEGER NOT NULL,
              end_char INTEGER NOT NULL,
              text TEXT NOT NULL,
              created_at TEXT NOT NULL
            );
            """
        )

        migrate_database(conn)

        tag_columns = _columns(conn, "tags")
        sentence_columns = _columns(conn, "sentences")
        annotation_columns = _columns(conn, "annotations")
        assert {"description", "examples_json"} <= tag_columns
        assert "answer" in sentence_columns
        assert {"source", "source_suggestion_id"} <= annotation_columns
        assert current_schema_version(conn) == CURRENT_SCHEMA_VERSION
    finally:
        conn.close()


def _columns(conn: sqlite3.Connection, table_name: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table_name})")}
