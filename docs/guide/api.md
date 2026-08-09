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
POST /api/projects/{project_id}/sentences/{sentence_id}/complete
```

- `import-txt` 接收 UTF-8 `.txt`，当前大小上限为 10 MB。
- `import-annotations-jsonl` 接收 Prodigy / AnnoPilot style `.jsonl` annotation records；frontend 在右侧 metrics/export panel 暴露 `Import JSONL` 入口。
- `documents` 返回最近 runtime documents 的轻量索引，包含 progress、annotation count 和 pending suggestion count，供 frontend 切换已导入文档。
- `summary` 返回 document meta、tags、metrics 和全局 sentence queue，不返回完整 tokens。
- `sentences` 返回分页 sentence window，当前 API limit 上限为 200。
- `complete` 写入 `completed` 和 Prodigy-compatible `answer`，当前支持 `accept`、`ignore`、`reject`、`pending`。

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
POST /api/projects/{project_id}/documents/{document_id}/suggestions/auto-reject
POST /api/projects/{project_id}/suggestions/{suggestion_id}/accept
POST /api/projects/{project_id}/suggestions/{suggestion_id}/reject
POST /api/projects/{project_id}/suggestions/{suggestion_id}/llm-review
```

- Suggestions 当前由 Character RAG 生成，支持 document scope 和 sentence scope。
- `limit_per_sentence` 默认 6，上限 20；`min_confidence` 取值范围 0 到 1。
- Accept 会创建 `source=accepted_suggestion` annotation，并把 suggestion 状态改为 `accepted`。
- Reject 会把 suggestion 状态改为 `rejected`，后续 Character RAG 会把同 tag + text 当作 negative example。
- LLM review 使用 OpenAI-compatible `/chat/completions`，返回 `recommendation`、`confidence`、`rationale` 和 `context_sha256`。

## Runs And Audit

```text
GET  /api/projects/{project_id}/runs
GET  /api/projects/{project_id}/runs/{run_id}/provenance.json
GET  /api/projects/{project_id}/audit
POST /api/projects/{project_id}/rebuild/preview
```

- Runs 当前记录 Character RAG run history、config、input count、suggestion count 和 accepted/rejected/pending counts。
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

- Task JSONL 使用 `annopilot.task.v1`，按 sentence 输出 tokens、spans、annotations、suggestions、answer 和 meta。
- Prodigy JSONL 使用 `prodigy.ner_manual.compat.v1`，保持 `_view_id=ner_manual`、`_session_id`、`_annotator_id`、`_input_hash` 和 `_task_hash`。
- Manifest JSON 使用 `annopilot.export_manifest.v1`，记录 artifact hashes、source run ids、run provenance summaries 和 event audit。
- Events JSONL 是 project-level audit trail。
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
