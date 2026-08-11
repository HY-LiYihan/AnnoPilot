from __future__ import annotations

import json
import sqlite3
from typing import Any


class TagQueryRepository:
    """Read-only tag queries and tag read-model shaping."""

    def __init__(self, *, default_tags: list[dict[str, Any]] | None = None) -> None:
        self.default_tags = default_tags or []

    def list_tags(self, conn: sqlite3.Connection, project_id: str) -> list[dict[str, Any]]:
        rows = conn.execute(
            """
            SELECT
              tags.id,
              tags.name,
              tags.description,
              tags.examples_json,
              tags.shortcut,
              tags.color,
              COUNT(DISTINCT a.id) AS usage_count,
              COUNT(DISTINCT sg.id) AS suggestion_count
            FROM tags
            LEFT JOIN documents d ON d.project_id = tags.project_id
            LEFT JOIN sentences s ON s.document_id = d.id
            LEFT JOIN annotations a ON a.sentence_id = s.id AND a.tag_id = tags.id
            LEFT JOIN annotation_suggestions sg ON sg.sentence_id = s.id AND sg.tag_id = tags.id
            WHERE tags.project_id = ?
            GROUP BY tags.id, tags.name, tags.description, tags.examples_json, tags.shortcut, tags.color
            """,
            (project_id,),
        ).fetchall()
        sort_order = {tag["id"]: index for index, tag in enumerate(self.default_tags)}
        tags = []
        for row in sorted(rows, key=lambda row: (sort_order.get(row["id"], len(sort_order)), self._shortcut_order(row["shortcut"]), row["name"])):
            tag = self._row_dict(row, exclude={"examples_json"})
            tag["examples"] = self._parse_examples_json(row["examples_json"])
            tag["count"] = row["usage_count"]
            tags.append(tag)
        return tags

    @classmethod
    def _parse_examples_json(cls, value: str | None) -> list[str]:
        if not value:
            return []
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return []
        if not isinstance(parsed, list):
            return []
        return cls._normalize_examples([str(item) for item in parsed])

    @staticmethod
    def _normalize_examples(values: list[str] | None) -> list[str]:
        if not values:
            return []
        normalized: list[str] = []
        seen: set[str] = set()
        for value in values:
            text = str(value).strip()
            if not text or text in seen:
                continue
            seen.add(text)
            normalized.append(text)
        return normalized[:80]

    @staticmethod
    def _shortcut_order(shortcut: str) -> int:
        return int(shortcut) if shortcut.isdigit() else 10_000

    @staticmethod
    def _row_dict(row: sqlite3.Row, exclude: set[str] | None = None) -> dict[str, Any]:
        excluded = exclude or set()
        return {key: row[key] for key in row.keys() if key not in excluded}
