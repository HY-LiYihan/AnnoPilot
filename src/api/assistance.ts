import type {
  AssistanceDecisionPayload,
  AssistanceDecisionResponse,
  AssistanceStatus,
} from '../types/domain.ts'
import { responseMessage } from './http.ts'

export class AssistanceApiError extends Error {
  readonly status: number

  constructor(
    message: string,
    status: number,
  ) {
    super(message)
    this.name = 'AssistanceApiError'
    this.status = status
  }
}

async function parseAssistanceResponse<T>(response: Response): Promise<T> {
  if (!response.ok) {
    throw new AssistanceApiError(await responseMessage(response), response.status)
  }
  return response.json() as Promise<T>
}

export async function fetchAssistanceStatus(projectId: string, documentId: string): Promise<AssistanceStatus> {
  const response = await fetch(`/api/projects/${projectId}/documents/${documentId}/assistance`)
  return parseAssistanceResponse<AssistanceStatus>(response)
}

export async function updateAssistanceSettings(projectId: string, documentId: string, enabled: boolean): Promise<AssistanceStatus> {
  const response = await fetch(`/api/projects/${projectId}/documents/${documentId}/assistance/settings`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ enabled }),
  })
  return parseAssistanceResponse<AssistanceStatus>(response)
}

export async function decideAssistance(
  projectId: string,
  sentenceId: string,
  payload: AssistanceDecisionPayload,
): Promise<AssistanceDecisionResponse> {
  const response = await fetch(`/api/projects/${projectId}/sentences/${sentenceId}/assistance/decision`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
  return parseAssistanceResponse<AssistanceDecisionResponse>(response)
}
