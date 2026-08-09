from __future__ import annotations

from urllib.parse import urlparse

from fastapi import APIRouter, Request

from ..schemas import HealthResponse
from .dependencies import get_effective_llm_settings


router = APIRouter(prefix="/api", tags=["health"])


@router.get("/health", response_model=HealthResponse)
def health(request: Request) -> HealthResponse:
    llm_settings = get_effective_llm_settings(request)
    base_host = urlparse(llm_settings.base_url).netloc or None
    return HealthResponse(
        status="ok",
        llm_configured=llm_settings.configured,
        llm_model=llm_settings.model,
        llm_base_host=base_host,
    )
