from __future__ import annotations

from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException, Request

from ..schemas import LlmSettingsResponse, UpdateLlmSettingsRequest
from ..settings import get_llm_model_option, list_llm_model_options, selected_llm_model_option_id
from ..storage import AnnotationStorage
from .dependencies import LLM_MODEL_SETTING_KEY, get_effective_llm_settings, get_selected_llm_model_option_id, get_storage


router = APIRouter(prefix="/api/settings", tags=["settings"])


@router.get("/llm", response_model=LlmSettingsResponse)
def get_llm_settings_payload(request: Request) -> LlmSettingsResponse:
    return _settings_response(request)


@router.post("/llm", response_model=LlmSettingsResponse)
def update_llm_settings_payload(
    request: Request,
    payload: UpdateLlmSettingsRequest,
    storage: AnnotationStorage = Depends(get_storage),
) -> LlmSettingsResponse:
    option = get_llm_model_option(payload.model_option_id)
    if option is None:
        raise HTTPException(status_code=400, detail="Unknown LLM model option.")
    storage.set_runtime_setting(LLM_MODEL_SETTING_KEY, option.id)
    return _settings_response(request)


def _settings_response(request: Request) -> LlmSettingsResponse:
    llm_settings = get_effective_llm_settings(request)
    selected_option_id = get_selected_llm_model_option_id(request) or selected_llm_model_option_id(llm_settings.model)
    return LlmSettingsResponse(
        configured=llm_settings.configured,
        model=llm_settings.model,
        base_host=urlparse(llm_settings.base_url).netloc or None,
        selected_model_option_id=selected_option_id,
        model_options=list_llm_model_options(),
    )
