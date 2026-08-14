# Runtime Storage

AnnoPilot 当前采用 **SQLite runtime store + JSONL durable event log**。SQLite 用于快速读取和 UI interaction；JSONL 用于 audit trail 和后续 rebuild 基础。

## 当前数据目录

本地开发默认：

```text
.runtime/
  annopilot.sqlite
  annopilot.sqlite-wal
  annopilot.sqlite-shm
  projects/
    default/
      events.jsonl
```

Docker runtime 默认：

```text
/data/
  runtime/
    annopilot.sqlite
  projects/
    <project_id>/
      events.jsonl
```

## SQLite Schema

当前 SQLite schema 主要服务 TXT reader、manual annotation、Character RAG suggestions 和 JSONL rebuild/audit。

Schema lifecycle 由 `backend/app/db/migrations.py` 管理。`schema_version` 记录已经应用的 migration version；baseline table/index SQL 放在 `backend/app/db/schema.py`，`AnnotationStorage` 只在初始化时调用 migration runner，不再直接持有建表 SQL。

```text
schema_version
  version
  name
  applied_at

tags
  id
  project_id
  name
  description
  examples_json
  shortcut
  color

documents
  id
  project_id
  filename
  text
  created_at

sentences
  id
  document_id
  sentence_index
  text
  start_char
  end_char
  completed
  answer

tokens
  id
  sentence_id
  token_index
  text
  start_char
  end_char

annotations
  id
  sentence_id
  tag_id
  start_token_index
  end_token_index
  start_char
  end_char
  text
  source
  source_suggestion_id
  created_at

annotation_suggestions
  id
  run_id
  sentence_id
  tag_id
  start_token_index
  end_token_index
  start_char
  end_char
  text
  confidence
  source
  evidence_text
  match_key
  evidence_match_key
  context_before
  context_after
  status
  created_at

annotation_runs
  id
  project_id
  document_id
  recipe
  config_json
  input_count
  suggestion_count
  created_at

annotation_suggestion_reviews
  id
  suggestion_id
  model
  recommendation
  confidence
  rationale
  context_sha256
  created_at

annotation_sessions
  id
  project_id
  document_id
  actor_id
  current_sentence_index
  updated_at

event_outbox
  id
  project_id
  event_json
  created_at
  flushed_at
```

Indexes：

```text
idx_sentences_document(document_id, sentence_index)
idx_tokens_sentence(sentence_id, token_index)
idx_annotations_sentence(sentence_id, start_token_index)
idx_suggestions_sentence(sentence_id, status, start_token_index)
idx_annotation_runs_project(project_id, document_id, created_at)
idx_suggestion_reviews(suggestion_id, created_at)
idx_annotation_sessions_document(project_id, document_id, updated_at)
idx_event_outbox_pending(project_id, flushed_at, created_at)
```

Mutation path 使用 SQLite outbox：domain rows 和 event payload 在同一个 transaction 中写入，随后 pending outbox rows flush 到 `events.jsonl`。这比“先提交 mutation、再单独写 JSONL”更容易保持 runtime state 和 audit trail 对齐。

`annotation_sessions` 保存 Prodigy-style runtime workflow state，例如默认人工会话当前停留的 sentence index。它用于刷新后恢复 reader 位置，不写入 JSONL audit log，避免普通导航操作污染业务事件流。

Annotation `source` 当前可能是 `human`、`accepted_suggestion`、`auto_monogloss` 或 `prodigy_import`。`source_suggestion_id` 用于追踪由 suggestion accept 创建的 annotation；`auto_monogloss` 表示右侧效率入口为无 annotation、无 pending suggestion 的未完成句自动创建整句 Monogloss span。

## JSONL Event Log

每次关键 mutation 都会 append 到：

```text
<DATA_ROOT>/<project_id>/events.jsonl
```

当前 event examples：

