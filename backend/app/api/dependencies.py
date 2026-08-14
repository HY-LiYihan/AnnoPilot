from __future__ import annotations

from fastapi import Request

from ..llm import OpenAICompatibleEngagementCandidateGenerator, OpenAICompatibleSuggestionReviewer
from ..settings import get_llm_model_option, get_llm_settings
from ..storage import AnnotationStorage


LLM_MODEL_SETTING_KEY = "llm_model_option_id"


def get_storage(request: Request) -> AnnotationStorage:
    return request.app.state.storage


def get_selected_llm_model_option_id(request: Request) -> str | None:
    storage = get_storage(request)
    return storage.get_runtime_setting(LLM_MODEL_SETTING_KEY)


def get_effective_llm_settings(request: Request):
    selected_option_id = get_selected_llm_model_option_id(request)
    option = get_llm_model_option(selected_option_id) if selected_option_id else None
    return get_llm_settings(model_override=option.model if option else None)


def get_suggestion_reviewer(request: Request):
    reviewer = getattr(request.app.state, "suggestion_reviewer", None)
    if reviewer is not None:
        return reviewer
    return OpenAICompatibleSuggestionReviewer(get_effective_llm_settings(request))


def get_engagement_candidate_generator(request: Request):
    generator = getattr(request.app.state, "engagement_candidate_generator", None)
    if generator is not None:
        return generator
    from ..llm import OpenAICompatibleEngagementCandidateGenerator

    return OpenAICompatibleEngagementCandidateGenerator(get_effective_llm_settings(request))
