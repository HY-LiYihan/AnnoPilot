from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from ..schemas import (
    CreateTagRequest,
    CreateTagResponse,
    DeleteTagResponse,
    ImportTagSchemaRequest,
    ImportTagSchemaResponse,
    RenameTagRequest,
    RenameTagResponse,
    TagListResponse,
)
from ..storage import AnnotationStorage, NotFoundError, ValidationError
from .dependencies import get_storage


router = APIRouter(prefix="/api/projects/{project_id}/tags", tags=["tags"])


@router.get("", response_model=TagListResponse)
def list_tags(
    project_id: str,
    storage: AnnotationStorage = Depends(get_storage),
) -> dict:
    return {"tags": storage.get_tags(project_id)}


@router.post("", response_model=CreateTagResponse)
def create_tag(
    project_id: str,
    payload: CreateTagRequest,
    storage: AnnotationStorage = Depends(get_storage),
) -> dict:
    try:
        return {"tag": storage.create_tag(project_id, payload.name, payload.description, payload.examples)}
    except ValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/schema/import", response_model=ImportTagSchemaResponse)
def import_tag_schema(
    project_id: str,
    payload: ImportTagSchemaRequest,
    storage: AnnotationStorage = Depends(get_storage),
) -> dict:
    try:
        payload_data = payload.model_dump() if hasattr(payload, "model_dump") else payload.dict()
        return storage.import_tag_schema(project_id, payload_data)
    except ValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.patch("/{tag_id}", response_model=RenameTagResponse)
def rename_tag(
    project_id: str,
    tag_id: str,
    payload: RenameTagRequest,
    storage: AnnotationStorage = Depends(get_storage),
) -> dict:
    try:
        return {"tag": storage.rename_tag(project_id, tag_id, payload.name, payload.description, payload.examples)}
    except ValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.delete("/{tag_id}", response_model=DeleteTagResponse)
def delete_tag(
    project_id: str,
    tag_id: str,
    storage: AnnotationStorage = Depends(get_storage),
) -> dict:
    try:
        return storage.delete_tag(project_id, tag_id)
    except ValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
