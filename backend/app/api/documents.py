from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile

from ..schemas import (
    CompleteSentenceRequest,
    CompleteSentenceResponse,
    ImportAnnotationsResponse,
    DocumentListResponse,
    DocumentResponse,
    DocumentSummaryResponse,
    ImportTxtResponse,
    SentencesPageResponse,
    UpdateSessionCursorRequest,
    UpdateSessionCursorResponse,
)
from ..storage import AnnotationStorage, NotFoundError, ValidationError
from .dependencies import get_storage


router = APIRouter(prefix="/api/projects/{project_id}", tags=["documents"])


@router.post("/import-txt", response_model=ImportTxtResponse)
async def import_txt(
    project_id: str,
    file: Annotated[UploadFile, File()],
    storage: AnnotationStorage = Depends(get_storage),
) -> dict:
    filename = file.filename or "import.txt"
    if not filename.lower().endswith(".txt"):
        raise HTTPException(status_code=400, detail="Only .txt files are supported.")
    data = await file.read()
    try:
        return storage.import_txt(project_id, filename, data)
    except ValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/documents", response_model=DocumentListResponse)
def list_documents(
    project_id: str,
    limit: int = Query(50, ge=1, le=100),
    storage: AnnotationStorage = Depends(get_storage),
) -> dict:
    return storage.list_documents(project_id, limit=limit)


@router.post("/documents/{document_id}/import-annotations-jsonl", response_model=ImportAnnotationsResponse)
async def import_annotations_jsonl(
    project_id: str,
    document_id: str,
    file: Annotated[UploadFile, File()],
    storage: AnnotationStorage = Depends(get_storage),
) -> dict:
    filename = file.filename or "annotations.jsonl"
    if not filename.lower().endswith(".jsonl"):
        raise HTTPException(status_code=400, detail="Only .jsonl files are supported.")
    data = await file.read()
    try:
        return storage.import_annotations_jsonl(project_id, document_id, filename, data)
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/documents/{document_id}", response_model=DocumentResponse)
def get_document(
    project_id: str,
    document_id: str,
    storage: AnnotationStorage = Depends(get_storage),
) -> dict:
    try:
        return storage.get_document(project_id, document_id)
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/documents/{document_id}/summary", response_model=DocumentSummaryResponse)
def get_document_summary(
    project_id: str,
    document_id: str,
    storage: AnnotationStorage = Depends(get_storage),
) -> dict:
    try:
        return storage.get_document_summary(project_id, document_id)
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/documents/{document_id}/session/cursor", response_model=UpdateSessionCursorResponse)
def update_session_cursor(
    project_id: str,
    document_id: str,
    payload: UpdateSessionCursorRequest,
    storage: AnnotationStorage = Depends(get_storage),
) -> dict:
    try:
        return storage.set_session_cursor(project_id, document_id, payload.current_sentence_index)
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/documents/{document_id}/sentences", response_model=SentencesPageResponse)
def get_document_sentences(
    project_id: str,
    document_id: str,
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    storage: AnnotationStorage = Depends(get_storage),
) -> dict:
    try:
        return storage.get_document_sentences(project_id, document_id, offset=offset, limit=limit)
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/sentences/{sentence_id}/complete", response_model=CompleteSentenceResponse)
def complete_sentence(
    project_id: str,
    sentence_id: str,
    payload: CompleteSentenceRequest,
    storage: AnnotationStorage = Depends(get_storage),
) -> CompleteSentenceResponse:
    try:
        result = storage.set_sentence_completed(project_id, sentence_id, payload.completed, payload.answer)
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return CompleteSentenceResponse(**result)
