from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse

from ..schemas import AnnotationRunListResponse, RunProvenanceResponse
from ..storage import AnnotationStorage, NotFoundError
from .dependencies import get_storage


router = APIRouter(prefix="/api/projects/{project_id}", tags=["runs"])


@router.get("/runs", response_model=AnnotationRunListResponse)
def list_runs(
    project_id: str,
    document_id: Optional[str] = None,
    limit: int = Query(default=10, ge=1, le=50),
    storage: AnnotationStorage = Depends(get_storage),
) -> dict:
    return {"runs": storage.list_runs(project_id, document_id=document_id, limit=limit)}


@router.get("/runs/{run_id}/provenance.json", response_model=RunProvenanceResponse)
def export_run_provenance(
    project_id: str,
    run_id: str,
    storage: AnnotationStorage = Depends(get_storage),
) -> JSONResponse:
    try:
        provenance = storage.export_run_provenance(project_id, run_id)
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    headers = {"Content-Disposition": f'attachment; filename="{run_id}.provenance.json"'}
    return JSONResponse(provenance, headers=headers)
