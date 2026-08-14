import type { AnnotationDef, SentenceDef, SuggestionDef, TagDef } from '../types/domain'
import { sliceByCodePoint } from '../utils/unicode.ts'

type TokenStyleOptions = {
  activeSuggestion?: SuggestionDef | null
  selectedTag?: TagDef | null
  isTokenInDrag?: (sentence: SentenceDef, tokenIndex: number) => boolean
  isTokenPending?: (sentence: SentenceDef, tokenIndex: number) => boolean
}

export function annotationForToken(sentence: SentenceDef, tokenIndex: number) {
  return sentence.annotations.find(
    (annotation) => annotation.start_token_index <= tokenIndex && annotation.end_token_index >= tokenIndex,
  )
}

export function suggestionForToken(sentence: SentenceDef, tokenIndex: number) {
  if (annotationForToken(sentence, tokenIndex)) return undefined
  return sentence.suggestions.find(
    (suggestion) => suggestion.start_token_index <= tokenIndex && suggestion.end_token_index >= tokenIndex,
  )
}

export function suggestionsWithoutAnnotationOverlaps(suggestions: SuggestionDef[], annotations: AnnotationDef[]) {
  return suggestions.filter(
    (suggestion) =>
      !annotations.some(
        (annotation) =>
          annotation.start_token_index <= suggestion.end_token_index && annotation.end_token_index >= suggestion.start_token_index,
      ),
  )
}

export function tokenPrefix(sentence: SentenceDef, tokenIndex: number) {
  const token = sentence.tokens[tokenIndex]
  if (!token) return ''
  const previousEnd = tokenIndex === 0 ? sentence.start_char : sentence.tokens[tokenIndex - 1]?.end_char ?? sentence.start_char
  return sliceByCodePoint(sentence.text, previousEnd - sentence.start_char, token.start_char - sentence.start_char)
}

export function tokenStyleForToken(
  sentence: SentenceDef,
  tokenIndex: number,
  options: TokenStyleOptions = {},
): Record<string, string> {
  const annotation = annotationForToken(sentence, tokenIndex)
  if (annotation) return { '--token-color': annotation.tag_color }

  const activeSuggestion = options.activeSuggestion
  if (
    activeSuggestion &&
    activeSuggestion.sentence_id === sentence.id &&
    activeSuggestion.start_token_index <= tokenIndex &&
    activeSuggestion.end_token_index >= tokenIndex
  ) {
    return { '--token-color': activeSuggestion.tag_color }
  }

  const suggestion = suggestionForToken(sentence, tokenIndex)
  if (suggestion) return { '--token-color': suggestion.tag_color }

  const isSelected = options.isTokenInDrag?.(sentence, tokenIndex) || options.isTokenPending?.(sentence, tokenIndex)
  if (isSelected && options.selectedTag) return { '--token-color': options.selectedTag.color }

  return {}
}
