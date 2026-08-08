import type { AnnotationDef } from '../types/domain'
import { parseJsonResponse, responseMessage } from './http'

export async function createAnnotation(
  projectId: string,
  sentenceId: string,
  tagId: string,
  startTokenIndex: number,
  endTokenIndex: number,
) {
  const response = await fetch(`/api/projects/${projectId}/sentences/${sentenceId}/annotations`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      tag_id: tagId,
      start_token_index: startTokenIndex,
      end_token_index: endTokenIndex,
    }),
  })
  return parseJsonResponse<{ annotations: AnnotationDef[] }>(response)
}

export async function deleteAnnotation(projectId: string, annotationId: string) {
  const response = await fetch(`/api/projects/${projectId}/annotations/${annotationId}`, { method: 'DELETE' })
  if (!response.ok) throw new Error(await responseMessage(response))
  return response.json() as Promise<{ deleted: boolean }>
}
