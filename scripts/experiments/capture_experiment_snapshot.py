#!/usr/bin/env python3
"""Capture a durable AnnoPilot human-annotation experiment snapshot."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen


REPO_ROOT = Path(__file__).resolve().parents[2]
SENSITIVE_MARKERS = ("KEY", "TOKEN", "SECRET", "PASSWORD")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api-base", default=os.getenv("ANNOPILOT_API_BASE", "http://127.0.0.1:8000"))
    parser.add_argument("--project-id", default="default")
    parser.add_argument("--database-path", type=Path, default=Path(os.getenv("DATABASE_PATH", REPO_ROOT / ".runtime" / "annopilot.sqlite")))
    parser.add_argument("--data-root", type=Path, default=Path(os.getenv("DATA_ROOT", REPO_ROOT / ".runtime" / "projects")))
    parser.add_argument("--output-root", type=Path, default=REPO_ROOT / "data" / "experiments")
    args = parser.parse_args()

    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    output_dir = args.output_root / timestamp
    logs_dir = output_dir / "logs"
    runtime_dir = output_dir / "runtime"
    exports_dir = output_dir / "exports"
    for directory in (logs_dir, runtime_dir, exports_dir):
        directory.mkdir(parents=True, exist_ok=True)

    metadata = {
        "captured_at": timestamp,
        "api_base": args.api_base,
        "project_id": args.project_id,
        "git_commit": run_text(["git", "rev-parse", "HEAD"]),
        "git_status": run_text(["git", "status", "--short"]),
        "env": redacted_env(),
        "health": fetch_json(f"{args.api_base.rstrip('/')}/api/health"),
        "documents": fetch_json(f"{args.api_base.rstrip('/')}/api/projects/{args.project_id}/documents"),
    }

    if args.database_path.exists():
        backup_sqlite(args.database_path, runtime_dir / args.database_path.name)
    if args.data_root.exists():
        shutil.copytree(args.data_root, runtime_dir / "projects", dirs_exist_ok=True)

    document_id = first_document_id(metadata.get("documents"))
    if document_id:
        export_url = f"{args.api_base.rstrip('/')}/api/projects/{args.project_id}/documents/{document_id}/export.prodigy.jsonl"
        export_text = fetch_text(export_url)
        if export_text is not None:
            (exports_dir / f"{document_id}.prodigy.jsonl").write_text(export_text, encoding="utf-8")
            metadata["prodigy_export"] = {"document_id": document_id, "line_count": len(export_text.splitlines())}

    (logs_dir / "api.log").touch(exist_ok=True)
    (logs_dir / "web.log").touch(exist_ok=True)
    (output_dir / "metadata.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    print(output_dir)
    return 0


def run_text(command: list[str]) -> str:
    try:
        return subprocess.run(command, cwd=REPO_ROOT, check=False, text=True, capture_output=True).stdout.strip()
    except OSError as exc:
        return f"unavailable: {exc}"


def redacted_env() -> dict[str, str]:
    interesting_prefixes = ("ANNOPILOT", "DATA_ROOT", "DATABASE_PATH", "LLM_", "ASSISTANCE_", "DENSE_", "BM25_", "RRF_")
    values: dict[str, str] = {}
    for key, value in sorted(os.environ.items()):
        if not key.startswith(interesting_prefixes):
            continue
        values[key] = "<redacted>" if any(marker in key.upper() for marker in SENSITIVE_MARKERS) else value
    return values


def fetch_json(url: str) -> object | None:
    text = fetch_text(url)
    if text is None:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {"raw": text[:500]}


def fetch_text(url: str) -> str | None:
    try:
        with urlopen(url, timeout=5) as response:
            return response.read().decode("utf-8")
    except (OSError, URLError):
        return None


def backup_sqlite(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    source_conn = sqlite3.connect(source)
    try:
        dest_conn = sqlite3.connect(destination)
        try:
            source_conn.backup(dest_conn)
        finally:
            dest_conn.close()
    finally:
        source_conn.close()


def first_document_id(payload: object | None) -> str | None:
    if not isinstance(payload, dict):
        return None
    documents = payload.get("documents")
    if not isinstance(documents, list) or not documents:
        return None
    first = documents[0]
    return str(first.get("id")) if isinstance(first, dict) and first.get("id") else None


if __name__ == "__main__":
    raise SystemExit(main())