```json
{"type":"document.imported","document_id":"doc_...","filename":"sample.txt","sentence_count":2,"token_count":8,"sentences":[{"id":"sent_...","tokens":[]}]}
{"type":"tag.created","tag_id":"tag_...","name":"地点","description":null,"examples":["桥边","小河"],"shortcut":"4","color":"#7a3db8"}
{"type":"tag.updated","tag_id":"tag_...","old_name":"地点","name":"地名","old_description":null,"description":"地理位置、方位、场所名称。","old_examples":["桥边","小河"],"examples":["村口","山脚"]}
{"type":"annotation.created","annotation_id":"ann_...","sentence_id":"sent_...","tag_id":"verb","start_token_index":2,"end_token_index":2,"text":"reduced","source":"human"}
{"type":"annotation.deleted","annotation_id":"ann_...","sentence_id":"sent_..."}
{"type":"sentence.completed","sentence_id":"sent_...","old_completed":false,"old_answer":"pending","completed":true,"answer":"accept"}
{"type":"annotations.imported","document_id":"doc_...","filename":"annotations.jsonl","record_count":1000,"matched_count":980,"source_sha256":"...","source_record_results":[{"line_number":1,"record_sha256":"...","status":"matched","sentence_id":"sent_...","sentence_index":0,"answer":"accept","created_annotation_count":2}]}
{"type":"suggestions.generated","document_id":"doc_...","sentence_id":"sent_...","run_id":"run_...","recipe":"character_rag","suggestion_count":3,"source_counts":{"lexical_exact":2,"char_ngram":1},"confidence_counts":{"high":2,"medium":1},"config":{"scope":"sentence"},"suggestions":[]}
{"type":"suggestion.accepted","suggestion_id":"sug_...","sentence_id":"sent_..."}
{"type":"suggestion.rejected","suggestion_id":"sug_...","sentence_id":"sent_..."}
{"type":"suggestion.llm_reviewed","suggestion_id":"sug_...","model":"gpt5.5","recommendation":"accept","context_sha256":"..."}
```

实际记录还会包含：

```text
ts
project_id
schema_version
record_type
event_id
actor_type
actor_id
```

`actor_type` 当前使用 `human`、`system`、`llm` 三类：人工导入、tag/annotation/sentence decision 记为 `annopilot-human`；Character RAG 生成建议、由建议落地的 annotation、自动补空白 Monogloss 记为 `annopilot-character-rag`；LLM review 记为对应模型名。Audit summary 会返回 `actor_type_counts` 和 `actor_id_counts`，用于快速检查一份事件日志里人工、系统建议和模型评审的来源比例。

`suggestions.generated` 支持 document scope 和 sentence scope。Sentence scope 用于 UI 的当前句 suggestion action，只清理并替换该句 pending suggestions；document scope 会清理并替换整个 document 的 pending suggestions。事件会保存 `source_counts` 与 `confidence_counts`，因此即使只查看 JSONL audit trail，也能快速判断本次候选主要来自 exact、contains 还是 char n-gram，以及高 / 中 / 低置信度分布。当前置信度 bucket 为 `high >= 0.90`、`medium >= 0.75`、`low < 0.75`。

当前句批量 accept/reject 使用 sentence-scoped endpoints，在一个 SQLite transaction 中更新该句所有 pending suggestions。Accept 仍逐条生成可重放的 `annotation.created` 和 `suggestion.accepted` events；reject 逐条生成 `suggestion.rejected` events，保持 JSONL audit trail 可重放。

`suggestion.llm_reviewed` 保存 `context_sha256`，即发送给 OpenAI-compatible reviewer 的完整结构化 context 的 SHA-256 hash。后续 audit 可以区分“同一个 suggestion + model”与“同一个 suggestion 在变化后的 sentence/tag/annotation context 下重新 review”。

`annotations.imported` 是 batch summary event。JSONL annotation import 产生的可重放状态变化，由同一 import transaction 中逐条写出的 `tag.created`、`annotation.deleted`、`annotation.created` 和 `sentence.completed` events 表达。该 summary event 还包含 `source_record_results`，按源 JSONL 行号记录每条外部记录的 `record_sha256`、匹配状态、目标 sentence、answer、创建/删除 annotation 数量，以及可用的 Prodigy-style `_view_id` / `_session_id` / `_annotator_id` / hash metadata，方便不保存原始导入全文也能定位审计来源。

## Import JSONL

当前 backend 支持把 Prodigy / AnnoPilot style annotations JSONL 导入到已经存在的 document：

```text
POST /api/projects/{project_id}/documents/{document_id}/import-annotations-jsonl
```

导入规则：

