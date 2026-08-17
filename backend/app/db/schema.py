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
  taxonomy_json TEXT,
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
  snapshot_complete INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS annotation_run_sentences (
  run_id TEXT NOT NULL REFERENCES annotation_runs(id) ON DELETE CASCADE,
  sentence_id TEXT NOT NULL REFERENCES sentences(id) ON DELETE CASCADE,
  PRIMARY KEY (run_id, sentence_id)
);

CREATE TABLE IF NOT EXISTS annotation_run_candidate_spans (
  id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL REFERENCES annotation_runs(id) ON DELETE CASCADE,
  sentence_id TEXT NOT NULL REFERENCES sentences(id) ON DELETE CASCADE,
  tag_id TEXT NOT NULL,
  tag_name TEXT NOT NULL,
  start_token_index INTEGER NOT NULL,
  end_token_index INTEGER NOT NULL,
  start_char INTEGER NOT NULL,
  end_char INTEGER NOT NULL,
  text TEXT NOT NULL,
  confidence REAL NOT NULL,
  source TEXT NOT NULL,
  evidence_text TEXT,
  created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_annotation_run_sentences_sentence
  ON annotation_run_sentences(sentence_id, run_id);

CREATE INDEX IF NOT EXISTS idx_annotation_run_candidate_spans_run_sentence
  ON annotation_run_candidate_spans(run_id, sentence_id, start_token_index);

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
    ("tags", "taxonomy_json", "TEXT"),
    ("sentences", "answer", "TEXT NOT NULL DEFAULT 'pending'"),
    ("annotations", "source", "TEXT NOT NULL DEFAULT 'human'"),
    ("annotations", "source_suggestion_id", "TEXT"),
    ("annotation_suggestions", "run_id", "TEXT"),
    ("annotation_suggestions", "evidence_text", "TEXT"),
    ("annotation_suggestions", "match_key", "TEXT"),
    ("annotation_suggestions", "evidence_match_key", "TEXT"),
    ("annotation_suggestions", "context_before", "TEXT"),
    ("annotation_suggestions", "context_after", "TEXT"),
    ("annotation_suggestions", "candidate_group_id", "TEXT"),
    ("annotation_suggestion_reviews", "context_sha256", "TEXT"),
    ("annotation_suggestion_reviews", "judge_json", "TEXT"),
)

_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def create_base_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(BASE_SCHEMA_SQL)
    ensure_legacy_columns(conn)


def create_run_candidate_snapshot_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS annotation_runs (
          id TEXT PRIMARY KEY,
          project_id TEXT NOT NULL,
          document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
          recipe TEXT NOT NULL,
          config_json TEXT NOT NULL,
          input_count INTEGER NOT NULL,
          suggestion_count INTEGER NOT NULL,
          snapshot_complete INTEGER NOT NULL DEFAULT 0,
          created_at TEXT NOT NULL
        )
        """
    )
    ensure_column(conn, "annotation_runs", "snapshot_complete", "INTEGER NOT NULL DEFAULT 0")
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS annotation_run_sentences (
          run_id TEXT NOT NULL REFERENCES annotation_runs(id) ON DELETE CASCADE,
          sentence_id TEXT NOT NULL REFERENCES sentences(id) ON DELETE CASCADE,
          PRIMARY KEY (run_id, sentence_id)
        );

        CREATE TABLE IF NOT EXISTS annotation_run_candidate_spans (
          id TEXT PRIMARY KEY,
          run_id TEXT NOT NULL REFERENCES annotation_runs(id) ON DELETE CASCADE,
          sentence_id TEXT NOT NULL REFERENCES sentences(id) ON DELETE CASCADE,
          tag_id TEXT NOT NULL,
          tag_name TEXT NOT NULL,
          start_token_index INTEGER NOT NULL,
          end_token_index INTEGER NOT NULL,
          start_char INTEGER NOT NULL,
          end_char INTEGER NOT NULL,
          text TEXT NOT NULL,
          confidence REAL NOT NULL,
          source TEXT NOT NULL,
          evidence_text TEXT,
          created_at TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_annotation_run_sentences_sentence
          ON annotation_run_sentences(sentence_id, run_id);

        CREATE INDEX IF NOT EXISTS idx_annotation_run_candidate_spans_run_sentence
          ON annotation_run_candidate_spans(run_id, sentence_id, start_token_index);
        """
    )


