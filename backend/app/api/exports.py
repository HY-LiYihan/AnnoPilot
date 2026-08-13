from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse, Response, StreamingResponse

from ..schemas import ExportManifestResponse, ProdigyLabelsExportResponse, TagSchemaExportResponse
from ..storage import AnnotationStorage, NotFoundError, ValidationError
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


@router.get("/documents/{document_id}/export.prodigy.jsonl")
def export_prodigy_document(
    project_id: str,
    document_id: str,
    storage: AnnotationStorage = Depends(get_storage),
) -> StreamingResponse:
    try:
        lines = storage.export_prodigy_document_lines(project_id, document_id)
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    headers = {"Content-Disposition": f'attachment; filename="{document_id}.prodigy.jsonl"'}
    return StreamingResponse(iter(lines), media_type="application/x-ndjson", headers=headers)


@router.get("/documents/{document_id}/export.prodigy.spans.jsonl")
def export_prodigy_spans_document(
    project_id: str,
    document_id: str,
    storage: AnnotationStorage = Depends(get_storage),
) -> StreamingResponse:
    try:
        lines = storage.export_prodigy_spans_document_lines(project_id, document_id)
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    headers = {"Content-Disposition": f'attachment; filename="{document_id}.prodigy.spans.jsonl"'}
    return StreamingResponse(iter(lines), media_type="application/x-ndjson", headers=headers)


@router.get("/documents/{document_id}/export.manifest.json", response_model=ExportManifestResponse)
def export_manifest(
    project_id: str,
    document_id: str,
    storage: AnnotationStorage = Depends(get_storage),
) -> JSONResponse:
    try:
        manifest = storage.export_manifest(project_id, document_id)
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    headers = {"Content-Disposition": f'attachment; filename="{document_id}.manifest.json"'}
    return JSONResponse(manifest, headers=headers)


@router.get("/documents/{document_id}/export.prodigy.bundle.zip")
def export_prodigy_bundle(
    project_id: str,
    document_id: str,
    storage: AnnotationStorage = Depends(get_storage),
) -> Response:
    try:
        bundle = storage.export_prodigy_bundle_bytes(project_id, document_id)
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    headers = {"Content-Disposition": f'attachment; filename="{document_id}.prodigy.bundle.zip"'}
    return Response(content=bundle, media_type="application/zip", headers=headers)


@router.get("/documents/{document_id}/export.goldsmith.review-queue.jsonl")
def export_goldsmith_review_queue(
    project_id: str,
    document_id: str,
    order: str = Query("hybrid"),
    limit: int = Query(100, ge=1, le=100),
    storage: AnnotationStorage = Depends(get_storage),
) -> StreamingResponse:
    try:
        lines = storage.export_goldsmith_review_queue_lines(project_id, document_id, order=order, limit=limit)
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    headers = {"Content-Disposition": f'attachment; filename="{document_id}.goldsmith.review-queue.jsonl"'}
    return StreamingResponse(iter(lines), media_type="application/x-ndjson", headers=headers)


@router.get("/documents/{document_id}/export.goldsmith.human-choices.jsonl")
def export_goldsmith_human_choices(
    project_id: str,
    document_id: str,
    storage: AnnotationStorage = Depends(get_storage),
) -> StreamingResponse:
    try:
        lines = storage.export_goldsmith_human_choices_lines(project_id, document_id)
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    headers = {"Content-Disposition": f'attachment; filename="{document_id}.goldsmith.human-choices.jsonl"'}
    return StreamingResponse(iter(lines), media_type="application/x-ndjson", headers=headers)


@router.get("/documents/{document_id}/export.goldsmith.hard-examples.jsonl")
def export_goldsmith_hard_examples(
    project_id: str,
    document_id: str,
    storage: AnnotationStorage = Depends(get_storage),
) -> StreamingResponse:
    try:
        lines = storage.export_goldsmith_hard_examples_lines(project_id, document_id)
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    headers = {"Content-Disposition": f'attachment; filename="{document_id}.goldsmith.hard-examples.jsonl"'}
    return StreamingResponse(iter(lines), media_type="application/x-ndjson", headers=headers)


@router.get("/documents/{document_id}/export.goldsmith.boundary-feedback.jsonl")
def export_goldsmith_boundary_feedback(
    project_id: str,
    document_id: str,
    storage: AnnotationStorage = Depends(get_storage),
) -> StreamingResponse:
    try:
        lines = storage.export_goldsmith_boundary_feedback_lines(project_id, document_id)
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    headers = {"Content-Disposition": f'attachment; filename="{document_id}.goldsmith.boundary-feedback.jsonl"'}
    return StreamingResponse(iter(lines), media_type="application/x-ndjson", headers=headers)


@router.get("/documents/{document_id}/export.goldsmith.consistency-scores.jsonl")
def export_goldsmith_consistency_scores(
    project_id: str,
    document_id: str,
    storage: AnnotationStorage = Depends(get_storage),
) -> StreamingResponse:
    try:
        lines = storage.export_goldsmith_consistency_scores_lines(project_id, document_id)
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    headers = {"Content-Disposition": f'attachment; filename="{document_id}.goldsmith.consistency-scores.jsonl"'}
    return StreamingResponse(iter(lines), media_type="application/x-ndjson", headers=headers)


@router.get("/documents/{document_id}/export.goldsmith.candidate-runs.jsonl")
def export_goldsmith_candidate_runs(
    project_id: str,
    document_id: str,
    storage: AnnotationStorage = Depends(get_storage),
) -> StreamingResponse:
    try:
        lines = storage.export_goldsmith_candidate_runs_lines(project_id, document_id)
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    headers = {"Content-Disposition": f'attachment; filename="{document_id}.goldsmith.candidate-runs.jsonl"'}
    return StreamingResponse(iter(lines), media_type="application/x-ndjson", headers=headers)


@router.get("/documents/{document_id}/export.goldsmith.risk-reasons.jsonl")
def export_goldsmith_risk_reasons(
    project_id: str,
    document_id: str,
    storage: AnnotationStorage = Depends(get_storage),
) -> StreamingResponse:
    try:
        lines = storage.export_goldsmith_risk_reason_lines(project_id, document_id)
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    headers = {"Content-Disposition": f'attachment; filename="{document_id}.goldsmith.risk-reasons.jsonl"'}
    return StreamingResponse(iter(lines), media_type="application/x-ndjson", headers=headers)


@router.get("/events.jsonl")
def export_events(
    project_id: str,
    storage: AnnotationStorage = Depends(get_storage),
) -> StreamingResponse:
    lines = storage.export_event_lines(project_id)
    headers = {"Content-Disposition": f'attachment; filename="{project_id}-events.jsonl"'}
    return StreamingResponse(iter(lines), media_type="application/x-ndjson", headers=headers)


@router.get("/tags/schema.json", response_model=TagSchemaExportResponse)
def export_tag_schema(
    project_id: str,
    storage: AnnotationStorage = Depends(get_storage),
) -> JSONResponse:
    schema = storage.export_tag_schema(project_id)
    headers = {"Content-Disposition": f'attachment; filename="{project_id}-tag-schema.json"'}
    return JSONResponse(schema, headers=headers)


@router.get("/tags/prodigy-labels.json", response_model=ProdigyLabelsExportResponse)
def export_prodigy_labels(
    project_id: str,
    storage: AnnotationStorage = Depends(get_storage),
) -> JSONResponse:
    labels = storage.export_prodigy_labels(project_id)
    headers = {"Content-Disposition": f'attachment; filename="{project_id}-prodigy-labels.json"'}
    return JSONResponse(labels, headers=headers)
