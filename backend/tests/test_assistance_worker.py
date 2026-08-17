from __future__ import annotations

import asyncio
import json
import threading
import time
from pathlib import Path
from typing import Any

import pytest

from backend.app.assistance_worker import AssistanceWorker
from backend.app.llm import LlmError
from backend.app.storage import AnnotationStorage


TEXT = "Alice visits Paris."
TOKENS = [
    {"id": "tok-0", "token_index": 0, "text": "Alice", "start_char": 0, "end_char": 5},
    {"id": "tok-1", "token_index": 1, "text": "visits", "start_char": 6, "end_char": 12},
    {"id": "tok-2", "token_index": 2, "text": "Paris", "start_char": 13, "end_char": 18},
    {"id": "tok-3", "token_index": 3, "text": ".", "start_char": 18, "end_char": 19},
]


def _context(job_id: str) -> dict[str, Any]:
    return {
        "job_id": job_id,
        "project_id": "default",
        "document_id": "doc-1",
        "sentence_id": f"sentence-{job_id}",
        "source_text": TEXT,
        "tokens": TOKENS,
        "tags": [{"id": "PER", "name": "Person"}],
        "examples_by_tag": {"PER": []},
        "corrections_by_tag": {"PER": []},
        "negative_examples": [],
        "attempt_count": 1,
    }


class _WorkerService:
    def __init__(self, job_ids: list[str]) -> None:
        self.job_ids = job_ids
        self.ensure_calls = 0
        self.claim_limits: list[int] = []
        self.stored: list[tuple[str, dict[str, Any]]] = []
        self.failed: list[tuple[str, str]] = []
        self.recover_feedback_calls = 0
        self.feedback: dict[str, Any] | None = None

    def ensure_all_queues(self) -> int:
        self.ensure_calls += 1
        return 0

    def claim_jobs(self, limit: int) -> list[str]:
        self.claim_limits.append(limit)
        return self.job_ids[:limit]

    def get_generation_context(self, job_id: str) -> dict[str, Any]:
        return _context(job_id)

    def store_generation_result(self, job_id: str, result: dict[str, Any]) -> None:
        self.stored.append((job_id, result))

    def fail_job(self, job_id: str, message: str, *, expected_attempt_count: int | None = None) -> None:
        assert expected_attempt_count in {None, 1}
        self.failed.append((job_id, message))

    def claim_feedback_for_classification(self) -> dict[str, Any] | None:
        feedback, self.feedback = self.feedback, None
        return feedback

    def recover_feedback_classifications(self) -> int:
        self.recover_feedback_calls += 1
        return 0

    def store_feedback_classification(self, _feedback_id: str, _reasons: list[str], _note: str = "") -> None:
        return None

    def release_feedback_classification(self, _feedback_id: str) -> None:
        return None


class _ConcurrentGenerator:
    model = "test-model"
    last_usage = {"total_tokens": 1}

    def __init__(self, state: dict[str, Any]) -> None:
        self.state = state

    def generate(self, *_args: Any, **_kwargs: Any) -> dict[str, Any]:
        with self.state["lock"]:
            self.state["active"] += 1
            self.state["peak"] = max(self.state["peak"], self.state["active"])
        time.sleep(0.04)
        with self.state["lock"]:
            self.state["active"] -= 1
        return {"text": TEXT, "spans": []}


class _RetryGenerator:
    model = "retry-model"
    last_usage: dict[str, int] = {}

    def __init__(self) -> None:
        self.calls = 0

    def generate(self, *_args: Any, **_kwargs: Any) -> dict[str, Any]:
        self.calls += 1
        if self.calls == 1:
            return {"text": "not the source", "spans": []}
        return {"text": TEXT, "spans": []}


