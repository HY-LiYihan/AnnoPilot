from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


class HealthResponse(BaseModel):
    status: str


class TagResponse(BaseModel):
    id: str
    name: str
    shortcut: str
    color: str
    count: int = 0


class ImportTxtResponse(BaseModel):
    document_id: str
    filename: str
    sentence_count: int
    token_count: int
    tags: list[TagResponse]


class DocumentMetaResponse(BaseModel):
    id: str
    project_id: str
    filename: str
    created_at: str
    sentence_count: int
    token_count: int


class TokenResponse(BaseModel):
    id: str
    token_index: int
    text: str
    start_char: int
    end_char: int


class AnnotationResponse(BaseModel):
    id: str
    tag_id: str
    tag_name: str
    tag_color: str
    start_token_index: int
    end_token_index: int
    start_char: int
    end_char: int
    text: str
    created_at: str


class SentenceResponse(BaseModel):
    id: str
    index: int
    text: str
    start_char: int
    end_char: int
    completed: bool
    tokens: list[TokenResponse]
    annotations: list[AnnotationResponse]


class MetricsResponse(BaseModel):
    sentence_count: int
    completed_count: int
    progress: float
    annotation_count: int
    accuracy: Optional[float]
    accuracy_label: str


class DocumentResponse(BaseModel):
    document: DocumentMetaResponse
    tags: list[TagResponse]
    sentences: list[SentenceResponse]
    metrics: MetricsResponse


class CreateAnnotationRequest(BaseModel):
    tag_id: str
    start_token_index: int = Field(ge=0)
    end_token_index: int = Field(ge=0)


class CreateAnnotationResponse(BaseModel):
    annotations: list[AnnotationResponse]


class DeleteAnnotationResponse(BaseModel):
    deleted: bool


class CompleteSentenceRequest(BaseModel):
    completed: bool = True


class CompleteSentenceResponse(BaseModel):
    completed: bool
