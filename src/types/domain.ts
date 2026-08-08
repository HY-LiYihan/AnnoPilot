export const PROJECT_ID = 'default'
export const ACTIVE_DOCUMENT_KEY = 'annopilot.activeDocumentId'

export type TagDef = {
  id: string
  name: string
  shortcut: string
  color: string
  count: number
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
  created_at: string
}

export type SentenceDef = {
  id: string
  index: number
  text: string
  start_char: number
  end_char: number
  completed: boolean
  tokens: TokenDef[]
  annotations: AnnotationDef[]
}

export type DocumentMeta = {
  id: string
  filename: string
  sentence_count: number
  token_count: number
}

export type Metrics = {
  sentence_count: number
  completed_count: number
  progress: number
  annotation_count: number
  accuracy: number | null
  accuracy_label: string
}

export type DocumentPayload = {
  document: DocumentMeta
  tags: TagDef[]
  sentences: SentenceDef[]
  metrics: Metrics
}

export type ImportTxtResponse = {
  document_id: string
  filename: string
  sentence_count: number
  token_count: number
  tags: TagDef[]
}

export type DragSelection = {
  sentenceId: string
  start: number
  end: number
}

export const fallbackTags: TagDef[] = [
  { id: 'noun', name: '名词', shortcut: '1', color: '#0b7565', count: 0 },
  { id: 'verb', name: '动词', shortcut: '2', color: '#326bd8', count: 0 },
  { id: 'adjective', name: '形容词', shortcut: '3', color: '#c45a2e', count: 0 },
]
