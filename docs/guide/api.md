# API Surface

本文记录当前 repo 中已经实现的 FastAPI endpoints。所有 project-scoped API 当前都使用 `project_id`，产品 UI 默认使用 `default` project。

## Health

```text
GET /api/health
```

返回 API 存活状态和非敏感 LLM runtime 信息：`llm_configured`、`llm_model`、`llm_base_host`。不会返回 `LLM_API_KEY`。

## Documents

```text
POST /api/projects/{project_id}/import-txt
POST /api/projects/{project_id}/documents/{document_id}/import-annotations-jsonl
GET  /api/projects/{project_id}/documents?limit=50
GET  /api/projects/{project_id}/documents/{document_id}
GET  /api/projects/{project_id}/documents/{document_id}/summary
GET  /api/projects/{project_id}/documents/{document_id}/sentences?offset=0&limit=50
GET  /api/projects/{project_id}/documents/{document_id}/review-queue?limit=20&order=position
GET  /api/projects/{project_id}/annotation-imports?document_id={document_id}&limit=5
POST /api/projects/{project_id}/documents/{document_id}/session/cursor
POST /api/projects/{project_id}/sentences/{sentence_id}/complete
```

- `import-txt` 接收 UTF-8 `.txt`，当前大小上限为 10 MB。
- `import-annotations-jsonl` 接收 Prodigy / AnnoPilot style `.jsonl` annotation records；frontend 在右侧 metrics/export panel 暴露 `Import JSONL` 入口。
- `documents` 返回最近 runtime documents 的轻量索引，包含 progress、annotation count 和 pending suggestion count，供 frontend 切换已导入文档。
- `summary` 返回 document meta、tags、metrics、全局 sentence queue 和当前 runtime session cursor，不返回完整 tokens；metrics 会包含 suggestion 的 `suggestion_status_counts`、`suggestion_review_counts` / `reviewed_suggestion_count`，以及 pending suggestion 的 `suggestion_source_counts` / `suggestion_confidence_counts`，用于快速判断当前待审队列质量、LLM 评审分布和已处理建议分布。
- `sentences` 返回分页 sentence window，当前 API limit 上限为 200。
- `review-queue` 返回未完成且存在 pending suggestions 的句子列表，以及每句第一条候选，供 UI 快速跳转待审任务；`order=uncertain` 会按最低 Character RAG confidence 优先排序。
- `annotation-imports` 从 `events.jsonl` 读取最近的 JSONL annotation import history；frontend 用它在刷新页面后恢复最近导入摘要。
- `session/cursor` 保存默认人工会话的当前句位置，用于刷新后恢复标注阅读器状态；该状态保存在 SQLite runtime，不写入 JSONL audit log。
- `complete` 写入 `completed` 和 Prodigy-compatible `answer`，当前支持 `accept`、`ignore`、`reject`，以及 `completed=false` reopen 回 `pending`。

## Sample Presets

```text
GET  /api/projects/{project_id}/sample-presets
POST /api/projects/{project_id}/sample-presets/{preset_id}/load
```

- `sample-presets` 返回后端内置的轻量样例索引；当前包含通用、新闻/政策、学术/方法三类 bilingual Engagement 样例。
- `load` 会导入对应 label schema、导入 TXT 文档，并默认运行一次高置信 Character RAG suggestions；响应返回 document id、tag 列表、sentence/token 数量、suggestion run id 和本次候选统计。
- 该接口不改变现有手动 `tags/schema/import`、`import-txt` 或 `suggestions/run` API，只是把演示/测试工作流合成一个快捷入口。

## Tags

```text
GET    /api/projects/{project_id}/tags
POST   /api/projects/{project_id}/tags
POST   /api/projects/{project_id}/tags/schema/import
PATCH  /api/projects/{project_id}/tags/{tag_id}
DELETE /api/projects/{project_id}/tags/{tag_id}
```

Tags 是 project-level label schema。每个 tag 包含 `id`、`name`、`description`、`examples`、`shortcut`、`color`，并返回 `usage_count` 和 `suggestion_count` 方便 UI 在删除前提示影响范围。

## Annotations

```text
POST   /api/projects/{project_id}/sentences/{sentence_id}/annotations
DELETE /api/projects/{project_id}/annotations/{annotation_id}
```

Annotation create 使用 token range：`tag_id`、`start_token_index`、`end_token_index`。Backend 根据 sentence tokens 计算 document-level `start_char` / `end_char`，并把 mutation 写入 SQLite 和 JSONL event log。

## Suggestions

