from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException

from ..llm import LlmError
from ..schemas import GenerateEngagementCandidatesRequest, GenerateEngagementCandidatesResponse
from ..storage import AnnotationStorage, NotFoundError, ValidationError
from .dependencies import get_engagement_candidate_generator, get_storage


router = APIRouter(prefix="/api/projects/{project_id}", tags=["engagement"])


@router.post("/documents/{document_id}/engagement/candidates/run", response_model=GenerateEngagementCandidatesResponse)
def generate_engagement_candidates(
    project_id: str,
    document_id: str,
    payload: Optional[GenerateEngagementCandidatesRequest] = None,
    storage: AnnotationStorage = Depends(get_storage),
    generator=Depends(get_engagement_candidate_generator),
) -> dict:
    request = payload or GenerateEngagementCandidatesRequest()
    try:
        return storage.generate_engagement_candidates(
            project_id,
            document_id,
            candidate_count=request.candidate_count,
            temperature=request.temperature,
            sentence_id=request.sentence_id,
            generator=generator,
        )
    except LlmError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except ValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
