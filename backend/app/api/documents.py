from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile

from ..schemas import CompleteSentenceRequest, CompleteSentenceResponse, DocumentResponse, ImportTxtResponse
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


@router.post("/sentences/{sentence_id}/complete", response_model=CompleteSentenceResponse)
def complete_sentence(
    project_id: str,
    sentence_id: str,
    payload: CompleteSentenceRequest,
    storage: AnnotationStorage = Depends(get_storage),
) -> CompleteSentenceResponse:
    try:
        storage.set_sentence_completed(project_id, sentence_id, payload.completed)
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return CompleteSentenceResponse(completed=payload.completed)
