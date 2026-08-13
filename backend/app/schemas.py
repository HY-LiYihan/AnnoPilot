from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field


class HealthResponse(BaseModel):
    status: str
    llm_configured: bool = False
    llm_model: Optional[str] = None
    llm_base_host: Optional[str] = None


class LlmModelOptionResponse(BaseModel):
    id: str
    family: str
    tier: str
    model: str


class LlmSettingsResponse(BaseModel):
    configured: bool
    model: Optional[str] = None
    base_host: Optional[str] = None
    selected_model_option_id: Optional[str] = None
    model_options: list[LlmModelOptionResponse]


class UpdateLlmSettingsRequest(BaseModel):
    model_option_id: str = Field(min_length=1, max_length=80)


class TagResponse(BaseModel):
    id: str
    name: str
    description: Optional[str] = None
    examples: list[str] = Field(default_factory=list)
    shortcut: str
    color: str
    count: int = 0
    usage_count: int = 0
    suggestion_count: int = 0


class CreateTagRequest(BaseModel):
    name: str = Field(min_length=1, max_length=32)
    description: Optional[str] = Field(default=None, max_length=280)
    examples: list[str] = Field(default_factory=list)


class RenameTagRequest(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=32)
    description: Optional[str] = Field(default=None, max_length=280)
    examples: Optional[list[str]] = None


class ImportTagSchemaItemRequest(BaseModel):
    id: str = Field(min_length=1, max_length=80)
    name: str = Field(min_length=1, max_length=32)
    description: Optional[str] = Field(default=None, max_length=280)
    examples: list[str] = Field(default_factory=list)
    shortcut: Optional[str] = Field(default=None, max_length=12)
    color: Optional[str] = Field(default=None, max_length=32)


class ImportTagSchemaRequest(BaseModel):
    schema_version: str
    record_type: str
    content_sha256: Optional[str] = None
    tags: list[ImportTagSchemaItemRequest]


class TagListResponse(BaseModel):
    tags: list[TagResponse]


class CreateTagResponse(BaseModel):
    tag: TagResponse


class RenameTagResponse(BaseModel):
    tag: TagResponse


class DeleteTagResponse(BaseModel):
    deleted: bool
    tag_id: str
    annotation_count: int
    suggestion_count: int = 0


class ImportTagSchemaResponse(BaseModel):
    created: int
    updated: int
    skipped: int
    content_sha256: str
    tags: list[TagResponse]


class ImportTxtResponse(BaseModel):
    document_id: str
    filename: str
    sentence_count: int
    token_count: int
    tags: list[TagResponse]


class ProjectResetResponse(BaseModel):
    project_id: str
    reset_at: str
    deleted_documents: int
    deleted_sentences: int
    deleted_tokens: int
    deleted_annotations: int
    deleted_suggestions: int
    deleted_suggestion_reviews: int
    deleted_runs: int
    deleted_sessions: int


class ImportAnnotationsResponse(BaseModel):
    document_id: str
    filename: str
    record_count: int
    matched_count: int
    skipped_count: int
    created_tag_count: int
    created_annotation_count: int
    deleted_annotation_count: int
    completed_sentence_count: int
    source_sha256: str
    tags: list[TagResponse]


class AnnotationImportHistoryItemResponse(BaseModel):
    event_id: Optional[str] = None
    document_id: str
    filename: str
    record_count: int
    matched_count: int
    skipped_count: int
    created_tag_count: int
    created_annotation_count: int
    deleted_annotation_count: int
    completed_sentence_count: int
    source_sha256: str
    source_record_results: list[dict[str, Any]] = Field(default_factory=list)
    actor_id: Optional[str] = None
    ts: Optional[str] = None


class AnnotationImportHistoryResponse(BaseModel):
    imports: list[AnnotationImportHistoryItemResponse]


class DocumentMetaResponse(BaseModel):
    id: str
    project_id: str
    filename: str
    created_at: str
    sentence_count: int
    token_count: int


class DocumentListItemResponse(DocumentMetaResponse):
    completed_count: int = 0
    progress: float = 0.0
    annotation_count: int = 0
    suggestion_count: int = 0
    current_sentence_index: Optional[int] = None
    session_updated_at: Optional[str] = None


class DocumentListResponse(BaseModel):
    documents: list[DocumentListItemResponse]


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
    source: str = "human"
    source_suggestion_id: Optional[str] = None
    created_at: str


class SuggestionReviewPayload(BaseModel):
    model: str
    recommendation: str
    confidence: float
    rationale: str
    context_sha256: Optional[str] = None
    created_at: Optional[str] = None


