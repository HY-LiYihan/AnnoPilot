from __future__ import annotations

import re
import sqlite3


BASE_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS tags (
  id TEXT NOT NULL,
  project_id TEXT NOT NULL,
  name TEXT NOT NULL,
  description TEXT,
  examples_json TEXT NOT NULL DEFAULT '[]',
  shortcut TEXT NOT NULL,
  color TEXT NOT NULL,
  PRIMARY KEY (project_id, id)
);

CREATE TABLE IF NOT EXISTS runtime_settings (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS documents (
  id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL,
  filename TEXT NOT NULL,
  text TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sentences (
  id TEXT PRIMARY KEY,
  document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  sentence_index INTEGER NOT NULL,
  text TEXT NOT NULL,
  start_char INTEGER NOT NULL,
  end_char INTEGER NOT NULL,
  completed INTEGER NOT NULL DEFAULT 0,
  answer TEXT NOT NULL DEFAULT 'pending'
);

CREATE TABLE IF NOT EXISTS tokens (
  id TEXT PRIMARY KEY,
  sentence_id TEXT NOT NULL REFERENCES sentences(id) ON DELETE CASCADE,
  token_index INTEGER NOT NULL,
  text TEXT NOT NULL,
  start_char INTEGER NOT NULL,
  end_char INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS annotations (
  id TEXT PRIMARY KEY,
  sentence_id TEXT NOT NULL REFERENCES sentences(id) ON DELETE CASCADE,
  tag_id TEXT NOT NULL,
  start_token_index INTEGER NOT NULL,
  end_token_index INTEGER NOT NULL,
  start_char INTEGER NOT NULL,
  end_char INTEGER NOT NULL,
  text TEXT NOT NULL,
  source TEXT NOT NULL DEFAULT 'human',
  source_suggestion_id TEXT,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS annotation_suggestions (
  id TEXT PRIMARY KEY,
  run_id TEXT,
  sentence_id TEXT NOT NULL REFERENCES sentences(id) ON DELETE CASCADE,
  tag_id TEXT NOT NULL,
  start_token_index INTEGER NOT NULL,
  end_token_index INTEGER NOT NULL,
  start_char INTEGER NOT NULL,
  end_char INTEGER NOT NULL,
  text TEXT NOT NULL,
  confidence REAL NOT NULL,
  source TEXT NOT NULL,
  evidence_text TEXT,
  match_key TEXT,
  evidence_match_key TEXT,
  context_before TEXT,
  context_after TEXT,
  status TEXT NOT NULL DEFAULT 'pending',
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS annotation_runs (
  id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL,
  document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  recipe TEXT NOT NULL,
  config_json TEXT NOT NULL,
  input_count INTEGER NOT NULL,
  suggestion_count INTEGER NOT NULL,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS annotation_suggestion_reviews (
  id TEXT PRIMARY KEY,
  suggestion_id TEXT NOT NULL REFERENCES annotation_suggestions(id) ON DELETE CASCADE,
  model TEXT NOT NULL,
  recommendation TEXT NOT NULL,
  confidence REAL NOT NULL,
  rationale TEXT NOT NULL,
  context_sha256 TEXT,
  judge_json TEXT,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS annotation_sessions (
  id TEXT NOT NULL,
  project_id TEXT NOT NULL,
  document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  actor_id TEXT NOT NULL,
  current_sentence_index INTEGER NOT NULL,
  updated_at TEXT NOT NULL,
  PRIMARY KEY (project_id, document_id, id)
);

CREATE TABLE IF NOT EXISTS event_outbox (
  id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL,
  event_json TEXT NOT NULL,
  created_at TEXT NOT NULL,
  flushed_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_sentences_document ON sentences(document_id, sentence_index);
CREATE INDEX IF NOT EXISTS idx_tokens_sentence ON tokens(sentence_id, token_index);
CREATE INDEX IF NOT EXISTS idx_annotations_sentence ON annotations(sentence_id, start_token_index);
CREATE INDEX IF NOT EXISTS idx_suggestions_sentence ON annotation_suggestions(sentence_id, status, start_token_index);
CREATE INDEX IF NOT EXISTS idx_annotation_runs_project ON annotation_runs(project_id, document_id, created_at);
CREATE INDEX IF NOT EXISTS idx_suggestion_reviews ON annotation_suggestion_reviews(suggestion_id, created_at);
CREATE INDEX IF NOT EXISTS idx_annotation_sessions_document ON annotation_sessions(project_id, document_id, updated_at);
CREATE INDEX IF NOT EXISTS idx_event_outbox_pending ON event_outbox(project_id, flushed_at, created_at);
"""

LEGACY_COLUMN_MIGRATIONS = (
    ("tags", "description", "TEXT"),
    ("tags", "examples_json", "TEXT"),
    ("sentences", "answer", "TEXT NOT NULL DEFAULT 'pending'"),
    ("annotations", "source", "TEXT NOT NULL DEFAULT 'human'"),
    ("annotations", "source_suggestion_id", "TEXT"),
    ("annotation_suggestions", "run_id", "TEXT"),
    ("annotation_suggestions", "evidence_text", "TEXT"),
    ("annotation_suggestions", "match_key", "TEXT"),
    ("annotation_suggestions", "evidence_match_key", "TEXT"),
    ("annotation_suggestions", "context_before", "TEXT"),
    ("annotation_suggestions", "context_after", "TEXT"),
    ("annotation_suggestion_reviews", "context_sha256", "TEXT"),
    ("annotation_suggestion_reviews", "judge_json", "TEXT"),
)

_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def create_base_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(BASE_SCHEMA_SQL)
    ensure_legacy_columns(conn)


def ensure_legacy_columns(conn: sqlite3.Connection) -> None:
    for table_name, column_name, column_type in LEGACY_COLUMN_MIGRATIONS:
        ensure_column(conn, table_name, column_name, column_type)


def ensure_column(conn: sqlite3.Connection, table_name: str, column_name: str, column_type: str) -> None:
    _validate_identifier(table_name)
    _validate_identifier(column_name)
    columns = {row[1] for row in conn.execute(f"PRAGMA table_info({table_name})").fetchall()}
    if column_name not in columns:
        conn.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_type}")


def _validate_identifier(identifier: str) -> None:
    if not _IDENTIFIER_RE.match(identifier):
        raise ValueError(f"Invalid SQLite identifier: {identifier}")
