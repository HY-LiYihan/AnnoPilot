from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException

from ..presets import get_sample_preset, list_sample_presets
from ..schemas import LoadSamplePresetRequest, LoadSamplePresetResponse, SamplePresetListResponse
from ..storage import AnnotationStorage, NotFoundError, ValidationError
from .dependencies import get_storage


router = APIRouter(prefix="/api/projects/{project_id}/sample-presets", tags=["sample-presets"])


@router.get("", response_model=SamplePresetListResponse)
def get_presets(project_id: str) -> dict:
    return {"presets": list_sample_presets()}


@router.post("/{preset_id}/load", response_model=LoadSamplePresetResponse)
def load_preset(
    project_id: str,
    preset_id: str,
    payload: Optional[LoadSamplePresetRequest] = None,
    storage: AnnotationStorage = Depends(get_storage),
) -> dict:
    preset = get_sample_preset(preset_id)
    if preset is None:
        raise HTTPException(status_code=404, detail="Sample preset not found.")

    request = payload or LoadSamplePresetRequest()
    try:
        storage.import_tag_schema(project_id, preset.tag_schema)
        imported = storage.import_txt(project_id, preset.filename, preset.text.encode("utf-8"))
        suggestion_result = {
            "suggestions_created": 0,
            "suggestion_run_id": None,
            "source_counts": {},
            "confidence_counts": {},
        }
        if request.generate_suggestions:
            generated = storage.generate_suggestions(
                project_id,
                imported["document_id"],
                request.limit_per_sentence or preset.default_limit_per_sentence,
                request.min_confidence if request.min_confidence is not None else preset.default_min_confidence,
            )
            suggestion_result = {
                "suggestions_created": generated["suggestions_created"],
                "suggestion_run_id": generated["run_id"],
                "source_counts": generated["source_counts"],
                "confidence_counts": generated["confidence_counts"],
            }
        return {
            "preset": preset.summary(),
            "document_id": imported["document_id"],
            "filename": imported["filename"],
            "sentence_count": imported["sentence_count"],
            "token_count": imported["token_count"],
            "tags": imported["tags"],
            **suggestion_result,
        }
    except ValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
