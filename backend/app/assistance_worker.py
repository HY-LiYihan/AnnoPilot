from __future__ import annotations

import asyncio
import json
import queue
import threading
from collections.abc import Callable
from typing import Any

from .assistance_generation import (
    build_assistance_prompt,
    parse_and_verify_assistance_candidate,
    select_assistance_examples,
)
from .hashing import payload_sha256
from .llm import LlmError
from .services.assistance import ASSISTANCE_CONCURRENCY, AssistanceService


ASSISTANCE_PROVIDER_DEADLINE_SECONDS = 300.0


class AssistanceWorker:
    """Single-process durable worker; SQLite remains the source of queue truth."""

    def __init__(
        self,
        service: AssistanceService,
        generator_factory: Callable[[], Any],
        *,
        poll_seconds: float = 0.5,
        provider_deadline_seconds: float = ASSISTANCE_PROVIDER_DEADLINE_SECONDS,
    ) -> None:
        self.service = service
        self.generator_factory = generator_factory
        self.poll_seconds = max(0.1, float(poll_seconds))
        self.provider_deadline_seconds = max(0.01, float(provider_deadline_seconds))
        self._stop = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self._feedback_thread: threading.Thread | None = None
        self._job_threads: set[threading.Thread] = set()
        self._semaphore: asyncio.Semaphore | None = None

    def start(self) -> None:
        if self._task is None or self._task.done():
            self.service.recover_feedback_classifications()
            self._stop.clear()
            self._task = asyncio.create_task(self._run(), name="annopilot-assistance-worker")

    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
            self._task = None
        self._semaphore = None

    async def run_once(self) -> int:
        self.service.ensure_all_queues()
        try:
            self.generator_factory()
        except LlmError:
            return 0
        self._job_threads = {thread for thread in self._job_threads if thread.is_alive()}
        available_slots = max(0, ASSISTANCE_CONCURRENCY - len(self._job_threads))
        job_ids = self.service.claim_jobs(available_slots) if available_slots else []
        if self._semaphore is None:
            self._semaphore = asyncio.Semaphore(ASSISTANCE_CONCURRENCY)
        loop = asyncio.get_running_loop()
        for job_id in job_ids:
            await self._semaphore.acquire()
            thread = threading.Thread(
                target=self._process_job_in_thread,
                args=(job_id, loop),
                name=f"annopilot-assistance-job-{job_id}",
                daemon=True,
            )
            self._job_threads.add(thread)
            try:
                thread.start()
            except Exception:
                self._job_threads.discard(thread)
                self._semaphore.release()
                raise
        self._schedule_feedback_classification()
        return len(job_ids)

    def _schedule_feedback_classification(self) -> None:
        if self._feedback_thread is not None and self._feedback_thread.is_alive():
            return
        self._feedback_thread = None
        feedback = self.service.claim_feedback_for_classification()
        if feedback is not None:
            self._feedback_thread = threading.Thread(
                target=self._classify_feedback,
                args=(feedback,),
                name="annopilot-assistance-feedback",
                daemon=True,
            )
            self._feedback_thread.start()

    def _process_job_in_thread(self, job_id: str, loop: asyncio.AbstractEventLoop) -> None:
        try:
            self._process_job(job_id)
        finally:
            try:
                loop.call_soon_threadsafe(self._release_job_slot)
            except RuntimeError:
                pass

    def _release_job_slot(self) -> None:
        if self._semaphore is not None:
            self._semaphore.release()

    async def wait_for_idle(self, timeout: float = 5.0) -> None:
        deadline = asyncio.get_running_loop().time() + timeout
        while any(thread.is_alive() for thread in self._job_threads):
            if asyncio.get_running_loop().time() >= deadline:
                raise TimeoutError("Assistance worker did not become idle.")
            await asyncio.sleep(0.01)

    async def _run(self) -> None:
        try:
            while not self._stop.is_set():
                try:
                    processed = await self.run_once()
                except Exception:
                    processed = 0
                await asyncio.sleep(0 if processed else self.poll_seconds)
        except asyncio.CancelledError:
            return

    def _process_job(self, job_id: str) -> None:
        attempt_count: int | None = None
        try:
            context = self.service.get_generation_context(job_id)
            attempt_count = int(context["attempt_count"])
            selected_examples = select_assistance_examples(
                context["source_text"],
                context["examples_by_tag"],
                context["corrections_by_tag"],
            )
            generator = self.generator_factory()
            label_to_id = self._label_map(context["tags"])
            validation_issues: list[dict[str, Any]] = []
            candidate: dict[str, Any] = {}
            raw_response = ""
            provider_attempts = 0
            usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
            for _attempt in range(2):
                provider_candidate = self._run_provider_with_deadline(
                    lambda: generator.generate(
                        context["source_text"],
                        context["tags"],
                        selected_examples,
                        context["negative_examples"],
                        validation_issues=validation_issues or None,
                    )
                )
                provider_attempts += 1
                for key in usage:
                    usage[key] += int(dict(getattr(generator, "last_usage", {}) or {}).get(key) or 0)
                raw_response = json.dumps(provider_candidate, ensure_ascii=False)
                candidate, validation_issues = parse_and_verify_assistance_candidate(
                    raw_response,
                    context["source_text"],
                    label_to_id,
                    context["tokens"],
                )
                if not validation_issues:
                    break
            if validation_issues:
                raise ValueError(f"Assistance candidate failed verification: {validation_issues}")
            prompt = build_assistance_prompt(
                context["source_text"],
                context["tags"],
                selected_examples,
                context["negative_examples"],
            )
            self.service.store_generation_result(
                job_id,
                {
                    "candidate": candidate,
                    "issues": validation_issues,
                    "raw_response": raw_response,
                    "model": str(getattr(getattr(generator, "settings", None), "model", getattr(generator, "model", "unknown"))),
                    "prompt_sha256": payload_sha256({"prompt": prompt}),
                    "retrieved_examples": selected_examples,
                    "usage": {
                        **usage,
                        "api_calls": provider_attempts,
                        "validation_attempts": provider_attempts,
                        "validation_retries": max(0, provider_attempts - 1),
                    },
                    "attempt_count": attempt_count,
                },
            )
        except Exception as exc:
            self.service.fail_job(job_id, self._safe_error(exc), expected_attempt_count=attempt_count)

    def _run_provider_with_deadline(self, operation: Callable[[], Any]) -> Any:
        result: queue.Queue[tuple[bool, Any]] = queue.Queue(maxsize=1)

        def invoke() -> None:
            try:
                result.put((True, operation()))
            except Exception as exc:
                result.put((False, exc))

        thread = threading.Thread(
            target=invoke,
            name=f"{threading.current_thread().name}-provider",
            daemon=True,
        )
        thread.start()
        try:
            succeeded, payload = result.get(timeout=self.provider_deadline_seconds)
        except queue.Empty as exc:
            raise TimeoutError(
                f"Assistance provider exceeded the {self.provider_deadline_seconds:.1f}s deadline."
            ) from exc
        if not succeeded:
            raise payload
        return payload

    def _classify_feedback(self, feedback: dict[str, Any]) -> None:
        try:
            generator = self.generator_factory()
            result = generator.classify_error(
                feedback["original_spans"],
                feedback["final_spans"],
                feedback["allowed_reasons"],
            )
            self.service.store_feedback_classification(
                feedback["feedback_id"],
                result.get("reasons") or ["other"],
                str(result.get("note") or ""),
            )
        except Exception:
            self.service.release_feedback_classification(feedback["feedback_id"])

    @staticmethod
    def _label_map(tags: list[dict[str, Any]]) -> dict[str, str]:
        result: dict[str, str] = {}
        for tag in tags:
            tag_id = str(tag["id"])
            name = str(tag.get("name") or tag_id)
            result[tag_id] = tag_id
            result[name] = tag_id
            result[name.casefold()] = tag_id
        return result

    @staticmethod
    def _safe_error(exc: Exception) -> str:
        value = str(exc)
        return value[:1000] if value else exc.__class__.__name__
