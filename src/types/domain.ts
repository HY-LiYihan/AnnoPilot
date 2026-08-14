export const PROJECT_ID = 'default'
export const ACTIVE_DOCUMENT_KEY = 'annopilot.activeDocumentId'

export type TagDef = {
  id: string
  name: string
  description?: string | null
  examples: string[]
  shortcut: string
  color: string
  count: number
  usage_count?: number
  suggestion_count?: number
}

export type TokenDef = {
  id: string
  token_index: number
  text: string
  start_char: number
  end_char: number
}

export type AnnotationDef = {
  id: string
  tag_id: string
  tag_name: string
  tag_color: string
  start_token_index: number
  end_token_index: number
  start_char: number
  end_char: number
  text: string
  source: 'human' | 'accepted_suggestion' | string
  source_suggestion_id?: string | null
  created_at: string
}

export type SuggestionDef = {
  id: string
  run_id?: string | null
  sentence_id: string
  tag_id: string
  tag_name: string
  tag_color: string
  start_token_index: number
  end_token_index: number
  start_char: number
  end_char: number
  text: string
  confidence: number
  source: string
  evidence_text?: string | null
  match_key?: string | null
  evidence_match_key?: string | null
  context_before?: string | null
  context_after?: string | null
  status: string
  created_at: string
  latest_review?: Omit<SuggestionReview, 'suggestion_id'> | null
}

export type SuggestionReview = {
  suggestion_id: string
  model: string
  recommendation: string
  confidence: number
  rationale: string
  context_sha256?: string | null
  judge?: {
    format_score?: number
    concept_fit_score?: number
    boundary_score?: number
    relation_score?: number
    missed_span_risk?: number
    extra_span_risk?: number
    overall_score?: number
    needs_review?: boolean
    error_types?: string[]
    risk_flags?: string[]
    rationale?: string
  } | null
  created_at?: string | null
}

export type SentenceDef = {
  id: string
  index: number
  text: string
  start_char: number
  end_char: number
  completed: boolean
  answer: 'pending' | 'accept' | 'reject' | 'ignore' | string
  tokens: TokenDef[]
  annotations: AnnotationDef[]
  suggestions: SuggestionDef[]
}

export type SentenceQueueItem = {
  id: string
  index: number
  completed: boolean
  answer: 'pending' | 'accept' | 'reject' | 'ignore' | string
  suggestion_count: number
}

export type ReviewQueueItem = {
  id: string
  index: number
  text: string
  suggestion_count: number
  priority_score: number
  min_confidence: number
  lexical_risk_score: number
  llm_review_risk_score: number
  judge_review_risk_score: number
  candidate_disagreement_score: number
  risk_score: number
  risk_reason_codes: string[]
  review_route: 'position' | 'uncertain' | 'risk' | 'calibration' | string
  action_hint: string
  review_guidance: {
    domain?: string
    primary_action?: string
    risk_reason_codes?: string[]
    action_hint?: string
    candidate_count?: number
    boundary_checks?: string[]
  }
  first_suggestion: SuggestionDef | null
  candidate_suggestions: SuggestionDef[]
}

export type ReviewQueueInsight = {
  headline: string
  detail: string
  actionHint: string
  reasons: string[]
}

export type ReviewQueueOrder = 'position' | 'random' | 'uncertain' | 'goldsmith' | 'hybrid'

export type ReviewEfficiencyPoint = {
  rank: number
  suggestion_id: string
  sentence_id: string
  sentence_index: number
  cumulative_reviewed: number
  cumulative_disagreements: number
  disagreement: boolean
  route: string
  risk_reason_codes: string[]
}

export type ReviewEfficiencyCurve = {
  order: string
  reviewed_count: number
  disagreement_count: number
  early_reviewed_count: number
  early_disagreement_count: number
  first_disagreement_rank: number | null
  reason_counts: Record<string, number>
  disagreement_reason_counts: Record<string, number>
  points: ReviewEfficiencyPoint[]
}

export type LabelCount = {
  tag_id: string
  name: string
  color: string
  count: number
}

export type DocumentMeta = {
  id: string
  filename: string
  sentence_count: number
  token_count: number
}

export type DocumentListItem = DocumentMeta & {
  completed_count: number
  progress: number
  annotation_count: number
  suggestion_count: number
  current_sentence_index?: number | null
  session_updated_at?: string | null
}

export type AutoMarkMonoglossResponse = {
  marked: number
  tag_id: string
  tag_name: string
  affected_sentence_ids: string[]
  annotation_ids: string[]
}

export type Metrics = {
  sentence_count: number
  completed_count: number
  answer_counts: Record<string, number>
  progress: number
  annotation_count: number
  annotation_overlap_count: number
  suggestion_count: number
  annotation_label_counts: LabelCount[]
  suggestion_label_counts: LabelCount[]
  suggestion_status_counts: Record<string, number>
  suggestion_source_counts: Record<string, number>
  suggestion_confidence_counts: Record<string, number>
  suggestion_review_counts: Record<string, number>
  reviewed_suggestion_count: number
  accuracy: number | null
  accuracy_label: string
  calibration_count: number
  calibration_disagreement_count: number
  calibration_error_rate: number | null
  review_efficiency_curves: Record<string, ReviewEfficiencyCurve>
}

