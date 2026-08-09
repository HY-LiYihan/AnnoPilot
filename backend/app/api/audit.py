from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory

from fastapi import APIRouter, Depends, HTTPException

from ..rebuild import rebuild_project_from_events
from ..schemas import AuditSummaryResponse, RebuildPreviewResponse
from ..storage import AnnotationStorage
from .dependencies import get_storage


router = APIRouter(prefix="/api/projects/{project_id}", tags=["audit"])


@router.get("/audit", response_model=AuditSummaryResponse)
def audit_project(
    project_id: str,
    storage: AnnotationStorage = Depends(get_storage),
) -> dict:
    return storage.audit_project(project_id)


@router.post("/rebuild/preview", response_model=RebuildPreviewResponse)
def preview_rebuild(
    project_id: str,
    storage: AnnotationStorage = Depends(get_storage),
) -> dict:
    storage.flush_event_outbox(project_id)
    event_path = storage.data_root / project_id / "events.jsonl"
    if not event_path.exists():
        raise HTTPException(status_code=404, detail="Project event log does not exist.")

    with TemporaryDirectory(prefix="annopilot-rebuild-") as temp_dir:
        temp_root = Path(temp_dir)
        result = rebuild_project_from_events(
            project_id=project_id,
            event_path=event_path,
            database_path=temp_root / "annopilot.sqlite",
            data_root=temp_root / "projects",
            force=True,
        )
    return result.to_dict()
