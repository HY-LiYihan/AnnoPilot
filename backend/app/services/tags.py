from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from typing import Any

from ..hashing import payload_sha256
from ..repositories import TagQueryRepository


class TagService:
    """Tag schema mutations and seed maintenance."""

    def __init__(
        self,
        connect: Callable[[], sqlite3.Connection],
        *,
        new_id: Callable[[str], str],
        now: Callable[[], str],
        enqueue_event: Callable[[sqlite3.Connection, str, dict[str, Any]], dict[str, Any]],
        flush_event_outbox: Callable[[str], int],
        tag_queries: TagQueryRepository,
        default_tags: list[dict[str, Any]],
        legacy_seeded_tags: list[dict[str, Any]],
        tag_colors: list[str],
        tag_schema_version: str,
        not_found_error: type[Exception],
        validation_error: type[Exception],
    ) -> None:
        self.connect = connect
        self.new_id = new_id
        self.now = now
        self.enqueue_event = enqueue_event
        self.flush_event_outbox = flush_event_outbox
        self.tag_queries = tag_queries
        self.default_tags = default_tags
        self.legacy_seeded_tags = legacy_seeded_tags
        self.tag_colors = tag_colors
        self.tag_schema_version = tag_schema_version
        self.not_found_error = not_found_error
        self.validation_error = validation_error

    def get_tags(self, project_id: str) -> list[dict[str, Any]]:
        with self.connect() as conn:
            self.seed_tags(conn, project_id)
            return self.list_tags_from_conn(conn, project_id)

    def list_tags_from_conn(self, conn: sqlite3.Connection, project_id: str) -> list[dict[str, Any]]:
        return self.tag_queries.list_tags(conn, project_id)

    def export_tag_schema(self, project_id: str) -> dict[str, Any]:
        tags = self.get_tags(project_id)
        payload = self._tag_schema_payload(project_id, tags)
        return {
            **payload,
            "generated_at": self.now(),
            "content_sha256": payload_sha256(self._tag_schema_content_payload(tags)),
        }

    def import_tag_schema(self, project_id: str, schema: dict[str, Any]) -> dict[str, Any]:
        incoming_tags = self._validate_tag_schema_import(schema)
        source_hash = schema.get("content_sha256")
        content_hash = payload_sha256(self._tag_schema_content_payload(incoming_tags))
        if source_hash and source_hash != content_hash:
            raise self.validation_error("Tag schema content_sha256 does not match tags payload.")

        created = 0
        updated = 0
        skipped = 0
        with self.connect() as conn:
            self.seed_tags(conn, project_id)
            existing = self.list_tags_from_conn(conn, project_id)
            existing_by_id = {tag["id"]: tag for tag in existing}
            existing_by_name = {tag["name"].casefold(): tag for tag in existing}
            used_shortcuts = {tag["shortcut"] for tag in existing}

            for incoming in incoming_tags:
                target = existing_by_id.get(incoming["id"]) or existing_by_name.get(incoming["name"].casefold())
                if target:
                    name_owner = existing_by_name.get(incoming["name"].casefold())
                    if name_owner and name_owner["id"] != target["id"]:
                        raise self.validation_error(f"Tag name already exists: {incoming['name']}")
                    used_without_current = used_shortcuts - {target["shortcut"]}
                    next_shortcut = self.unique_shortcut(incoming.get("shortcut"), used_without_current)
                    changed = (
                        target["name"] != incoming["name"]
                        or target.get("description") != incoming.get("description")
                        or target.get("examples", []) != incoming.get("examples", [])
                        or target["shortcut"] != next_shortcut
                        or target["color"] != incoming["color"]
                    )
                    if not changed:
                        skipped += 1
                        continue

                    conn.execute(
                        """
                        UPDATE tags
                        SET name = ?, description = ?, examples_json = ?, shortcut = ?, color = ?
                        WHERE project_id = ? AND id = ?
                        """,
                        (
                            incoming["name"],
                            incoming.get("description"),
                            json.dumps(incoming.get("examples", []), ensure_ascii=False),
                            next_shortcut,
                            incoming["color"],
                            project_id,
                            target["id"],
                        ),
                    )
                    self.enqueue_event(
                        conn,
                        project_id,
                        {
                            "type": "tag.updated",
                            "tag_id": target["id"],
                            "old_name": target["name"],
                            "name": incoming["name"],
                            "old_description": target.get("description"),
                            "description": incoming.get("description"),
                            "old_examples": target.get("examples", []),
                            "examples": incoming.get("examples", []),
                            "old_shortcut": target["shortcut"],
                            "shortcut": next_shortcut,
                            "old_color": target["color"],
                            "color": incoming["color"],
                        },
                    )
                    used_shortcuts = used_without_current | {next_shortcut}
                    updated += 1
                else:
                    next_shortcut = self.unique_shortcut(incoming.get("shortcut"), used_shortcuts)
                    conn.execute(
                        """
                        INSERT INTO tags (id, project_id, name, description, examples_json, shortcut, color)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            incoming["id"],
                            project_id,
                            incoming["name"],
                            incoming.get("description"),
                            json.dumps(incoming.get("examples", []), ensure_ascii=False),
                            next_shortcut,
                            incoming["color"],
                        ),
                    )
                    self.enqueue_event(
                        conn,
                        project_id,
                        {
                            "type": "tag.created",
                            "tag_id": incoming["id"],
                            "name": incoming["name"],
                            "description": incoming.get("description"),
                            "examples": incoming.get("examples", []),
                            "shortcut": next_shortcut,
                            "color": incoming["color"],
                        },
                    )
                    used_shortcuts.add(next_shortcut)
                    created += 1

                existing = self.list_tags_from_conn(conn, project_id)
                existing_by_id = {tag["id"]: tag for tag in existing}
                existing_by_name = {tag["name"].casefold(): tag for tag in existing}

            tags = self.list_tags_from_conn(conn, project_id)

        self.flush_event_outbox(project_id)
        return {"created": created, "updated": updated, "skipped": skipped, "content_sha256": content_hash, "tags": tags}

    def create_tag(
        self,
        project_id: str,
        name: str,
        description: str | None = None,
        examples: list[str] | None = None,
    ) -> dict[str, Any]:
        tag_name = name.strip()
        if not tag_name:
            raise self.validation_error("Tag name is required.")
        tag_description = self.normalize_optional_text(description)
        tag_examples = self.normalize_examples(examples)

        tag_id = self.new_id("tag")
        with self.connect() as conn:
            self.seed_tags(conn, project_id)
            existing = self.list_tags_from_conn(conn, project_id)
            if any(tag["name"].casefold() == tag_name.casefold() for tag in existing):
                raise self.validation_error("Tag name already exists.")
            shortcut = self.next_tag_shortcut(existing)
            color = self.tag_colors[len(existing) % len(self.tag_colors)]
            conn.execute(
                """
                INSERT INTO tags (id, project_id, name, description, examples_json, shortcut, color)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (tag_id, project_id, tag_name, tag_description, json.dumps(tag_examples, ensure_ascii=False), shortcut, color),
            )
            self.enqueue_event(
                conn,
                project_id,
                {
                    "type": "tag.created",
                    "tag_id": tag_id,
                    "name": tag_name,
                    "description": tag_description,
                    "examples": tag_examples,
                    "shortcut": shortcut,
                    "color": color,
                },
            )

        self.flush_event_outbox(project_id)
        return {
            "id": tag_id,
            "name": tag_name,
            "description": tag_description,
            "examples": tag_examples,
            "shortcut": shortcut,
            "color": color,
            "count": 0,
            "usage_count": 0,
            "suggestion_count": 0,
        }

    def rename_tag(
        self,
        project_id: str,
        tag_id: str,
        name: str | None,
        description: str | None = None,
        examples: list[str] | None = None,
    ) -> dict[str, Any]:
        tag_name = name.strip() if name is not None else None
        tag_description = self.normalize_optional_text(description)
        tag_examples = self.normalize_examples(examples) if examples is not None else None
        if tag_name is None and description is None and examples is None:
            raise self.validation_error("Tag name, description, or examples are required.")
        if tag_name is not None and not tag_name:
            raise self.validation_error("Tag name is required.")

        with self.connect() as conn:
            self.seed_tags(conn, project_id)
            tag = conn.execute(
                "SELECT id, name, description, examples_json FROM tags WHERE project_id = ? AND id = ?",
                (project_id, tag_id),
            ).fetchone()
            if tag is None:
                raise self.not_found_error("Tag not found.")

            existing = self.list_tags_from_conn(conn, project_id)
            if tag_name is not None and any(
                existing_tag["id"] != tag_id and existing_tag["name"].casefold() == tag_name.casefold() for existing_tag in existing
            ):
                raise self.validation_error("Tag name already exists.")

            next_name = tag_name if tag_name is not None else tag["name"]
            next_description = tag_description if description is not None else tag["description"]
            current_examples = self.parse_examples_json(tag["examples_json"])
            next_examples = tag_examples if examples is not None else current_examples
            if next_name == tag["name"] and next_description == tag["description"] and next_examples == current_examples:
                return next(tag_item for tag_item in existing if tag_item["id"] == tag_id)

            conn.execute(
                "UPDATE tags SET name = ?, description = ?, examples_json = ? WHERE project_id = ? AND id = ?",
                (next_name, next_description, json.dumps(next_examples, ensure_ascii=False), project_id, tag_id),
            )
            self.enqueue_event(
                conn,
                project_id,
                {
                    "type": "tag.updated",
                    "tag_id": tag_id,
                    "old_name": tag["name"],
                    "name": next_name,
                    "old_description": tag["description"],
                    "description": next_description,
                    "old_examples": current_examples,
                    "examples": next_examples,
                },
            )
            updated_tag = next(tag_item for tag_item in self.list_tags_from_conn(conn, project_id) if tag_item["id"] == tag_id)

        self.flush_event_outbox(project_id)
        return updated_tag

    def delete_tag(self, project_id: str, tag_id: str) -> dict[str, Any]:
        with self.connect() as conn:
            tag = conn.execute(
                "SELECT id, name FROM tags WHERE project_id = ? AND id = ?",
                (project_id, tag_id),
            ).fetchone()
            if tag is None:
                raise self.not_found_error("Tag not found.")

            annotation_count = conn.execute(
                """
                SELECT COUNT(*) AS count
                FROM annotations a
                JOIN sentences s ON s.id = a.sentence_id
                JOIN documents d ON d.id = s.document_id
                WHERE d.project_id = ? AND a.tag_id = ?
                """,
                (project_id, tag_id),
            ).fetchone()["count"]

            suggestion_count = conn.execute(
                """
                SELECT COUNT(*) AS count
                FROM annotation_suggestions sg
                JOIN sentences s ON s.id = sg.sentence_id
                JOIN documents d ON d.id = s.document_id
                WHERE d.project_id = ? AND sg.tag_id = ?
                """,
                (project_id, tag_id),
            ).fetchone()["count"]

            conn.execute(
                """
                DELETE FROM annotations
                WHERE tag_id = ?
                  AND sentence_id IN (
                    SELECT s.id
                    FROM sentences s
                    JOIN documents d ON d.id = s.document_id
                    WHERE d.project_id = ?
                  )
                """,
                (tag_id, project_id),
            )
            conn.execute(
                """
                DELETE FROM annotation_suggestions
                WHERE tag_id = ?
                  AND sentence_id IN (
                    SELECT s.id
                    FROM sentences s
                    JOIN documents d ON d.id = s.document_id
                    WHERE d.project_id = ?
                  )
                """,
                (tag_id, project_id),
            )
            conn.execute("DELETE FROM tags WHERE project_id = ? AND id = ?", (project_id, tag_id))
            self.enqueue_event(
                conn,
                project_id,
                {
                    "type": "tag.deleted",
                    "tag_id": tag_id,
                    "name": tag["name"],
                    "annotation_count": annotation_count,
                    "suggestion_count": suggestion_count,
                },
            )

        self.flush_event_outbox(project_id)
        return {"deleted": True, "tag_id": tag_id, "annotation_count": annotation_count, "suggestion_count": suggestion_count}

    def seed_tags(self, conn: sqlite3.Connection, project_id: str) -> None:
        self.remove_legacy_seeded_tags(conn, project_id)
        existing_count = conn.execute("SELECT COUNT(*) AS count FROM tags WHERE project_id = ?", (project_id,)).fetchone()[
            "count"
        ]
        if existing_count:
            return
        conn.executemany(
            """
            INSERT INTO tags (id, project_id, name, description, examples_json, shortcut, color)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(project_id, id) DO UPDATE SET
              name = excluded.name,
              description = excluded.description,
              examples_json = excluded.examples_json,
              shortcut = excluded.shortcut,
              color = excluded.color
            """,
            [
                (
                    tag["id"],
                    project_id,
                    tag["name"],
                    tag["description"],
                    json.dumps(tag["examples"], ensure_ascii=False),
                    tag["shortcut"],
                    tag["color"],
                )
                for tag in self.default_tags
            ],
        )

    def remove_legacy_seeded_tags(self, conn: sqlite3.Connection, project_id: str) -> None:
        removed_legacy_tag = False
        for tag in self.legacy_seeded_tags:
            legacy_tag = conn.execute(
                "SELECT 1 FROM tags WHERE project_id = ? AND id = ? AND name = ?",
                (project_id, tag["id"], tag["name"]),
            ).fetchone()
            if legacy_tag is None:
                continue
            conn.execute(
                """
                DELETE FROM annotations
                WHERE tag_id = ?
                  AND sentence_id IN (
                    SELECT s.id
                    FROM sentences s
                    JOIN documents d ON d.id = s.document_id
                    WHERE d.project_id = ?
                  )
                """,
                (tag["id"], project_id),
            )
            conn.execute(
                """
                DELETE FROM annotation_suggestions
                WHERE tag_id = ?
                  AND sentence_id IN (
                    SELECT s.id
                    FROM sentences s
                    JOIN documents d ON d.id = s.document_id
                    WHERE d.project_id = ?
                  )
                """,
                (tag["id"], project_id),
            )
            conn.execute(
                """
                DELETE FROM tags
                WHERE project_id = ?
                  AND id = ?
                  AND name = ?
                """,
                (project_id, tag["id"], tag["name"]),
            )
            removed_legacy_tag = True
        if removed_legacy_tag:
            self.compact_tag_shortcuts_and_colors(conn, project_id)

    def compact_tag_shortcuts_and_colors(self, conn: sqlite3.Connection, project_id: str) -> None:
        rows = conn.execute(
            """
            SELECT id, shortcut, name
            FROM tags
            WHERE project_id = ?
            ORDER BY
              CASE WHEN shortcut GLOB '[0-9]*' THEN CAST(shortcut AS INTEGER) ELSE 10000 END,
              name,
              id
            """,
            (project_id,),
        ).fetchall()
        for index, row in enumerate(rows):
            conn.execute(
                "UPDATE tags SET shortcut = ?, color = ? WHERE project_id = ? AND id = ?",
                (str(index + 1), self.tag_colors[index % len(self.tag_colors)], project_id, row["id"]),
            )

    def backfill_default_tag_descriptions(self, conn: sqlite3.Connection) -> None:
        conn.executemany(
            """
            UPDATE tags
            SET description = ?
            WHERE id = ? AND (description IS NULL OR TRIM(description) = '')
            """,
            [(tag["description"], tag["id"]) for tag in self.default_tags],
        )

    def backfill_default_tag_examples(self, conn: sqlite3.Connection) -> None:
        conn.executemany(
            """
            UPDATE tags
            SET examples_json = ?
            WHERE id = ? AND (examples_json IS NULL OR TRIM(examples_json) = '')
            """,
            [(json.dumps(tag["examples"], ensure_ascii=False), tag["id"]) for tag in self.default_tags],
        )

    @staticmethod
    def next_tag_shortcut(tags: list[dict[str, Any]]) -> str:
        used = {tag["shortcut"] for tag in tags}
        next_number = 1
        while str(next_number) in used:
            next_number += 1
        return str(next_number)

    @staticmethod
    def unique_shortcut(preferred: str | None, used: set[str]) -> str:
        normalized = str(preferred).strip() if preferred is not None else ""
        if normalized and normalized not in used:
            return normalized
        next_number = 1
        while str(next_number) in used:
            next_number += 1
        return str(next_number)

    @staticmethod
    def normalize_optional_text(value: str | None) -> str | None:
        if value is None:
            return None
        normalized = str(value).strip()
        return normalized or None

    @staticmethod
    def normalize_examples(values: list[str] | None) -> list[str]:
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

    @classmethod
    def parse_examples_json(cls, value: str | None) -> list[str]:
        if not value:
            return []
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return []
        if not isinstance(parsed, list):
            return []
        return cls.normalize_examples([str(item) for item in parsed])

    def _validate_tag_schema_import(self, schema: dict[str, Any]) -> list[dict[str, Any]]:
        if schema.get("schema_version") != self.tag_schema_version or schema.get("record_type") != "tag_schema":
            raise self.validation_error("Tag schema must be annopilot.tag_schema.v1.")
        raw_tags = schema.get("tags")
        if not isinstance(raw_tags, list) or not raw_tags:
            raise self.validation_error("Tag schema must include at least one tag.")

        tags: list[dict[str, Any]] = []
        seen_ids: set[str] = set()
        seen_names: set[str] = set()
        for index, raw_tag in enumerate(raw_tags):
            if not isinstance(raw_tag, dict):
                raise self.validation_error(f"Tag schema item {index + 1} must be an object.")
            tag_id = str(raw_tag.get("id", "")).strip()
            name = str(raw_tag.get("name", "")).strip()
            if not tag_id or not name:
                raise self.validation_error(f"Tag schema item {index + 1} must include id and name.")
            if tag_id in seen_ids:
                raise self.validation_error(f"Duplicate tag id in schema: {tag_id}")
            name_key = name.casefold()
            if name_key in seen_names:
                raise self.validation_error(f"Duplicate tag name in schema: {name}")
            seen_ids.add(tag_id)
            seen_names.add(name_key)
            color = str(raw_tag.get("color") or self.tag_colors[len(tags) % len(self.tag_colors)]).strip()
            tags.append(
                {
                    "id": tag_id,
                    "name": name,
                    "description": self.normalize_optional_text(raw_tag.get("description")),
                    "examples": self.normalize_examples(raw_tag.get("examples") if isinstance(raw_tag.get("examples"), list) else []),
                    "shortcut": str(raw_tag.get("shortcut") or index + 1).strip(),
                    "color": color or self.tag_colors[len(tags) % len(self.tag_colors)],
                }
            )
        return tags

    def _tag_schema_payload(self, project_id: str, tags: list[dict[str, Any]]) -> dict[str, Any]:
        return {**self._tag_schema_content_payload(tags), "project_id": project_id}

    def _tag_schema_content_payload(self, tags: list[dict[str, Any]]) -> dict[str, Any]:
        schema_tags = [
            {
                "id": tag["id"],
                "name": tag["name"],
                "description": tag.get("description"),
                "examples": tag.get("examples", []),
                "shortcut": tag["shortcut"],
                "color": tag["color"],
            }
            for tag in tags
        ]
        return {
            "schema_version": self.tag_schema_version,
            "record_type": "tag_schema",
            "tag_count": len(schema_tags),
            "retrieval": "character_rag_lexical_examples",
            "tags": schema_tags,
        }
