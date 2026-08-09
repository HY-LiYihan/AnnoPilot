from __future__ import annotations

from fastapi import Request

from ..llm import OpenAICompatibleSuggestionReviewer
from ..settings import get_llm_settings
from ..storage import AnnotationStorage


def get_storage(request: Request) -> AnnotationStorage:
    return request.app.state.storage


def get_suggestion_reviewer(request: Request):
    reviewer = getattr(request.app.state, "suggestion_reviewer", None)
    if reviewer is not None:
        return reviewer
    return OpenAICompatibleSuggestionReviewer(get_llm_settings())
