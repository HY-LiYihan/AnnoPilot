import type { AnnotationDef } from '../types/domain'

export type AnnotationConflictPair = {
  id: string
  left: AnnotationDef
  right: AnnotationDef
}

export type ConflictResolutionMode = 'narrower' | 'wider'

export function buildAnnotationConflictPairs(annotations: AnnotationDef[]) {
  const pairs: AnnotationConflictPair[] = []
  for (let leftIndex = 0; leftIndex < annotations.length; leftIndex += 1) {
    const left = annotations[leftIndex]
    for (let rightIndex = leftIndex + 1; rightIndex < annotations.length; rightIndex += 1) {
      const right = annotations[rightIndex]
      if (left.start_token_index <= right.end_token_index && left.end_token_index >= right.start_token_index) {
        pairs.push({ id: `${left.id}:${right.id}`, left, right })
      }
    }
  }
  return pairs
}

export function conflictResolutionAnnotation(pair: AnnotationConflictPair, mode: ConflictResolutionMode) {
  const comparison = compareSpanSize(pair.left, pair.right)
  if (mode === 'narrower') return comparison <= 0 ? pair.left : pair.right
  return comparison >= 0 ? pair.left : pair.right
}

export function conflictResolutionAnnotationIds(pairs: AnnotationConflictPair[], mode: ConflictResolutionMode) {
  return Array.from(new Set(pairs.map((pair) => conflictResolutionAnnotation(pair, mode).id)))
}

function spanTokenLength(annotation: AnnotationDef) {
  return annotation.end_token_index - annotation.start_token_index + 1
}

function spanCharLength(annotation: AnnotationDef) {
  return annotation.end_char - annotation.start_char
}

function compareSpanSize(left: AnnotationDef, right: AnnotationDef) {
  const tokenDelta = spanTokenLength(left) - spanTokenLength(right)
  if (tokenDelta) return tokenDelta
  const charDelta = spanCharLength(left) - spanCharLength(right)
  if (charDelta) return charDelta
  return left.created_at.localeCompare(right.created_at)
}