```text
POST /api/projects/{project_id}/documents/{document_id}/suggestions/run
POST /api/projects/{project_id}/documents/{document_id}/sentences/{sentence_id}/suggestions/run
POST /api/projects/{project_id}/documents/{document_id}/suggestions/auto-accept
POST /api/projects/{project_id}/documents/{document_id}/suggestions/auto-annotate
POST /api/projects/{project_id}/documents/{document_id}/suggestions/auto-reject
POST /api/projects/{project_id}/documents/{document_id}/suggestions/apply-llm-review
POST /api/projects/{project_id}/suggestions/{suggestion_id}/accept
POST /api/projects/{project_id}/suggestions/{suggestion_id}/reject
POST /api/projects/{project_id}/sentences/{sentence_id}/suggestions/accept
POST /api/projects/{project_id}/sentences/{sentence_id}/suggestions/reject
POST /api/projects/{project_id}/suggestions/{suggestion_id}/llm-review
POST /api/projects/{project_id}/sentences/{sentence_id}/suggestions/llm-review
POST /api/projects/{project_id}/sentences/{sentence_id}/suggestions/apply-llm-review
```

- Suggestions 当前由 Character RAG 生成，支持 document scope 和 sentence scope。
- `limit_per_sentence` 默认 6，上限 20；`min_confidence` 取值范围 0 到 1。
- Generate / auto-annotate response 会返回 `source_counts` 和 `confidence_counts`，分别汇总本次 run 的候选来源分布与置信度分布；置信度 bucket 为 `high >= 0.90`、`medium >= 0.75`、`low < 0.75`。
- `auto-annotate` 会先运行 Character RAG suggestions，再按同一 `min_confidence` 自动接受高置信 span，形成一键低算力自动标注 slice。
- Suggestion payload 保留 `evidence_text`、`match_key` 和 `evidence_match_key`，用于审计字符规则实际命中的原始证据与归一化匹配键。
- Accept 会创建 `source=accepted_suggestion` annotation，并把 suggestion 状态改为 `accepted`。
- Reject 会把 suggestion 状态改为 `rejected`，后续 Character RAG 会把同 tag + text 当作 negative example。
- Sentence-level accept/reject 会在一个 SQLite transaction 中批量处理当前句 pending suggestions，UI 的 `A` / `X` 快捷键走这组 endpoint。
- LLM review 使用 OpenAI-compatible `/chat/completions`，返回 `recommendation`、`confidence`、`rationale` 和 `context_sha256`；sentence-scoped endpoint 会批量评审当前句仍 pending 且未被已有 annotation 覆盖的 suggestions。
- `apply-llm-review` 支持 document scope 和 sentence scope，会在一个 SQLite transaction 中应用 latest LLM recommendations：`accept` 创建 `accepted_suggestion` annotation，`reject` 更新 suggestion 状态，`uncertain` 或未评审 suggestions 保持 pending。

## Runs And Audit

```text
GET  /api/projects/{project_id}/runs
GET  /api/projects/{project_id}/runs/{run_id}/provenance.json
GET  /api/projects/{project_id}/audit
POST /api/projects/{project_id}/rebuild/preview
```

- Runs 当前记录 Character RAG run history、config、input count、suggestion count 和 accepted/rejected/pending counts。
- Runs、run provenance、manifest run summary 和 `suggestions.generated` event 都返回 `source_counts` / `confidence_counts`，按候选来源与置信度 bucket 汇总每次低算力 RAG run 的质量分布。
- Run provenance JSON 记录 run config、match keys、evidence、latest LLM review 和 decision event。
- Audit 汇总 event count、schema version、event types、actor distribution、pending outbox 和 replay issues。
- Rebuild preview 使用临时 SQLite database 重放 `events.jsonl`，不会覆盖当前 runtime database。

## Exports

```text
GET /api/projects/{project_id}/documents/{document_id}/export.jsonl
GET /api/projects/{project_id}/documents/{document_id}/export.prodigy.jsonl
GET /api/projects/{project_id}/documents/{document_id}/export.manifest.json
GET /api/projects/{project_id}/events.jsonl
GET /api/projects/{project_id}/tags/schema.json
```

- Task JSONL 使用 `annopilot.task.v1`，按 sentence 输出 tokens、spans、annotations、suggestions、answer、meta，并包含 Prodigy-style `_input_hash`、`_task_hash`、`_session_id`、`_annotator_id` 和 `_view_id`。
- Prodigy JSONL 使用 `prodigy.ner_manual.compat.v1`，保持 `_view_id=ner_manual`、`_session_id`、`_annotator_id`、`_input_hash` 和 `_task_hash`。
- Manifest JSON 使用 `annopilot.export_manifest.v1`，记录 artifact hashes、source run ids、annotation import history、run provenance summaries、live queue metrics 和 event audit，并提供排除生成时间的稳定 `content_sha256`。
- Events JSONL 是 project-level audit trail。
- `annotations.imported` event 会保存逐行 `source_record_results` manifest，用于审计外部 JSONL 每条记录的 hash、匹配状态、目标 sentence、answer 和 Prodigy-style source metadata。
- Tag schema JSON 使用 `annopilot.tag_schema.v1`，包含 label 定义、准则说明和 Character RAG lexical examples。

## Frontend Client

当前 frontend API client 分布在 `src/api/*`：

```text
annotations.ts
audit.ts
documents.ts
health.ts
http.ts
runs.ts
suggestions.ts
tags.ts
```

`src/types/domain.ts` 暂时手写 TypeScript payload types。后续 API 稳定后，建议从 FastAPI OpenAPI schema 生成 frontend types，减少 schema drift。