def create_tag_taxonomy_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS tags (
          id TEXT NOT NULL,
          project_id TEXT NOT NULL,
          name TEXT NOT NULL,
          description TEXT,
          examples_json TEXT NOT NULL DEFAULT '[]',
          taxonomy_json TEXT,
          shortcut TEXT NOT NULL,
          color TEXT NOT NULL,
          PRIMARY KEY (project_id, id)
        )
        """
    )
    ensure_column(conn, "tags", "taxonomy_json", "TEXT")


def create_engagement_candidate_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS annotation_candidate_groups (
          id TEXT PRIMARY KEY,
          run_id TEXT NOT NULL REFERENCES annotation_runs(id) ON DELETE CASCADE,
          sentence_id TEXT NOT NULL REFERENCES sentences(id) ON DELETE CASCADE,
          candidate_index INTEGER NOT NULL,
          model TEXT NOT NULL,
          temperature REAL NOT NULL,
          prompt_sha256 TEXT NOT NULL,
          source_text TEXT NOT NULL,
          raw_response TEXT NOT NULL,
          explanation TEXT NOT NULL DEFAULT '',
          spans_json TEXT NOT NULL DEFAULT '[]',
          verifier_status TEXT NOT NULL,
          verifier_issues_json TEXT NOT NULL DEFAULT '[]',
          consistency_json TEXT NOT NULL DEFAULT '{}',
          created_at TEXT NOT NULL,
          UNIQUE (run_id, sentence_id, candidate_index)
        )
        """
    )
    if _table_exists(conn, "annotation_suggestions"):
        ensure_column(conn, "annotation_suggestions", "candidate_group_id", "TEXT")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_annotation_suggestions_candidate_group ON annotation_suggestions(candidate_group_id)"
        )
    if _table_exists(conn, "annotation_run_candidate_spans"):
        ensure_column(conn, "annotation_run_candidate_spans", "candidate_group_id", "TEXT")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_annotation_run_candidate_spans_group ON annotation_run_candidate_spans(candidate_group_id)"
        )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_annotation_candidate_groups_sentence ON annotation_candidate_groups(sentence_id, created_at, candidate_index)"
    )


def create_assistance_workflow_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS assistance_settings (
          project_id TEXT NOT NULL,
          document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
          enabled INTEGER NOT NULL DEFAULT 1,
          knowledge_revision INTEGER NOT NULL DEFAULT 0,
          queue_sequence INTEGER NOT NULL DEFAULT 0,
          updated_at TEXT NOT NULL,
          PRIMARY KEY (project_id, document_id)
        );

        CREATE TABLE IF NOT EXISTS assistance_jobs (
          id TEXT PRIMARY KEY,
          project_id TEXT NOT NULL,
          document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
          sentence_id TEXT NOT NULL REFERENCES sentences(id) ON DELETE CASCADE,
          run_id TEXT REFERENCES annotation_runs(id) ON DELETE SET NULL,
          status TEXT NOT NULL,
          queue_order INTEGER NOT NULL,
          knowledge_revision INTEGER NOT NULL,
          draft_version INTEGER NOT NULL DEFAULT 1,
          active_tag_ids_json TEXT NOT NULL,
          tag_schema_sha256 TEXT NOT NULL,
          retrieved_examples_json TEXT NOT NULL DEFAULT '{}',
          prompt_sha256 TEXT,
          model TEXT,
          raw_response TEXT,
          result_json TEXT,
          verifier_status TEXT,
          verifier_issues_json TEXT NOT NULL DEFAULT '[]',
          attempt_count INTEGER NOT NULL DEFAULT 0,
          usage_json TEXT NOT NULL DEFAULT '{}',
          lease_until TEXT,
          error_message TEXT,
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL,
          UNIQUE (project_id, document_id, sentence_id)
        );

        CREATE TABLE IF NOT EXISTS assistance_feedback (
          id TEXT PRIMARY KEY,
          job_id TEXT NOT NULL UNIQUE REFERENCES assistance_jobs(id) ON DELETE CASCADE,
          project_id TEXT NOT NULL,
          document_id TEXT NOT NULL,
          sentence_id TEXT NOT NULL,
          action TEXT NOT NULL,
          original_spans_json TEXT NOT NULL DEFAULT '[]',
          final_spans_json TEXT NOT NULL DEFAULT '[]',
          error_reasons_json TEXT NOT NULL DEFAULT '[]',
          reason_source TEXT,
          error_note TEXT,
          created_at TEXT NOT NULL,
          classified_at TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_assistance_jobs_queue
          ON assistance_jobs(project_id, document_id, status, queue_order);
        CREATE INDEX IF NOT EXISTS idx_assistance_jobs_lease
          ON assistance_jobs(status, lease_until, queue_order);
        CREATE INDEX IF NOT EXISTS idx_assistance_feedback_document
          ON assistance_feedback(project_id, document_id, created_at);
        """
    )
    if _table_exists(conn, "annotation_suggestions"):
        ensure_column(conn, "annotation_suggestions", "assistance_job_id", "TEXT")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_suggestions_assistance_job ON annotation_suggestions(assistance_job_id, status)"
        )


def ensure_legacy_columns(conn: sqlite3.Connection) -> None:
    for table_name, column_name, column_type in LEGACY_COLUMN_MIGRATIONS:
        ensure_column(conn, table_name, column_name, column_type)


def ensure_column(conn: sqlite3.Connection, table_name: str, column_name: str, column_type: str) -> None:
    _validate_identifier(table_name)
    _validate_identifier(column_name)
    columns = {row[1] for row in conn.execute(f"PRAGMA table_info({table_name})").fetchall()}
    if column_name not in columns:
        conn.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_type}")


def _table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table_name,),
    ).fetchone()
    return row is not None


def _validate_identifier(identifier: str) -> None:
    if not _IDENTIFIER_RE.match(identifier):
        raise ValueError(f"Invalid SQLite identifier: {identifier}")
