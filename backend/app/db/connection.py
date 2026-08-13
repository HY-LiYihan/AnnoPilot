from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any


JUDGE_ERROR_RISK_WEIGHTS = {
    "format_error": 0.9,
    "text_mismatch": 0.85,
    "invalid_label": 0.9,
    "missed_span": 0.9,
    "extra_span": 0.85,
    "boundary_too_wide": 0.75,
    "boundary_too_narrow": 0.75,
    "wrong_label": 0.9,
    "uncertain": 0.6,
}

JUDGE_FLAG_RISK_WEIGHTS = {
    "borderline_concept": 0.25,
    "reference_conflict": 0.45,
    "possible_over_annotation": 0.45,
    "possible_under_annotation": 0.45,
    "format_repair_needed": 0.65,
    "low_evidence": 0.4,
    "hard_example": 0.7,
}


def connect_database(database_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(database_path)
    conn.row_factory = sqlite3.Row
    configure_connection(conn)
    return conn


def configure_connection(conn: sqlite3.Connection, *, enable_wal: bool = False) -> None:
    if enable_wal:
        conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        conn.create_function("annopilot_judge_review_risk", 1, judge_review_risk_score, deterministic=True)
    except TypeError:
        conn.create_function("annopilot_judge_review_risk", 1, judge_review_risk_score)


def judge_review_risk_score(judge_json: Any) -> float:
    if not judge_json:
        return 0.0
    if isinstance(judge_json, bytes):
        judge_json = judge_json.decode("utf-8", errors="replace")
    try:
        judge = json.loads(str(judge_json)) if isinstance(judge_json, str) else judge_json
    except (TypeError, ValueError, json.JSONDecodeError):
        return 0.0
    if not isinstance(judge, dict):
        return 0.0

    risk = 0.0
    risk = max(risk, _score_deficit(judge.get("overall_score")))
    risk = max(risk, _score_deficit(judge.get("boundary_score")))
    risk = max(risk, _bounded_float(judge.get("missed_span_risk")))
    risk = max(risk, _bounded_float(judge.get("extra_span_risk")))
    if judge.get("needs_review") is True:
        risk = max(risk, 0.6)
    risk = max(risk, _choice_list_risk(judge.get("error_types"), JUDGE_ERROR_RISK_WEIGHTS))
    risk = max(risk, _choice_list_risk(judge.get("risk_flags"), JUDGE_FLAG_RISK_WEIGHTS))
    return round(max(0.0, min(1.0, risk)), 4)


def _score_deficit(value: Any) -> float:
    if value is None:
        return 0.0
    return 1.0 - _bounded_float(value, default=1.0)


def _bounded_float(value: Any, *, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = default
    return max(0.0, min(1.0, number))


def _choice_list_risk(value: Any, weights: dict[str, float]) -> float:
    if isinstance(value, str):
        choices = [value]
    elif isinstance(value, list):
        choices = value
    else:
        choices = []
    return max((weights.get(str(choice), 0.0) for choice in choices), default=0.0)
