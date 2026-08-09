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
  first_suggestion: SuggestionDef | null
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

export type Metrics = {
  sentence_count: number
  completed_count: number
  answer_counts: Record<string, number>
  progress: number
  annotation_count: number
  suggestion_count: number
  suggestion_status_counts: Record<string, number>
  suggestion_source_counts: Record<string, number>
  suggestion_confidence_counts: Record<string, number>
  accuracy: number | null
  accuracy_label: string
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

export type ImportTxtResponse = {
  document_id: string
  filename: string
  sentence_count: number
  token_count: number
  tags: TagDef[]
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