class _EchoGenerator:
    model = "echo-model"
    last_usage: dict[str, int] = {}

    def generate(self, source_text: str, *_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return {"text": source_text, "spans": []}


class _BlockingFeedbackGenerator(_EchoGenerator):
    def __init__(self, started: threading.Event, release: threading.Event) -> None:
        self.started = started
        self.release = release

    def classify_error(self, *_args: Any, **_kwargs: Any) -> dict[str, Any]:
        self.started.set()
        self.release.wait(timeout=2)
        return {"reasons": ["wrong_label"], "note": "classified"}


class _OneSlowGenerator(_EchoGenerator):
    def __init__(self, started: threading.Event, release: threading.Event) -> None:
        self.started = started
        self.release = release

    def generate(self, source_text: str, *_args: Any, **_kwargs: Any) -> dict[str, Any]:
        if "job-slow" in threading.current_thread().name:
            self.started.set()
            self.release.wait(timeout=2)
        return {"text": source_text, "spans": []}


class _QueueWorkerService(_WorkerService):
    def claim_jobs(self, limit: int) -> list[str]:
        self.claim_limits.append(limit)
        claimed = self.job_ids[:limit]
        del self.job_ids[:limit]
        return claimed


def test_worker_processes_at_most_five_jobs_concurrently() -> None:
    service = _WorkerService([f"job-{index}" for index in range(9)])
    state: dict[str, Any] = {"lock": threading.Lock(), "active": 0, "peak": 0}

    async def run() -> int:
        worker = AssistanceWorker(service, lambda: _ConcurrentGenerator(state))  # type: ignore[arg-type]
        processed = await worker.run_once()
        await worker.wait_for_idle()
        return processed

    processed = asyncio.run(run())

    assert processed == 5
    assert service.ensure_calls == 1
    assert service.claim_limits == [5]
    assert state["peak"] == 5
    assert {job_id for job_id, _result in service.stored} == {f"job-{index}" for index in range(5)}
    assert service.failed == []


def test_worker_retries_verifier_failure_once_then_stores_valid_draft() -> None:
    service = _WorkerService(["job-1"])
    generator = _RetryGenerator()

    async def run() -> int:
        worker = AssistanceWorker(service, lambda: generator)  # type: ignore[arg-type]
        processed = await worker.run_once()
        await worker.wait_for_idle()
        return processed

    processed = asyncio.run(run())

    assert processed == 1
    assert generator.calls == 2
    assert service.failed == []
    assert len(service.stored) == 1
    _job_id, result = service.stored[0]
    assert result["candidate"] == {"text": TEXT, "spans": []}
    assert result["issues"] == []


def test_worker_recovers_expired_lease_after_restart(tmp_path: Path) -> None:
    storage, job_id = _storage_with_running_assistance_job(tmp_path)
    with storage.connect() as conn:
        conn.execute("UPDATE assistance_jobs SET lease_until = ? WHERE id = ?", ("2000-01-01T00:00:00+00:00", job_id))

    # A newly constructed worker represents the process that starts after a restart.
    async def run() -> int:
        worker = AssistanceWorker(storage.assistance_service, _EchoGenerator)
        processed = await worker.run_once()
        await worker.wait_for_idle()
        return processed

    processed = asyncio.run(run())

    assert processed == 5
    with storage.connect() as conn:
        job = conn.execute(
            "SELECT status, attempt_count, lease_until FROM assistance_jobs WHERE id = ?", (job_id,)
        ).fetchone()
    assert tuple(job) == ("ready", 2, None)


def test_worker_leaves_jobs_queued_when_llm_is_not_configured() -> None:
    service = _WorkerService(["job-1"])

    def unavailable_generator() -> Any:
        raise LlmError("LLM is not configured.")

    async def run() -> int:
        worker = AssistanceWorker(service, unavailable_generator)  # type: ignore[arg-type]
        return await worker.run_once()

    processed = asyncio.run(run())

    assert processed == 0
    assert service.ensure_calls == 1
    assert service.claim_limits == []
    assert service.stored == []
    assert service.failed == []


def test_feedback_classification_does_not_block_draft_jobs() -> None:
    service = _WorkerService([])
    service.feedback = {
        "feedback_id": "feedback-1",
        "original_spans": [],
        "final_spans": [],
        "allowed_reasons": ["wrong_label"],
    }
    started = threading.Event()
    release = threading.Event()
    generator = _BlockingFeedbackGenerator(started, release)

    async def run() -> int:
        worker = AssistanceWorker(service, lambda: generator)  # type: ignore[arg-type]
        first = await worker.run_once()
        await asyncio.to_thread(started.wait, 1)
        service.job_ids = ["job-1"]
        second = await asyncio.wait_for(worker.run_once(), timeout=1)
        await worker.wait_for_idle()
        release.set()
        await asyncio.sleep(0.05)
        await worker.stop()
        assert first == 0
        return second

    assert asyncio.run(run()) == 1
    assert [job_id for job_id, _result in service.stored] == ["job-1"]


def test_slow_job_does_not_freeze_rolling_slot_refill() -> None:
    service = _QueueWorkerService(["job-slow", "job-1", "job-2", "job-3", "job-4", "job-5", "job-6"])
    started = threading.Event()
    release = threading.Event()
    generator = _OneSlowGenerator(started, release)

    async def wait_until(predicate, timeout: float = 1.0) -> None:
        deadline = asyncio.get_running_loop().time() + timeout
        while not predicate():
            if asyncio.get_running_loop().time() >= deadline:
                raise TimeoutError("Timed out waiting for rolling worker progress.")
            await asyncio.sleep(0.01)

    async def run() -> None:
        worker = AssistanceWorker(service, lambda: generator)  # type: ignore[arg-type]
        assert await worker.run_once() == 5
        await asyncio.to_thread(started.wait, 1)
        await wait_until(lambda: len(service.stored) == 4)
        assert await worker.run_once() == 2
        await wait_until(lambda: len(service.stored) == 6)
        assert "job-slow" not in {job_id for job_id, _result in service.stored}
        release.set()
        await worker.wait_for_idle()

    asyncio.run(run())
    assert {job_id for job_id, _result in service.stored} == {
        "job-slow",
        "job-1",
        "job-2",
        "job-3",
        "job-4",
        "job-5",
        "job-6",
    }


def _storage_with_running_assistance_job(tmp_path: Path) -> tuple[AnnotationStorage, str]:
    storage = AnnotationStorage(tmp_path / "runtime" / "annopilot.sqlite", tmp_path / "projects")
    storage.initialize()
    storage.import_tag_schema(
        "default",
        {
            "schema_version": "annopilot.tag_schema.v1",
            "record_type": "tag_schema",
            "tags": [{"id": "PER", "name": "Person", "shortcut": "1", "color": "#0b7565"}],
        },
    )
    document = storage.import_txt(
        "default",
        "lease-recovery.txt",
        "\n".join(f"Alice visits Paris {index}." for index in range(12)).encode("utf-8"),
    )
    sentences = storage.get_document("default", document["document_id"])["sentences"]
    for sentence in sentences[:5]:
        storage.create_annotation("default", sentence["id"], "PER", 0, 0)
        storage.set_sentence_completed("default", sentence["id"], True, "accept")

    claimed = storage.assistance_service.claim_jobs(1)
    assert len(claimed) == 1
    return storage, claimed[0]
