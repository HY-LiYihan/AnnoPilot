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

    assert {
        "schema_version",
        "documents",
        "sentences",
        "tokens",
        "annotations",
        "event_outbox",
        "annotation_run_sentences",
        "annotation_run_candidate_spans",
    } <= tables
    with storage.connect() as conn:
        assert "snapshot_complete" in _columns(conn, "annotation_runs")


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


def test_migration_v2_backfills_columns_when_baseline_already_recorded(tmp_path: Path) -> None:
    database_path = tmp_path / "baseline-recorded.sqlite"
    conn = sqlite3.connect(database_path)
    try:
        conn.executescript(
            """
            CREATE TABLE schema_version (
              version INTEGER PRIMARY KEY,
              name TEXT NOT NULL,
              applied_at TEXT NOT NULL
            );
            INSERT INTO schema_version (version, name, applied_at)
            VALUES (1, 'baseline_schema', '2026-08-10T00:00:00+00:00');

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

            CREATE TABLE annotation_suggestions (
              id TEXT PRIMARY KEY,
              sentence_id TEXT NOT NULL,
              tag_id TEXT NOT NULL,
              start_token_index INTEGER NOT NULL,
              end_token_index INTEGER NOT NULL,
              start_char INTEGER NOT NULL,
              end_char INTEGER NOT NULL,
              text TEXT NOT NULL,
              confidence REAL NOT NULL,
              source TEXT NOT NULL,
              status TEXT NOT NULL DEFAULT 'pending',
              created_at TEXT NOT NULL
            );

            CREATE TABLE annotation_suggestion_reviews (
              id TEXT PRIMARY KEY,
              suggestion_id TEXT NOT NULL,
              model TEXT NOT NULL,
              recommendation TEXT NOT NULL,
              confidence REAL NOT NULL,
              rationale TEXT NOT NULL,
              created_at TEXT NOT NULL
            );
            """
        )

        migrate_database(conn)

        assert {"description", "examples_json"} <= _columns(conn, "tags")
        assert "answer" in _columns(conn, "sentences")
        assert {"source", "source_suggestion_id"} <= _columns(conn, "annotations")
        assert {"run_id", "evidence_text", "match_key", "evidence_match_key", "context_before", "context_after"} <= _columns(
            conn,
            "annotation_suggestions",
        )
        assert {"context_sha256", "judge_json"} <= _columns(conn, "annotation_suggestion_reviews")
        assert current_schema_version(conn) == CURRENT_SCHEMA_VERSION
    finally:
        conn.close()


def test_migration_v3_adds_review_judge_json_to_existing_v2_database(tmp_path: Path) -> None:
    database_path = tmp_path / "v2.sqlite"
    conn = sqlite3.connect(database_path)
    try:
        conn.executescript(
            """
            CREATE TABLE schema_version (
              version INTEGER PRIMARY KEY,
              name TEXT NOT NULL,
              applied_at TEXT NOT NULL
            );
            INSERT INTO schema_version (version, name, applied_at)
            VALUES (1, 'baseline_schema', '2026-08-10T00:00:00+00:00');
            INSERT INTO schema_version (version, name, applied_at)
            VALUES (2, 'ensure_legacy_columns', '2026-08-10T00:01:00+00:00');

            CREATE TABLE annotation_suggestion_reviews (
              id TEXT PRIMARY KEY,
              suggestion_id TEXT NOT NULL,
              model TEXT NOT NULL,
              recommendation TEXT NOT NULL,
              confidence REAL NOT NULL,
              rationale TEXT NOT NULL,
              context_sha256 TEXT,
              created_at TEXT NOT NULL
            );
            """
        )

        migrate_database(conn)

        assert "judge_json" in _columns(conn, "annotation_suggestion_reviews")
        assert current_schema_version(conn) == CURRENT_SCHEMA_VERSION
    finally:
        conn.close()


def test_migration_v4_adds_run_candidate_snapshot_schema(tmp_path: Path) -> None:
    database_path = tmp_path / "v3.sqlite"
    conn = sqlite3.connect(database_path)
    try:
        conn.executescript(
            """
            CREATE TABLE schema_version (
              version INTEGER PRIMARY KEY,
              name TEXT NOT NULL,
              applied_at TEXT NOT NULL
            );
            INSERT INTO schema_version (version, name, applied_at)
            VALUES (1, 'baseline_schema', '2026-08-10T00:00:00+00:00');
            INSERT INTO schema_version (version, name, applied_at)
            VALUES (2, 'ensure_legacy_columns', '2026-08-10T00:01:00+00:00');
            INSERT INTO schema_version (version, name, applied_at)
            VALUES (3, 'ensure_review_judge_json', '2026-08-10T00:02:00+00:00');

            CREATE TABLE annotation_runs (
              id TEXT PRIMARY KEY,
              project_id TEXT NOT NULL,
              document_id TEXT NOT NULL,
              recipe TEXT NOT NULL,
              config_json TEXT NOT NULL,
              input_count INTEGER NOT NULL,
              suggestion_count INTEGER NOT NULL,
              created_at TEXT NOT NULL
            );
            """
        )

        migrate_database(conn)

        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        assert "snapshot_complete" in _columns(conn, "annotation_runs")
        assert {"annotation_run_sentences", "annotation_run_candidate_spans"} <= tables
        assert current_schema_version(conn) == CURRENT_SCHEMA_VERSION
    finally:
        conn.close()


def test_migration_v5_adds_tag_taxonomy_metadata(tmp_path: Path) -> None:
    database_path = tmp_path / "v4.sqlite"
    conn = sqlite3.connect(database_path)
    try:
        conn.executescript(
            """
            CREATE TABLE schema_version (
              version INTEGER PRIMARY KEY,
              name TEXT NOT NULL,
              applied_at TEXT NOT NULL
            );
            INSERT INTO schema_version (version, name, applied_at) VALUES (1, 'baseline_schema', '2026-08-10T00:00:00Z');
            INSERT INTO schema_version (version, name, applied_at) VALUES (2, 'ensure_legacy_columns', '2026-08-10T00:01:00Z');
            INSERT INTO schema_version (version, name, applied_at) VALUES (3, 'ensure_review_judge_json', '2026-08-10T00:02:00Z');
            INSERT INTO schema_version (version, name, applied_at) VALUES (4, 'run_candidate_snapshots', '2026-08-10T00:03:00Z');

            CREATE TABLE tags (
              id TEXT NOT NULL,
              project_id TEXT NOT NULL,
              name TEXT NOT NULL,
              description TEXT,
              examples_json TEXT NOT NULL DEFAULT '[]',
              shortcut TEXT NOT NULL,
              color TEXT NOT NULL,
              PRIMARY KEY (project_id, id)
            );
            """
        )

        migrate_database(conn)

        assert "taxonomy_json" in _columns(conn, "tags")
        assert current_schema_version(conn) == CURRENT_SCHEMA_VERSION
    finally:
        conn.close()


def _columns(conn: sqlite3.Connection, table_name: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table_name})")}
