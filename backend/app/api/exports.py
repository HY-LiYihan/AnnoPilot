from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse

from ..storage import AnnotationStorage, NotFoundError
from .dependencies import get_storage


router = APIRouter(prefix="/api/projects/{project_id}", tags=["exports"])


@router.get("/documents/{document_id}/export.jsonl")
def export_document(
    project_id: str,
    document_id: str,
    storage: AnnotationStorage = Depends(get_storage),
) -> StreamingResponse:
    try:
        lines = storage.export_document_lines(project_id, document_id)
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    headers = {"Content-Disposition": f'attachment; filename="{document_id}.jsonl"'}
    return StreamingResponse(iter(lines), media_type="application/x-ndjson", headers=headers)