class SuggestionResponse(BaseModel):
    id: str
    run_id: Optional[str] = None
    sentence_id: str
    tag_id: str
    tag_name: str
    tag_color: str
    start_token_index: int
    end_token_index: int
    start_char: int
    end_char: int
    text: str
    confidence: float
    source: str
    evidence_text: Optional[str] = None
    match_key: Optional[str] = None
    evidence_match_key: Optional[str] = None
    context_before: Optional[str] = None
    context_after: Optional[str] = None
    status: str
    created_at: str
    latest_review: Optional[SuggestionReviewPayload] = None


class SentenceResponse(BaseModel):
    id: str
    index: int
    text: str
    start_char: int
    end_char: int
    completed: bool
    answer: str = "pending"
    tokens: list[TokenResponse]
    annotations: list[AnnotationResponse]
    suggestions: list[SuggestionResponse] = []


class SentenceQueueItemResponse(BaseModel):
    id: str
    index: int
    completed: bool
    answer: str = "pending"
    suggestion_count: int = 0


class ReviewQueueItemResponse(BaseModel):
    id: str
    index: int
    text: str
    suggestion_count: int
    priority_score: float
    min_confidence: float
    lexical_risk_score: float = 0.0
    llm_review_risk_score: float = 0.0
    candidate_disagreement_score: float = 0.0
    risk_score: float
    review_route: str = "risk"
    first_suggestion: Optional[SuggestionResponse] = None


class ReviewQueueResponse(BaseModel):
    items: list[ReviewQueueItemResponse]
    total: int


class ReviewEfficiencyPointResponse(BaseModel):
    rank: int
    suggestion_id: str
    sentence_id: str
    sentence_index: int
    cumulative_reviewed: int
    cumulative_disagreements: int
    disagreement: bool
    route: str


class ReviewEfficiencyCurveResponse(BaseModel):
    order: str
    reviewed_count: int
    disagreement_count: int
    early_reviewed_count: int
    early_disagreement_count: int
    first_disagreement_rank: Optional[int] = None
    points: list[ReviewEfficiencyPointResponse] = Field(default_factory=list)


class LabelCountResponse(BaseModel):
    tag_id: str
    name: str
    color: str
    count: int


class MetricsResponse(BaseModel):
    sentence_count: int
    completed_count: int
    answer_counts: dict[str, int] = Field(default_factory=dict)
    progress: float
    annotation_count: int
    suggestion_count: int = 0
    annotation_label_counts: list[LabelCountResponse] = Field(default_factory=list)
    suggestion_label_counts: list[LabelCountResponse] = Field(default_factory=list)
    suggestion_status_counts: dict[str, int] = Field(default_factory=dict)
    suggestion_source_counts: dict[str, int] = Field(default_factory=dict)
    suggestion_confidence_counts: dict[str, int] = Field(default_factory=dict)
    suggestion_review_counts: dict[str, int] = Field(default_factory=dict)
    reviewed_suggestion_count: int = 0
    accuracy: Optional[float]
    accuracy_label: str
    calibration_count: int = 0
    calibration_disagreement_count: int = 0
    calibration_error_rate: Optional[float] = None
    review_efficiency_curves: dict[str, ReviewEfficiencyCurveResponse] = Field(default_factory=dict)


class SessionResponse(BaseModel):
    id: str
    actor_id: str
    current_sentence_index: Optional[int] = None
    updated_at: Optional[str] = None


class DocumentResponse(BaseModel):
    document: DocumentMetaResponse
    tags: list[TagResponse]
    sentences: list[SentenceResponse]
    metrics: MetricsResponse
    session: SessionResponse


class DocumentSummaryResponse(BaseModel):
    document: DocumentMetaResponse
    tags: list[TagResponse]
    metrics: MetricsResponse
    queue: list[SentenceQueueItemResponse]
    session: SessionResponse


class SentencesPageResponse(BaseModel):
    sentences: list[SentenceResponse]
    offset: int
    limit: int
    total: int
    has_more: bool


class SamplePresetResponse(BaseModel):
    id: str
    title: str
    description: str
    filename: str
    language_pair: str
    tag_count: int
    default_limit_per_sentence: int
    default_min_confidence: float


class SamplePresetListResponse(BaseModel):
    presets: list[SamplePresetResponse]


class LoadSamplePresetRequest(BaseModel):
    generate_suggestions: bool = True
    limit_per_sentence: Optional[int] = Field(default=None, ge=1, le=20)
    min_confidence: Optional[float] = Field(default=None, ge=0.0, le=1.0)


