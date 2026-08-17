from __future__ import annotations

import asyncio
import json
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


class AssistanceWorker:
    """Single-process durable worker; SQLite remains the source of queue truth."""

    def __init__(
        self,
        service: AssistanceService,
        generator_factory: Callable[[], Any],
        *,
        poll_seconds: float = 0.5,
    ) -> None:
        self.service = service
        self.generator_factory = generator_factory
        self.poll_seconds = max(0.1, float(poll_seconds))
        self._stop = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self._semaphore: asyncio.Semaphore | None = None

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._stop.clear()
            self._task = asyncio.create_task(self._run(), name="annopilot-assistance-worker")

    async def stop(self) -> None:
        self._stop.set()
        if self._task is None:
            return
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
        job_ids = self.service.claim_jobs(ASSISTANCE_CONCURRENCY)
        if job_ids:
            if self._semaphore is None:
                self._semaphore = asyncio.Semaphore(ASSISTANCE_CONCURRENCY)
            await asyncio.gather(*(self._process_job_limited(job_id) for job_id in job_ids))
        feedback = self.service.claim_feedback_for_classification()
        if feedback is not None:
            await asyncio.to_thread(self._classify_feedback, feedback)
        return len(job_ids)

    async def _process_job_limited(self, job_id: str) -> None:
        assert self._semaphore is not None
        async with self._semaphore:
            await asyncio.to_thread(self._process_job, job_id)

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
        try:
            context = self.service.get_generation_context(job_id)
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
            for _attempt in range(2):
                provider_candidate = generator.generate(
                    context["source_text"],
                    context["tags"],
                    selected_examples,
                    context["negative_examples"],
                    validation_issues=validation_issues or None,
                )
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
                    "usage": dict(getattr(generator, "last_usage", {}) or {}),
                },
            )
        except Exception as exc:
            self.service.fail_job(job_id, self._safe_error(exc))

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
