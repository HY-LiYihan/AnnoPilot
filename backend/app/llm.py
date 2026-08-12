from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from typing import Any

from .settings import LlmSettings


class LlmError(Exception):
    pass


class OpenAICompatibleSuggestionReviewer:
    def __init__(self, settings: LlmSettings):
        if not settings.configured:
            raise LlmError("LLM is not configured.")
        self.settings = settings

    def review(self, context: dict[str, Any]) -> dict[str, Any]:
        payload = {
            "model": self.settings.model,
            "temperature": 0,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You review character-match annotation suggestions. "
                        "Return strict JSON only with recommendation, confidence, and rationale. "
                        "recommendation must be one of accept, reject, uncertain. "
                        "Use tag descriptions, examples, existing annotations, and review_guidance when provided. "
                        "Do not invent labels outside the provided tag set."
                    ),
                },
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "task": "Decide whether the suggested span should receive the suggested label.",
                            "context": context,
                            "output_schema": {
                                "recommendation": "accept|reject|uncertain",
                                "confidence": "number from 0 to 1",
                                "rationale": "short Chinese explanation",
                            },
                        },
                        ensure_ascii=False,
                    ),
                },
            ],
        }
        try:
            response_payload = self._post_chat_completions(payload, auth_header="Authorization", auth_value=f"Bearer {self.settings.api_key}")
        except urllib.error.HTTPError as exc:
            if exc.code not in {401, 403}:
                raise LlmError(self._format_http_error(exc)) from exc
            try:
                response_payload = self._post_chat_completions(payload, auth_header="X-Api-Key", auth_value=self.settings.api_key)
            except urllib.error.HTTPError as retry_exc:
                raise LlmError(self._format_http_error(retry_exc)) from retry_exc
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as retry_exc:
                raise LlmError(f"LLM request failed: {retry_exc}") from retry_exc
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise LlmError(f"LLM request failed: {exc}") from exc

        content = _extract_message_content(response_payload)
        return normalize_review_payload(content, model=self.settings.model)

    def _post_chat_completions(self, payload: dict[str, Any], auth_header: str, auth_value: str) -> dict[str, Any]:
        request = urllib.request.Request(
            f"{self.settings.base_url}/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                auth_header: auth_value,
                "Content-Type": "application/json",
                "User-Agent": "AnnoPilot/0.1",
            },
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=self.settings.timeout_seconds) as response:
            return json.loads(response.read().decode("utf-8"))

    def _format_http_error(self, exc: urllib.error.HTTPError) -> str:
        message = f"LLM request failed: HTTP {exc.code} {exc.reason}"
        body = exc.read().decode("utf-8", errors="replace").strip()
        if body:
            body = body.replace(self.settings.api_key, "[redacted]")[:600]
            message = f"{message}; provider response: {body}"
        return message


def normalize_review_payload(content: str, model: str) -> dict[str, Any]:
    try:
        payload = json.loads(content)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", content, flags=re.DOTALL)
        if not match:
            raise LlmError("LLM response did not contain JSON.")
        payload = json.loads(match.group(0))

    recommendation = str(payload.get("recommendation", "uncertain")).strip().lower()
    if recommendation not in {"accept", "reject", "uncertain"}:
        recommendation = "uncertain"
    try:
        confidence = float(payload.get("confidence", 0.5))
    except (TypeError, ValueError):
        confidence = 0.5
    confidence = max(0.0, min(1.0, confidence))
    rationale = str(payload.get("rationale", "")).strip()[:600]
    return {
        "model": model,
        "recommendation": recommendation,
        "confidence": confidence,
        "rationale": rationale,
    }


def _extract_message_content(response_payload: dict[str, Any]) -> str:
    choices = response_payload.get("choices") or []
    if not choices:
        raise LlmError("LLM response did not include choices.")
    message = choices[0].get("message") or {}
    content = message.get("content")
    if not isinstance(content, str) or not content.strip():
        raise LlmError("LLM response did not include message content.")
    return content