class LoadSamplePresetResponse(BaseModel):
    preset: SamplePresetResponse
    document_id: str
    filename: str
    sentence_count: int
    token_count: int
    tags: list[TagResponse]
    suggestions_created: int = 0
    suggestion_run_id: Optional[str] = None
    source_counts: dict[str, int] = Field(default_factory=dict)
    confidence_counts: dict[str, int] = Field(default_factory=dict)


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
    answer: Optional[str] = None


class CompleteSentenceResponse(BaseModel):
    completed: bool
    answer: str = "pending"


class UpdateSessionCursorRequest(BaseModel):
    current_sentence_index: int = Field(ge=0)


class UpdateSessionCursorResponse(BaseModel):
    session: SessionResponse


class GenerateSuggestionsRequest(BaseModel):
    limit_per_sentence: int = Field(default=6, ge=1, le=20)
    min_confidence: float = Field(default=0.0, ge=0.0, le=1.0)


class GenerateSuggestionsResponse(BaseModel):
    run_id: str
    suggestions_created: int
    source_counts: dict[str, int] = Field(default_factory=dict)
    confidence_counts: dict[str, int] = Field(default_factory=dict)
    suggestions: list[SuggestionResponse]


class AutoAcceptSuggestionsRequest(BaseModel):
    min_confidence: float = Field(default=0.9, ge=0.0, le=1.0)


class AutoAnnotateSuggestionsResponse(BaseModel):
    run_id: str
    suggestions_created: int
    source_counts: dict[str, int] = Field(default_factory=dict)
    confidence_counts: dict[str, int] = Field(default_factory=dict)
    accepted: int
    skipped: int
    min_confidence: float
    accepted_suggestion_ids: list[str]
    affected_sentence_ids: list[str]


class AutoAcceptSuggestionsResponse(BaseModel):
    accepted: int
    skipped: int
    min_confidence: float
    accepted_suggestion_ids: list[str]
    affected_sentence_ids: list[str]


class AcceptSentenceSuggestionsResponse(BaseModel):
    accepted: int
    skipped: int
    accepted_suggestion_ids: list[str]
    affected_sentence_ids: list[str]
    annotations: list[AnnotationResponse]


class ApplySentenceSuggestionReviewsResponse(BaseModel):
    accepted: int
    rejected: int
    skipped: int
    kept: int
    accepted_suggestion_ids: list[str]
    rejected_suggestion_ids: list[str]
    affected_sentence_ids: list[str]
    annotations: list[AnnotationResponse]


class ApplyDocumentSuggestionReviewsResponse(BaseModel):
    accepted: int
    rejected: int
    skipped: int
    kept: int
    accepted_suggestion_ids: list[str]
    rejected_suggestion_ids: list[str]
    affected_sentence_ids: list[str]


class AutoRejectSuggestionsResponse(BaseModel):
    rejected: int
    rejected_suggestion_ids: list[str]
    affected_sentence_ids: list[str]


class RejectSentenceSuggestionsResponse(BaseModel):
    rejected: int
    rejected_suggestion_ids: list[str]
    affected_sentence_ids: list[str]


class AnnotationRunResponse(BaseModel):
    id: str
    project_id: str
    document_id: str
    filename: str
    recipe: str
    config: dict[str, object]
    input_count: int
    suggestion_count: int
    pending_count: int = 0
    accepted_count: int = 0
    rejected_count: int = 0
    acceptance_rate: Optional[float] = None
    source_counts: dict[str, int] = Field(default_factory=dict)
    confidence_counts: dict[str, int] = Field(default_factory=dict)
    created_at: str


class AnnotationRunListResponse(BaseModel):
    runs: list[AnnotationRunResponse]


class SuggestionDecisionEventResponse(BaseModel):
    event_id: Optional[str] = None
    type: str
    action: str
    ts: Optional[str] = None
    sentence_id: Optional[str] = None
    actor_type: Optional[str] = None
    actor_id: Optional[str] = None


class RunProvenanceSuggestionResponse(BaseModel):
    id: str
    sentence_id: str
    sentence_index: int
    tag_id: str
    tag_name: str
    tag_color: str
    start_token_index: int
    end_token_index: int
    start_char: int
    end_char: int
    text: str
    confidence: float
    source: str
    evidence_text: Optional[str] = None
    match_key: Optional[str] = None
    evidence_match_key: Optional[str] = None
    context_before: Optional[str] = None
    context_after: Optional[str] = None
    status: str
    decision_event: Optional[SuggestionDecisionEventResponse] = None
    latest_review: Optional[SuggestionReviewPayload] = None
    created_at: str