export type AuditSummary = {
  project_id: string
  event_count: number
  pending_outbox_count: number
  invalid_event_count: number
  legacy_event_count: number
  non_replayable_event_count: number
  replay_issue_counts: Record<string, number>
  replay_issues: RebuildIssue[]
  schema_versions: string[]
  event_types: Record<string, number>
  actor_type_counts: Record<string, number>
  actor_id_counts: Record<string, number>
  last_event_type: string | null
  last_event_ts: string | null
  rebuild_status: string
}

export type RebuildIssue = {
  line_number: number
  event_id: string | null
  event_type: string | null
  message: string
}

export type RebuildPreview = {
  project_id: string
  event_count: number
  documents: number
  sentences: number
  tokens: number
  tags: number
  annotations: number
  suggestions: number
  suggestion_reviews: number
  runs: number
  issues: RebuildIssue[]
  ok: boolean
}

export type RuntimeHealth = {
  status: string
  llm_configured: boolean
  llm_model: string | null
  llm_base_host: string | null
}

export type LlmModelOption = {
  id: string
  family: string
  tier: string
  model: string
}

export type LlmSettingsState = {
  configured: boolean
  model: string | null
  base_host: string | null
  selected_model_option_id: string | null
  model_options: LlmModelOption[]
}

export type AnnotationRun = {
  id: string
  project_id: string
  document_id: string
  filename: string
  recipe: string
  config: Record<string, unknown> & { limit_per_sentence?: number }
  input_count: number
  suggestion_count: number
  pending_count: number
  accepted_count: number
  rejected_count: number
  acceptance_rate: number | null
  source_counts: Record<string, number>
  confidence_counts: Record<string, number>
  created_at: string
}

export type SessionState = {
  id: string
  actor_id: string
  current_sentence_index: number | null
  updated_at: string | null
}

export type DocumentPayload = {
  document: DocumentMeta
  tags: TagDef[]
  sentences: SentenceDef[]
  metrics: Metrics
  session: SessionState
}

export type DocumentListPayload = {
  documents: DocumentListItem[]
}

export type DocumentSummaryPayload = {
  document: DocumentMeta
  tags: TagDef[]
  metrics: Metrics
  queue: SentenceQueueItem[]
  session: SessionState
}

export type SentencesPagePayload = {
  sentences: SentenceDef[]
  offset: number
  limit: number
  total: number
  has_more: boolean
}

export type ReviewQueuePayload = {
  items: ReviewQueueItem[]
  total: number
}

export type SamplePreset = {
  id: string
  title: string
  description: string
  filename: string
  language_pair: string
  tag_count: number
  default_limit_per_sentence: number
  default_min_confidence: number
  calibration_candidate_count: number
  auto_accept_on_load: boolean
  complete_sentences_on_load: boolean
}

export type SamplePresetListPayload = {
  presets: SamplePreset[]
}

export type LoadSamplePresetResponse = {
  preset: SamplePreset
  document_id: string
  filename: string
  sentence_count: number
  token_count: number
  tags: TagDef[]
  suggestions_created: number
  suggestion_run_id: string | null
  source_counts: Record<string, number>
  confidence_counts: Record<string, number>
  auto_accepted: number
  auto_accept_skipped: number
  auto_completed: number
  auto_accepted_suggestion_ids: string[]
  auto_completed_sentence_ids: string[]
}

export type ImportTxtResponse = {
  document_id: string
  filename: string
  sentence_count: number
  token_count: number
  tags: TagDef[]
}

export type ProjectResetResponse = {
  project_id: string
  reset_at: string
  deleted_documents: number
  deleted_sentences: number
  deleted_tokens: number
  deleted_annotations: number
  deleted_suggestions: number
  deleted_suggestion_reviews: number
  deleted_runs: number
  deleted_sessions: number
}

export type TxtImportMode = 'replace' | 'merge'

export type ImportAnnotationsResponse = {
  document_id: string
  filename: string
  record_count: number
  matched_count: number
  skipped_count: number
  created_tag_count: number
  created_annotation_count: number
  deleted_annotation_count: number
  completed_sentence_count: number
  source_sha256: string
  tags: TagDef[]
}

export type AnnotationImportSummary = Omit<ImportAnnotationsResponse, 'tags'> & {
  import_filename: string
  tags?: TagDef[]
}

export type AnnotationImportHistoryItem = Omit<ImportAnnotationsResponse, 'tags'> & {
  import_filename?: string
  event_id: string | null
  actor_id: string | null
  ts: string | null
  source_record_results: Record<string, unknown>[]
}

export type AnnotationImportHistoryPayload = {
  imports: AnnotationImportHistoryItem[]
}

export type DragSelection = {
  sentenceId: string
  start: number
  end: number
}

export const fallbackTags: TagDef[] = []
