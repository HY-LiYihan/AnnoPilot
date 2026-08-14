from __future__ import annotations

from collections.abc import Callable
from typing import Any


class SuggestionAutomationService:
    """High-level suggestion automation workflows that compose generation and decisions."""

    def __init__(
        self,
        *,
        generate_suggestions: Callable[..., dict[str, Any]],
        auto_accept_document_suggestions: Callable[..., dict[str, Any]],
    ) -> None:
        self.generate_suggestions = generate_suggestions
        self.auto_accept_document_suggestions = auto_accept_document_suggestions

    def auto_annotate_document_suggestions(
        self,
        project_id: str,
        document_id: str,
        limit_per_sentence: int = 6,
        min_confidence: float = 0.9,
        *,
        complete_sentences: bool = False,
    ) -> dict[str, Any]:
        generated = self.generate_suggestions(project_id, document_id, limit_per_sentence, min_confidence)
        accepted = self.auto_accept_document_suggestions(
            project_id,
            document_id,
            min_confidence,
            complete_sentences=complete_sentences,
            completion_source="auto_annotate_suggestions",
        )
        return {
            "run_id": generated["run_id"],
            "suggestions_created": generated["suggestions_created"],
            "source_counts": generated["source_counts"],
            "confidence_counts": generated["confidence_counts"],
            **accepted,
        }