- 文件必须是 UTF-8 `.jsonl`，大小上限为 10 MB。
- 句子优先按 `sentence_id` 匹配，其次可按 `sentence_index` 或 sentence text 匹配。
- Span 可使用 Prodigy 的 `token_start` / `token_end`，也可使用 character offsets。
- 如果导入 label 不存在，会自动创建 tag，description 标记为 `Imported from Prodigy/AnnoPilot JSONL.`。
- 匹配到的 sentence 会先清除旧 annotations，再按导入 spans 写入 `source=prodigy_import` annotations。
- 导入结果返回 `record_count`、`matched_count`、`skipped_count`、created/deleted counts 和 `source_sha256`；event log 的 `annotations.imported.source_record_results` 会保存逐行匹配 manifest。

Frontend 在右侧 metrics/export panel 暴露 `Import JSONL` 入口，可把外部 review 后的 Prodigy / AnnoPilot JSONL 导回当前 document，和 Prodigy export 形成 round-trip workflow。`GET /api/projects/{project_id}/annotation-imports` 会直接从 `events.jsonl` 派生最近导入历史，因此刷新页面后仍能恢复最近一次导入摘要，而不需要额外 runtime 表。

## Export JSONL

当前 document export endpoint：

```text
GET /api/projects/{project_id}/documents/{document_id}/summary
GET /api/projects/{project_id}/documents?limit=50
GET /api/projects/{project_id}/documents/{document_id}/sentences?offset=0&limit=50
GET /api/projects/{project_id}/documents/{document_id}/review-queue?limit=20&order=position|random|uncertain|goldsmith|hybrid
POST /api/projects/{project_id}/documents/{document_id}/session/cursor
POST /api/projects/{project_id}/documents/{document_id}/suggestions/run
POST /api/projects/{project_id}/documents/{document_id}/suggestions/auto-annotate
POST /api/projects/{project_id}/documents/{document_id}/suggestions/auto-accept
POST /api/projects/{project_id}/documents/{document_id}/suggestions/auto-reject
POST /api/projects/{project_id}/documents/{document_id}/suggestions/apply-llm-review
POST /api/projects/{project_id}/sentences/{sentence_id}/suggestions/accept
POST /api/projects/{project_id}/sentences/{sentence_id}/suggestions/reject
POST /api/projects/{project_id}/sentences/{sentence_id}/suggestions/llm-review
POST /api/projects/{project_id}/sentences/{sentence_id}/suggestions/apply-llm-review
GET /api/projects/{project_id}/documents/{document_id}/export.jsonl
GET /api/projects/{project_id}/documents/{document_id}/export.prodigy.jsonl
GET /api/projects/{project_id}/documents/{document_id}/export.prodigy.spans.jsonl
GET /api/projects/{project_id}/documents/{document_id}/export.prodigy.bundle.zip
GET /api/projects/{project_id}/documents/{document_id}/export.manifest.json
GET /api/projects/{project_id}/runs/{run_id}/provenance.json
GET /api/projects/{project_id}/events.jsonl
GET /api/projects/{project_id}/tags/schema.json
GET /api/projects/{project_id}/tags/prodigy-labels.json
```

Product UI 使用 `summary` 读取全局 metrics、tags 和 sentence-dot state，使用 `sentences` 读取 paged reader window，使用 `review-queue` 读取右侧待审任务列表。Summary metrics 会输出 document-level `annotation_label_counts` 和 live pending `suggestion_label_counts`，同时输出 `suggestion_status_counts`、LLM latest review 的 `suggestion_review_counts` / `reviewed_suggestion_count`，并对当前可处理的 pending suggestions 输出 `suggestion_source_counts` 和 `suggestion_confidence_counts`，因此右侧运行状态无需加载全文 sentence window 也能展示 Engagement label coverage、待审队列质量和评审分布。Review queue 支持 `order=position`、`order=random`、`order=uncertain`、`order=goldsmith` 和 `order=hybrid`；`random` 提供稳定伪随机 baseline，`uncertain` 按句内最低 Character RAG confidence 优先，`goldsmith` 按词面低置信、句内候选密度、latest LLM review 风险、Rosetta-style judge 风险和候选标签/边界冲突综合优先，`hybrid` 保留高风险优先并插入少量高置信且无 LLM / judge 风险的 `calibration` 抽检样本。队列 item 返回 `min_confidence`、`lexical_risk_score`、`llm_review_risk_score`、`judge_review_risk_score`、`candidate_disagreement_score`、`risk_score`、`risk_reason_codes`、`review_route` 和兼容字段 `priority_score=min_confidence`，其中 latest LLM review 为 `reject` / `uncertain` 的 pending suggestions 会提高 `llm_review_risk_score`，latest `judge` 中低 `overall_score` / `boundary_score`、高 `missed_span_risk` / `extra_span_risk`、`needs_review`、`error_types` 或 `risk_flags` 会提高 `judge_review_risk_score`，同句候选之间的 label 或 boundary 冲突会提高 `candidate_disagreement_score`。UI 在 `goldsmith` / `hybrid` 风险项显示综合 `risk_score`、risk breakdown 和 `risk_reason_codes` 对应的原因 chips，在 `hybrid` 抽检项显示校准置信度。旧的 full document endpoint 仍保留，用于兼容和 export-adjacent workflows。

