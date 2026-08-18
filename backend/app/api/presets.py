from __future__ import annotations

from dataclasses import asdict
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
    should_auto_accept = preset.auto_accept_on_load if request.auto_accept_suggestions is None else request.auto_accept_suggestions
    should_complete_sentences = (
        preset.complete_sentences_on_load if request.complete_sentences is None else request.complete_sentences
    )
    try:
        if preset.clear_tags_on_load:
            storage.reset_project(project_id)
        if preset.tag_schema.get("tags"):
            storage.import_tag_schema(project_id, preset.tag_schema)
        imported = storage.import_txt(project_id, preset.filename, preset.text.encode("utf-8"))
        suggestion_result = {
            "suggestions_created": 0,
            "suggestion_run_id": None,
            "source_counts": {},
            "confidence_counts": {},
            "auto_accepted": 0,
            "auto_accept_skipped": 0,
            "auto_completed": 0,
            "auto_accepted_suggestion_ids": [],
            "auto_completed_sentence_ids": [],
        }
        if request.generate_suggestions:
            confidence_floor = request.min_confidence if request.min_confidence is not None else preset.default_min_confidence
            limit_per_sentence = request.limit_per_sentence or preset.default_limit_per_sentence
            if preset.calibration_candidates:
                candidates = []
                candidate_counts_by_anchor: dict[str, int] = {}
                for candidate in preset.calibration_candidates:
                    if candidate.confidence < confidence_floor:
                        continue
                    anchor_count = candidate_counts_by_anchor.get(candidate.sentence_contains, 0)
                    if anchor_count >= limit_per_sentence:
                        continue
                    candidates.append(asdict(candidate))
                    candidate_counts_by_anchor[candidate.sentence_contains] = anchor_count + 1
                generated = storage.seed_calibration_suggestions(
                    project_id,
                    imported["document_id"],
                    candidates,
                    preset_id=preset.id,
                )
            else:
                generated = storage.generate_suggestions(
                    project_id,
                    imported["document_id"],
                    limit_per_sentence,
                    confidence_floor,
                )
            accepted = {
                "accepted": 0,
                "skipped": 0,
                "completed": 0,
                "accepted_suggestion_ids": [],
                "completed_sentence_ids": [],
            }
            if should_auto_accept and generated["suggestions_created"]:
                accept_floor = request.auto_accept_min_confidence
                if accept_floor is None:
                    accept_floor = max(confidence_floor, 0.9)
                accepted = storage.auto_accept_document_suggestions(
                    project_id,
                    imported["document_id"],
                    accept_floor,
                    complete_sentences=should_complete_sentences,
                )
            suggestion_result = {
                "suggestions_created": generated["suggestions_created"],
                "suggestion_run_id": generated["run_id"],
                "source_counts": generated["source_counts"],
                "confidence_counts": generated["confidence_counts"],
                "auto_accepted": accepted["accepted"],
                "auto_accept_skipped": accepted["skipped"],
                "auto_completed": accepted["completed"],
                "auto_accepted_suggestion_ids": accepted["accepted_suggestion_ids"],
                "auto_completed_sentence_ids": accepted["completed_sentence_ids"],
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
