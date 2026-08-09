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

Annotation `source` 当前可能是 `human`、`accepted_suggestion` 或 `prodigy_import`。`source_suggestion_id` 用于追踪由 suggestion accept 创建的 annotation。

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
{"type":"annotations.imported","document_id":"doc_...","filename":"annotations.jsonl","record_count":1000,"matched_count":980,"source_sha256":"..."}
{"type":"suggestions.generated","document_id":"doc_...","sentence_id":"sent_...","run_id":"run_...","recipe":"character_rag","config":{"scope":"sentence"},"suggestions":[]}
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

`actor_type` 当前使用 `human`、`system`、`llm` 三类：人工导入、tag/annotation/sentence decision 记为 `annopilot-human`；Character RAG 生成建议和由建议落地的 annotation 记为 `annopilot-character-rag`；LLM review 记为对应模型名。Audit summary 会返回 `actor_type_counts` 和 `actor_id_counts`，用于快速检查一份事件日志里人工、系统建议和模型评审的来源比例。

`suggestions.generated` 支持 document scope 和 sentence scope。Sentence scope 用于 UI 的当前句 suggestion action，只清理并替换该句 pending suggestions；document scope 会清理并替换整个 document 的 pending suggestions。

当前句批量 accept/reject 使用 sentence-scoped endpoints，在一个 SQLite transaction 中更新该句所有 pending suggestions。Accept 仍逐条生成可重放的 `annotation.created` 和 `suggestion.accepted` events；reject 逐条生成 `suggestion.rejected` events，保持 JSONL audit trail 可重放。

`suggestion.llm_reviewed` 保存 `context_sha256`，即发送给 OpenAI-compatible reviewer 的完整结构化 context 的 SHA-256 hash。后续 audit 可以区分“同一个 suggestion + model”与“同一个 suggestion 在变化后的 sentence/tag/annotation context 下重新 review”。

`annotations.imported` 是 batch summary event。JSONL annotation import 产生的可重放状态变化，由同一 import transaction 中逐条写出的 `tag.created`、`annotation.deleted`、`annotation.created` 和 `sentence.completed` events 表达。

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
- 导入结果返回 `record_count`、`matched_count`、`skipped_count`、created/deleted counts 和 `source_sha256`。

Frontend 在右侧 metrics/export panel 暴露 `Import JSONL` 入口，可把外部 review 后的 Prodigy / AnnoPilot JSONL 导回当前 document，和 Prodigy export 形成 round-trip workflow。

## Export JSONL

当前 document export endpoint：

```text
GET /api/projects/{project_id}/documents/{document_id}/summary
GET /api/projects/{project_id}/documents?limit=50
GET /api/projects/{project_id}/documents/{document_id}/sentences?offset=0&limit=50
GET /api/projects/{project_id}/documents/{document_id}/review-queue?limit=20&order=position
POST /api/projects/{project_id}/documents/{document_id}/session/cursor
POST /api/projects/{project_id}/sentences/{sentence_id}/suggestions/accept
POST /api/projects/{project_id}/sentences/{sentence_id}/suggestions/reject
GET /api/projects/{project_id}/documents/{document_id}/export.jsonl
GET /api/projects/{project_id}/documents/{document_id}/export.prodigy.jsonl
GET /api/projects/{project_id}/documents/{document_id}/export.manifest.json
GET /api/projects/{project_id}/runs/{run_id}/provenance.json
GET /api/projects/{project_id}/events.jsonl
GET /api/projects/{project_id}/tags/schema.json
```

Product UI 使用 `summary` 读取全局 metrics、tags 和 sentence-dot state，使用 `sentences` 读取 paged reader window，使用 `review-queue` 读取右侧待审任务列表。Review queue 支持 `order=position` 和 `order=uncertain`，后者按句内最低 Character RAG confidence 优先。旧的 full document endpoint 仍保留，用于兼容和 export-adjacent workflows。

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
meta
```

Document summary / manifest 的 `metrics` 包含 `answer_counts`，用于直接区分 `accept`、`reject`、`ignore` 和 `pending` 句子数量。Product UI 当前用 `Enter` / `Space` / `J` / `E` 分别写入 accept、ignore、reject 和 reopen-to-pending。

Prodigy JSONL 保持 `ner_manual` 兼容结构，并写入稳定的 `_session_id`、`_annotator_id`、`_input_hash`、`_task_hash` 和 `_view_id`。额外的 AnnoPilot provenance 放在 `meta.annotation_sources`，避免污染标准 `spans` 字段。Tag schema JSON 独立导出 label 名称、准则说明和 Character RAG 词面种子，并包含不受 `generated_at` 影响的 `content_sha256`。每次 Character RAG run 的 `config` 都记录 `tag_schema_version`、`tag_schema_sha256`、`examples_by_tag`、`examples_sha256`、`negative_examples_by_tag`、`negative_examples_sha256` 和 retrieval 规则；当前 retrieval 会按 token offsets 还原英文词间空格，再做 casefold + whitespace normalization 后执行 exact / contains / char-ngram 匹配，suggestion text 和 char offsets 仍保留原始文本。每条 suggestion 会持久化 `context_before` / `context_after`，只保存当前句内候选 span 附近的小窗口，供 UI、LLM review、JSONL event、export 和 run provenance 共同审计；LLM review context 还会附带 `span_context`，用方括号直接高亮候选 span。Run config 还会保存 `match_normalization`、`examples_match_keys_by_tag`、`negative_examples_match_keys_by_tag` 及对应 hash，让审计者能同时看到原始词面和真正参与匹配的归一化键。Run provenance JSON 独立记录 run config、状态计数、LLM review 计数和每条 suggestion 的 evidence/status/latest review；已接受或拒绝的 suggestion 还会包含 `decision_event`，指向 JSONL audit log 中对应的 `suggestion.accepted` / `suggestion.rejected` event id、时间戳和 actor 信息。该文件带不受 `generated_at` 影响的 `content_sha256`。Manifest JSON 记录 tasks/prodigy/events/tag-schema artifacts、run provenance artifact summaries、source run ids、annotation source counts 和 `event_audit` 聚合，用于归档和复现；artifact `sha256` 表示本次导出字节，带 `generated_at` 的 JSON artifact 还会提供稳定的 `content_sha256` 用于判断真实内容是否变化。

## 当前边界

当前 JSONL 已作为 audit trail，并支持 non-destructive rebuild preview 与 CLI rebuild。当前已经可重放的核心事件包括 document import snapshot、tag mutations、annotation mutations、sentence decisions、suggestion runs、suggestion decisions 和 LLM review snapshots。长期目标是：

- SQLite 可从 JSONL artifacts rebuild。
- Import、annotation、suggestion、LLM review 和后续 batch run 都有可重放 event。
- Export manifest 记录 source run ids、schema version、artifact hashes、event actor/type audit summary 和 timestamp。
- Project data 可以作为 portable artifact 迁移或归档。
