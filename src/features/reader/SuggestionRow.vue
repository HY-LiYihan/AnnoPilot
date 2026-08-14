<script setup lang="ts">
import { Check, Sparkles, X } from '@lucide/vue'
import type { UiLabels } from '../../i18n'
import type { SuggestionDef, SuggestionReview } from '../../types/domain'

const props = defineProps<{
  labels: UiLabels['reader']
  suggestion: SuggestionDef
  review?: SuggestionReview
  isSaving: boolean
  isReviewing: boolean
  isKeyboardTarget: boolean
  activePosition: number
  totalSuggestions: number
}>()

const emit = defineEmits<{
  target: [suggestion: SuggestionDef]
  review: [suggestion: SuggestionDef]
  accept: [suggestion: SuggestionDef]
  reject: [suggestion: SuggestionDef]
}>()

function suggestionSourceLabel(source: string) {
  return props.labels.sourceLabels[source as keyof typeof props.labels.sourceLabels] ?? source
}

function suggestionRangeLabel() {
  const suggestion = props.suggestion
  const tokenRange =
    suggestion.start_token_index === suggestion.end_token_index
      ? `${props.labels.tokenRange} ${suggestion.start_token_index}`
      : `${props.labels.tokenRange} ${suggestion.start_token_index}-${suggestion.end_token_index}`
  return `${tokenRange} · ${props.labels.charRange} ${suggestion.start_char}-${suggestion.end_char}`
}

function suggestionMatchKeyLabel() {
  const matchKey = props.suggestion.match_key?.trim()
  const evidenceMatchKey = props.suggestion.evidence_match_key?.trim()
  if (!matchKey && !evidenceMatchKey) return ''
  if (matchKey && evidenceMatchKey && matchKey !== evidenceMatchKey) return `${matchKey} → ${evidenceMatchKey}`
  return matchKey || evidenceMatchKey || ''
}

function reviewJudgeLabel() {
  const judge = props.review?.judge
  if (!judge) return ''
  const parts: string[] = []
  if (typeof judge.overall_score === 'number') parts.push(`${props.labels.judgeOverall} ${Math.round(judge.overall_score * 100)}%`)
  if (typeof judge.boundary_score === 'number') parts.push(`${props.labels.judgeBoundary} ${Math.round(judge.boundary_score * 100)}%`)
  const flags = [...(judge.error_types ?? []), ...(judge.risk_flags ?? [])]
  if (flags.length) parts.push(`${props.labels.judgeFlags} ${flags.slice(0, 2).join(', ')}`)
  return parts.join(' · ')
}
</script>

<template>
  <article
    class="suggestion-row"
    :class="{ 'keyboard-target': isKeyboardTarget }"
    :style="{ '--token-color': suggestion.tag_color }"
    @click="emit('target', suggestion)"
  >
    <span>
      <strong>{{ suggestion.text }}</strong>
      <small class="suggestion-meta-line">
        <em class="suggestion-badge">{{ suggestionSourceLabel(suggestion.source) }}</em>
        <em>{{ suggestion.tag_name }}</em>
        <em>{{ Math.round(suggestion.confidence * 100) }}%</em>
        <em>{{ suggestionRangeLabel() }}</em>
        <em v-if="suggestion.run_id">{{ suggestion.run_id.slice(0, 10) }}</em>
        <em v-if="isKeyboardTarget" class="keyboard-target-badge">
          {{ labels.keyboardTarget(activePosition, totalSuggestions) }}
        </em>
      </small>
      <small v-if="suggestion.evidence_text" class="evidence-copy">
        <em>{{ labels.evidence }}</em>
        <strong>{{ suggestion.evidence_text }}</strong>
      </small>
      <small v-if="suggestionMatchKeyLabel()" class="evidence-copy match-key-copy">
        <em>{{ labels.matchKeys }}</em>
        <strong>{{ suggestionMatchKeyLabel() }}</strong>
      </small>
      <small v-if="suggestion.context_before || suggestion.context_after" class="evidence-copy">
        <em>{{ labels.context }}</em>
        <strong>{{ suggestion.context_before }}[{{ suggestion.text }}]{{ suggestion.context_after }}</strong>
      </small>
      <small v-if="review" class="review-copy">
        <em>{{ labels.llmReview }}</em>
        {{ review.recommendation }} · {{ Math.round(review.confidence * 100) }}% · {{ review.rationale }}
      </small>
      <small v-if="reviewJudgeLabel()" class="review-copy judge-copy">
        <em>{{ labels.judgeSignal }}</em>
        {{ reviewJudgeLabel() }}
      </small>
    </span>
    <div class="suggestion-actions">
      <button type="button" :disabled="isSaving || isReviewing" :title="labels.reviewTitle" @click.stop="emit('review', suggestion)">
        <Sparkles :size="15" aria-hidden="true" />
      </button>
      <button type="button" :disabled="isSaving" :title="labels.acceptTitle" @click.stop="emit('accept', suggestion)">
        <Check :size="15" aria-hidden="true" />
      </button>
      <button type="button" :disabled="isSaving" :title="labels.rejectTitle" @click.stop="emit('reject', suggestion)">
        <X :size="15" aria-hidden="true" />
      </button>
    </div>
  </article>
</template>
