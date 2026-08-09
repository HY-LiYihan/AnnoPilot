import type { TagDef } from '../types/domain'
import { parseJsonResponse } from './http'

export async function fetchTags(projectId: string) {
  const response = await fetch(`/api/projects/${projectId}/tags`)
  return parseJsonResponse<{ tags: TagDef[] }>(response)
}

export async function createTag(projectId: string, name: string, description?: string | null, examples: string[] = []) {
  const response = await fetch(`/api/projects/${projectId}/tags`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name, description, examples }),
  })
  return parseJsonResponse<{ tag: TagDef }>(response)
}

export async function renameTag(projectId: string, tagId: string, name: string, description?: string | null, examples?: string[]) {
  const response = await fetch(`/api/projects/${projectId}/tags/${tagId}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name, description, examples }),
  })
  return parseJsonResponse<{ tag: TagDef }>(response)
}

export async function importTagSchema(projectId: string, schema: unknown) {
  const response = await fetch(`/api/projects/${projectId}/tags/schema/import`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(schema),
  })
  return parseJsonResponse<{ created: number; updated: number; skipped: number; content_sha256: string; tags: TagDef[] }>(response)
}

export async function deleteTag(projectId: string, tagId: string) {
  const response = await fetch(`/api/projects/${projectId}/tags/${tagId}`, { method: 'DELETE' })
  return parseJsonResponse<{ deleted: boolean; tag_id: string; annotation_count: number; suggestion_count: number }>(response)
}