Task JSONL 输出以 sentence 为粒度，每行包含：

```text
schema_version
record_type
document_id
sentence_id
sentence_index
text
tokens
spans
annotations
suggestions
answer
completed
_view_id
_session_id
_annotator_id
_input_hash
_task_hash
meta
```

这些 `_...` 字段与 Prodigy task metadata 兼容，用于稳定去重、session tracing 和 annotator/source audit；`meta.session_id` 与 `meta.annotator_id` 同步保留一份非下划线 provenance，方便非 Prodigy 消费方读取。Prodigy export 会把已经含有 annotations 的 pending sentence 顶层 `answer` 映射为 `accept`，确保自动接受的 Character RAG spans 能直接作为有效标注使用；runtime `meta.answer` 仍保留原始句子状态，方便区分“已标注”和“人工完成”。LLM review context 会附带 `boundary_feedback`，把同项目、同标签、已由人工 accept/reject 的历史 suggestions、latest LLM review 为 `reject` 的 pending suggestions、以及 latest LLM review 为 `uncertain` 的 pending boundary cases 带入当前候选复核；review 可把 Rosetta-style judge scores / risk flags 存入 `judge_json` 并进入 JSONL event、rebuild、run provenance 和 Goldsmith exports。pending reject 会进入 negative examples 和 hard examples，pending uncertain 只作为 hard example，不作为负例。hard example reasons 与 Goldsmith hard-example export 保持一致，覆盖 LLM/人工分歧、人工拒绝、pending LLM reject、低 Character RAG confidence 和 LLM uncertain。

Document summary / manifest 的 `metrics` 包含 `answer_counts`，用于直接区分 `accept`、`reject`、`ignore` 和 `pending` 句子数量；同时包含 `annotation_label_counts`、live pending `suggestion_label_counts`、suggestion 的 pending / accepted / rejected 状态分布、LLM review 的 accept / reject / uncertain 推荐分布、human-calibrated `calibration_error_rate`，以及 live pending suggestion 的来源分布和置信度分布，供 UI 和导出 manifest 判断当前 Engagement label coverage 与复核队列质量。Manifest 还会写入独立 `prodigy_readiness`，把句子完成度、span 数、label 覆盖、pending suggestions 和 Prodigy schema versions 汇总成 `ready/status/blockers`，便于导出归档时机器判断是否仍需人工复核或补标。`calibration_error_rate` 只统计已有 human accept/reject 决策且有 latest LLM review 的 suggestions，用来估计 routing / judge signal 错配率，而不是把 LLM judge score 当成真实 accuracy。`review_efficiency_curves` 会基于同一批 human-calibrated suggestions，按 position、stable random、uncertainty、Goldsmith risk 和 hybrid queue order 生成累计错配发现曲线；Goldsmith / hybrid 风险排序会把候选冲突风险、Rosetta-style judge risk 和低置信候选密度纳入 `risk_score`，因此同一句中多个模型候选互相争夺 label 或 span boundary 的样本会更早被复核。每条 curve 还会输出 `reason_counts` 与 `disagreement_reason_counts`，每个 point 保留 `risk_reason_codes`，用于判断哪类风险最常触发人工纠错；导出包同时包含 `annopilot.goldsmith_risk_reasons.v1`，把这些原因与 review queue、hard examples、boundary feedback 聚合成独立 risk reason summary JSONL。右侧运行状态面板用前 5 条错配发现数快速比较 risk routing 与 random baseline 的复核效率。Product UI 当前用 `Enter` / `Space` / `J` / `E` 分别写入 accept、ignore、reject 和 reopen-to-pending。