class RunProvenanceResponse(BaseModel):
    schema_version: str
    record_type: str
    generated_at: str
    content_sha256: str
    project_id: str
    run: AnnotationRunResponse
    status_counts: dict[str, int]
    source_counts: dict[str, int]
    confidence_counts: dict[str, int]
    review_counts: dict[str, int]
    suggestions: list[RunProvenanceSuggestionResponse]


class ExportArtifactResponse(BaseModel):
    filename: str
    schema_version: str
    line_count: int
    byte_count: int
    sha256: str
    content_sha256: Optional[str] = None


class ExportManifestAuditResponse(BaseModel):
    project_id: str
    event_count: int
    pending_outbox_count: int
    invalid_event_count: int
    legacy_event_count: int = 0
    non_replayable_event_count: int = 0
    replay_issue_counts: dict[str, int] = Field(default_factory=dict)
    schema_versions: list[str]
    event_types: dict[str, int]
    actor_type_counts: dict[str, int] = Field(default_factory=dict)
    actor_id_counts: dict[str, int] = Field(default_factory=dict)
    last_event_type: Optional[str]
    last_event_ts: Optional[str]
    rebuild_status: str


class ProdigyReadinessResponse(BaseModel):
    ready: bool
    status: str
    blockers: list[str] = Field(default_factory=list)
    sentence_count: int
    completed_sentence_count: int
    progress: float
    annotation_count: int
    covered_label_count: int
    total_label_count: int
    pending_suggestion_count: int
    formats: dict[str, str]


class ExportManifestResponse(BaseModel):
    schema_version: str
    record_type: str
    generated_at: str
    project_id: str
    document: DocumentMetaResponse
    metrics: MetricsResponse
    prodigy_readiness: ProdigyReadinessResponse
    tag_count: int
    annotation_source_counts: dict[str, int]
    source_run_ids: list[str]
    runs: list[AnnotationRunResponse]
    event_audit: ExportManifestAuditResponse
    run_provenance_artifacts: dict[str, ExportArtifactResponse] = Field(default_factory=dict)
    artifacts: dict[str, ExportArtifactResponse]


class TagSchemaItemResponse(BaseModel):
    id: str
    name: str
    description: Optional[str] = None
    examples: list[str] = Field(default_factory=list)
    shortcut: str
    color: str


class TagSchemaExportResponse(BaseModel):
    schema_version: str
    record_type: str
    generated_at: str
    content_sha256: str
    project_id: str
    tag_count: int
    retrieval: str
    tags: list[TagSchemaItemResponse]


class AcceptSuggestionResponse(BaseModel):
    accepted: bool
    annotations: list[AnnotationResponse]


class RejectSuggestionResponse(BaseModel):
    rejected: bool
    suggestion_id: str


class ReviewSuggestionResponse(BaseModel):
    suggestion_id: str
    model: str
    recommendation: str
    confidence: float
    rationale: str
    context_sha256: Optional[str] = None
    created_at: Optional[str] = None


class ReviewSentenceSuggestionsResponse(BaseModel):
    reviewed: int
    reviewed_suggestion_ids: list[str]
    reviews: list[ReviewSuggestionResponse]


class RebuildIssueResponse(BaseModel):
    line_number: int
    event_id: Optional[str]
    event_type: Optional[str]
    message: str


class AuditSummaryResponse(BaseModel):
    project_id: str
    event_count: int
    pending_outbox_count: int
    invalid_event_count: int
    legacy_event_count: int = 0
    non_replayable_event_count: int = 0
    replay_issue_counts: dict[str, int] = Field(default_factory=dict)
    replay_issues: list[RebuildIssueResponse] = Field(default_factory=list)
    schema_versions: list[str]
    event_types: dict[str, int]
    actor_type_counts: dict[str, int] = Field(default_factory=dict)
    actor_id_counts: dict[str, int] = Field(default_factory=dict)
    last_event_type: Optional[str]
    last_event_ts: Optional[str]
    rebuild_status: str


class RebuildPreviewResponse(BaseModel):
    project_id: str
    event_count: int
    documents: int
    sentences: int
    tokens: int
    tags: int
    annotations: int
    suggestions: int
    suggestion_reviews: int
    runs: int
    issues: list[RebuildIssueResponse]
    ok: bool
