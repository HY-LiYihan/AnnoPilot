from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from ..schemas import CreateAnnotationRequest, CreateAnnotationResponse, DeleteAnnotationResponse
from ..storage import AnnotationStorage, NotFoundError, ValidationError
from .dependencies import get_storage


router = APIRouter(prefix="/api/projects/{project_id}", tags=["annotations"])


@router.post("/sentences/{sentence_id}/annotations", response_model=CreateAnnotationResponse)
def create_annotation(
    project_id: str,
    sentence_id: str,
    payload: CreateAnnotationRequest,
    storage: AnnotationStorage = Depends(get_storage),
) -> dict:
    try:
        annotations = storage.create_annotation(
            project_id=project_id,
            sentence_id=sentence_id,
            tag_id=payload.tag_id,
            start_token_index=payload.start_token_index,
            end_token_index=payload.end_token_index,
        )
    except ValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"annotations": annotations}


@router.delete("/annotations/{annotation_id}", response_model=DeleteAnnotationResponse)
def delete_annotation(
    project_id: str,
    annotation_id: str,
    storage: AnnotationStorage = Depends(get_storage),
) -> DeleteAnnotationResponse:
    try:
        storage.delete_annotation(project_id, annotation_id)
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return DeleteAnnotationResponse(deleted=True)
