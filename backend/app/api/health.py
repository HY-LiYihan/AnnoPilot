from __future__ import annotations

from urllib.parse import urlparse

from fastapi import APIRouter

from ..schemas import HealthResponse
from ..settings import get_llm_settings


router = APIRouter(prefix="/api", tags=["health"])


@router.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    llm_settings = get_llm_settings()
    base_host = urlparse(llm_settings.base_url).netloc or None
    return HealthResponse(
        status="ok",
        llm_configured=llm_settings.configured,
        llm_model=llm_settings.model,
        llm_base_host=base_host,
    )
