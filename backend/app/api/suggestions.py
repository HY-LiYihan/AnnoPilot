from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException

from ..llm import LlmError
from ..schemas import (
    AcceptSuggestionResponse,
    AutoAcceptSuggestionsRequest,
    AutoAcceptSuggestionsResponse,
    AutoRejectSuggestionsResponse,
    GenerateSuggestionsRequest,
    GenerateSuggestionsResponse,
    RejectSuggestionResponse,
    ReviewSuggestionResponse,
)
from ..storage import AnnotationStorage, NotFoundError, ValidationError
from .dependencies import get_storage, get_suggestion_reviewer


router = APIRouter(prefix="/api/projects/{project_id}", tags=["suggestions"])


@router.post("/documents/{document_id}/suggestions/run", response_model=GenerateSuggestionsResponse)
def generate_suggestions(
    project_id: str,
    document_id: str,
    payload: Optional[GenerateSuggestionsRequest] = None,
    storage: AnnotationStorage = Depends(get_storage),
) -> dict:
    try:
        limit = payload.limit_per_sentence if payload else 6
        min_confidence = payload.min_confidence if payload else 0.0
        return storage.generate_suggestions(project_id, document_id, limit, min_confidence)
    except ValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/documents/{document_id}/sentences/{sentence_id}/suggestions/run", response_model=GenerateSuggestionsResponse)
def generate_sentence_suggestions(
    project_id: str,
    document_id: str,
    sentence_id: str,
    payload: Optional[GenerateSuggestionsRequest] = None,
    storage: AnnotationStorage = Depends(get_storage),
) -> dict:
    try:
        limit = payload.limit_per_sentence if payload else 6
        min_confidence = payload.min_confidence if payload else 0.0
        return storage.generate_suggestions(project_id, document_id, limit, min_confidence, sentence_id=sentence_id)
    except ValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/documents/{document_id}/suggestions/auto-accept", response_model=AutoAcceptSuggestionsResponse)
def auto_accept_suggestions(
    project_id: str,
    document_id: str,
    payload: Optional[AutoAcceptSuggestionsRequest] = None,
    storage: AnnotationStorage = Depends(get_storage),
) -> dict:
    try:
        min_confidence = payload.min_confidence if payload else 0.9
        return storage.auto_accept_document_suggestions(project_id, document_id, min_confidence)
    except ValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/documents/{document_id}/suggestions/auto-reject", response_model=AutoRejectSuggestionsResponse)
def auto_reject_suggestions(
    project_id: str,
    document_id: str,
    storage: AnnotationStorage = Depends(get_storage),
) -> dict:
    try:
        return storage.auto_reject_document_suggestions(project_id, document_id)
    except ValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/suggestions/{suggestion_id}/accept", response_model=AcceptSuggestionResponse)
def accept_suggestion(
    project_id: str,
    suggestion_id: str,
    storage: AnnotationStorage = Depends(get_storage),
) -> dict:
    try:
        return {"accepted": True, "annotations": storage.accept_suggestion(project_id, suggestion_id)}
    except ValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/suggestions/{suggestion_id}/reject", response_model=RejectSuggestionResponse)
def reject_suggestion(
    project_id: str,
    suggestion_id: str,
    storage: AnnotationStorage = Depends(get_storage),
) -> dict:
    try:
        return storage.reject_suggestion(project_id, suggestion_id)
    except ValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/suggestions/{suggestion_id}/llm-review", response_model=ReviewSuggestionResponse)
def review_suggestion(
    project_id: str,
    suggestion_id: str,
    storage: AnnotationStorage = Depends(get_storage),
    reviewer=Depends(get_suggestion_reviewer),
) -> dict:
    try:
        context = storage.get_suggestion_review_context(project_id, suggestion_id)
        review = reviewer.review(context)
        return storage.record_suggestion_review(project_id, suggestion_id, review, context_sha256=storage._payload_sha256(context))
    except LlmError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except ValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
