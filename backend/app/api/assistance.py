from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from ..schemas import (
    AssistanceDecisionRequest,
    AssistanceDecisionResponse,
    AssistanceStatusResponse,
    UpdateAssistanceSettingsRequest,
)
from ..storage import AnnotationStorage, ConflictError, NotFoundError, ValidationError
from .dependencies import get_storage


router = APIRouter(prefix="/api/projects/{project_id}", tags=["assistance"])


@router.get("/documents/{document_id}/assistance", response_model=AssistanceStatusResponse)
def get_assistance_status(
    project_id: str,
    document_id: str,
    storage: AnnotationStorage = Depends(get_storage),
) -> dict:
    try:
        return storage.get_assistance_status(project_id, document_id)
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.put("/documents/{document_id}/assistance/settings", response_model=AssistanceStatusResponse)
def update_assistance_settings(
    project_id: str,
    document_id: str,
    payload: UpdateAssistanceSettingsRequest,
    storage: AnnotationStorage = Depends(get_storage),
) -> dict:
    try:
        return storage.set_assistance_enabled(project_id, document_id, payload.enabled)
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/sentences/{sentence_id}/assistance/decision", response_model=AssistanceDecisionResponse)
def decide_assistance(
    project_id: str,
    sentence_id: str,
    payload: AssistanceDecisionRequest,
    storage: AnnotationStorage = Depends(get_storage),
) -> dict:
    try:
        return storage.decide_assistance(
            project_id,
            sentence_id,
            action=payload.action,
            draft_id=payload.draft_id,
            draft_version=payload.draft_version,
            final_spans=[span.model_dump() for span in payload.final_spans],
            error_reasons=list(payload.error_reasons),
            error_note=payload.error_note,
        )
    except ConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