Prodigy JSONL 提供 `ner_manual` 和 `spans_manual` 两种兼容导出，并写入稳定的 `_session_id`、`_annotator_id`、`_input_hash`、`_task_hash` 和 `_view_id`。两种导出都使用相对于当前 sentence `text` 的 token / span offsets；额外的 AnnoPilot provenance 放在 `meta.annotation_sources`，避免污染标准 `spans` 字段。每条 Prodigy record 还会在 `meta.tag_schema` 携带当前 schema hash、label definitions、examples、shortcut 和颜色，让外部 Prodigy/审阅流程能看到当时的 Engagement 标签含义。Prodigy bundle ZIP 会同时包含 `ner_manual` JSONL、`spans_manual` JSONL、labels config、tag schema、Goldsmith review queue、verification report、bootstrap report、README 和 manifest，zip 内 artifact bytes 与 manifest 中的 `sha256` 保持一致。Goldsmith review queue JSONL 会导出当前待人工复核队列，并保留 `lexical_risk_score`、`llm_review_risk_score`、`judge_review_risk_score`、`candidate_disagreement_score`、综合 `risk_score`、`risk_reason_codes`、route 和首条 suggestion，可作为 Rosetta 风格 `human_review_queue.jsonl`；Goldsmith human choices JSONL 会导出人工 accept/reject 过的 suggestions、latest LLM review、错配标记和 `risk_reason_codes`，可作为 `human_choices.jsonl` 进入离线优化/评估；Goldsmith hard examples JSONL 会进一步筛出人工拒绝、LLM/人工分歧、低置信或 LLM uncertain 的样本，并写入 `failure_note` 与 `risk_reason_codes`，用作下一轮 label definition / bilingual examples / negative examples 的边界反馈；Goldsmith boundary feedback JSONL 会把这批 human hard examples 与仍 pending 但 latest LLM review 为 `reject` / `uncertain` 的候选合并导出，并保留 `risk_reason_codes`，便于在人工最终处理前也能沉淀 judge feedback；Goldsmith consistency scores JSONL 会对当前可见 pending suggestions 输出 `pairwise_span_f1`、Rosetta-compatible `exact_match_rate`（与第一条候选完全一致的比例）、`consensus_match_rate`（多数共识比例）、`average_model_confidence`、`uncertainty_score` 和 `rosetta_route=high|medium|low` 等字段，同时保留 AnnoPilot 的 review route、risk 和 candidate_scores；Goldsmith candidate runs JSONL 会以 Rosetta `rosetta.prodigy_candidate.v1` 输出候选标注、inline markup、Prodigy-style span offsets、Rosetta `high/medium/low` route、uncertainty score、candidate_score 和句内 consistency 摘要，作为 `candidate_runs.jsonl` 或候选选择题式人工复核输入；Goldsmith label statistics JSONL 会按 Rosetta `label_statistics` 思路导出 token-level entity / context / other 计数、概率和 label entity counts，用于优化 bilingual lexical examples、negative examples 和边界规则；Goldsmith contrastive examples JSONL 会按 Rosetta `contrastive_retrieval` 思路，为每条已标注 sentence 挑选 lexical overlap 最高的 similar examples 与最低的 boundary examples，作为后续 prompt / guideline calibration 的对比例子；Goldsmith reflection plans JSONL 会按 Rosetta `reflection.py` 思路输出 possible false negative、boundary token 和 unseen token 复核项；Goldsmith prompt package JSONL 会按 Rosetta `prompting.py` 风格把 review task、tag schema、context examples、reflection items、候选选项和输出契约组合成可交给 LLM 或专家的 prompt task，并列出 `verifier.py` 风格检查项；Goldsmith verification report JSONL 会按 Rosetta `verifier.py` 思路汇总 Prodigy offsets、candidate spans、review task markup 和 prompt contract 的导出前检查结果；Goldsmith bootstrap report Markdown 会按 Rosetta `bootstrap_report.py` 思路汇总进度、review routes、hybrid queue、top entity tokens、reflection items、verification 状态和推荐人工动作，作为 bundle 中优先阅读的人工导航文件；Goldsmith review tasks JSONL 会进一步把 low / medium route 的同句候选聚合成 `annopilot.goldsmith_review_tasks.v1` / Rosetta-style `human_review_task`，包含 prompt、priority、选项 A/B/C、每个候选的 `action_hint`、任务级 `review_guidance` 和 `manual_option_id=__manual__`，便于直接交给人工复核。`review_guidance` 会输出 Engagement 复核目标、primary action、候选 label/boundary 冲突摘要、稳定 risk reason codes 和边界检查清单。同一批信号以及 pending LLM reject 信号也会在线进入 `boundary_feedback`，辅助后续同标签 LLM review。Tag schema JSON 独立导出 label 名称、准则说明和 Character RAG 词面种子，并包含不受 `generated_at` 影响的 `content_sha256`。每次 Character RAG run 的 `config` 都记录 `tag_schema_version`、`tag_schema_sha256`、`examples_by_tag`、`examples_sha256`、`negative_examples_by_tag`、`negative_examples_sha256` 和 retrieval 规则；负例策略为 `human_rejected_or_latest_llm_reject`，并通过 `negative_example_source_counts` 区分人工 rejected 与 LLM rejected 来源；重新运行 suggestions 时只清理未 review pending suggestions，`pending_suggestion_clear_policy` 会记录该行为。当前 retrieval 会按 token offsets 还原英文词间空格，再做 Unicode NFKC、quote/dash/slash folding、casefold 和 whitespace normalization，并移除中文字符之间的抽取空格后执行 exact / contains / char-ngram 匹配，因此全角数字、全角标点、连字符/斜杠 cue、curly apostrophe 和半角证据词面可以共用同一套 examples；suggestion text 和 char offsets 仍保留原始文本。每条 suggestion 会持久化 `evidence_text`、`match_key`、`evidence_match_key`、`context_before` / `context_after`，既保存原始证据词面，也保存真正参与匹配的归一化键；上下文只保存当前句内候选 span 附近的小窗口，供 UI、单条 LLM review、当前句批量 LLM review、应用 LLM recommendation、JSONL event、export 和 run provenance 共同审计；LLM review context 还会附带 `span_context`，用方括号直接高亮候选 span。Run list 和 run provenance 都会输出 `source_counts` 与 `confidence_counts`，用于审计 exact / contains / char-ngram 的候选来源分布和高 / 中 / 低置信度分布。Run config 还会保存 `match_normalization`、`examples_match_keys_by_tag`、`negative_examples_match_keys_by_tag` 及对应 hash，让审计者能同时看到原始词面和真正参与匹配的归一化键。Run provenance JSON 独立记录 run config、状态计数、来源分布、置信度分布、LLM review 计数和每条 suggestion 的 evidence/status/latest review；已接受或拒绝的 suggestion 还会包含 `decision_event`，指向 JSONL audit log 中对应的 `suggestion.accepted` / `suggestion.rejected` event id、时间戳和 actor 信息。该文件带不受 `generated_at` 影响的 `content_sha256`。Manifest JSON 记录 tasks/prodigy/events/tag-schema artifacts、Goldsmith review artifacts、run provenance artifact summaries、source run ids、annotation import history、annotation source counts 和 `event_audit` 聚合，用于归档和复现，并写入排除 manifest `generated_at` 后的稳定 `content_sha256`；artifact `sha256` 表示本次导出字节，带 `generated_at` 的 JSON artifact 还会提供稳定的 `content_sha256` 用于判断真实内容是否变化。

## 当前边界

当前 JSONL 已作为 audit trail，并支持 non-destructive rebuild preview 与 CLI rebuild。Replay validation 和 apply 逻辑集中在 `backend/app/events/replay.py`，避免 audit preview、CLI rebuild 和 `AnnotationStorage` facade 各自维护一套事件规则。当前已经可重放的核心事件包括 document import snapshot、tag mutations、annotation mutations、sentence decisions、suggestion runs、suggestion decisions 和 LLM review snapshots。长期目标是：

- SQLite 可从 JSONL artifacts rebuild。
- Import、annotation、suggestion、LLM review 和后续 batch run 都有可重放 event。
- Export manifest 记录 source run ids、annotation import history、schema version、artifact hashes、`prodigy_readiness`、`prodigy_labels_json`、event actor/type audit summary、timestamp 和稳定 content hash。
- Project data 可以作为 portable artifact 迁移或归档。
