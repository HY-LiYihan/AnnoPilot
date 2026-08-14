# AnnoPilot 架构设计

AnnoPilot 现在已经从早期 Streamlit 方向切到轻量的 **local-first Web application**：产品 UI 使用 Vue 3 + Vite + TypeScript，backend 使用 FastAPI，runtime state 放在 SQLite，durable audit trail 使用 JSONL。默认部署目标仍然是单个 Docker container，内存预算控制在 1 GB 以内。

长期架构取舍记录在 [架构决策记录](/decisions/) 中；本页只描述当前系统形态和主要数据流。

## 当前目标

- 面向 mobile 和 desktop 的 annotation / review Web UI，优先保证高频标注效率。
- 单容器部署，挂载 `/data` 后即可保存 SQLite runtime database 和 JSONL artifacts。
- SQLite 负责快速读取、分页、queue 和派生统计；JSONL 负责 audit、export 和 rebuild 基础。
- 当前先服务 TXT annotation reader、Character RAG suggestions、LLM review 和 Prodigy-compatible export。
- 后续再按真实 workload 拆出 project management、batch workers、SSE progress 和 multi-user storage。

## 总体形态

```text
Mobile/Desktop Browser
        |
Vue 3 + Vite Static SPA
        |
FastAPI REST API
        |
AnnotationStorage service
        |
SQLite runtime store + event_outbox
        |
JSONL event log / exports / provenance
        |
Optional OpenAI-compatible LLM provider
```

当前 production image 由 FastAPI 同时 serve `/api/*` 和 Vue static assets。frontend build output 会复制到 `/app/static`，backend 检测到 `STATIC_DIR` 后提供 `/assets/*` 与 SPA history fallback。

## Frontend

当前 frontend 位于 `src/`，已经不是 landing prototype，而是一个可用的 TXT annotation workspace。

```text
src/
  main.ts
  App.vue
  styles.css
  api/
    annotations.ts
    audit.ts
    documents.ts
    health.ts
    http.ts
    runs.ts
    suggestions.ts
    tags.ts
  composables/
    useDocumentReader.ts
    useTokenSelection.ts
  features/reader/
    ReaderWorkspace.vue
    MetricsPanel.vue
    SentencePanel.vue
    TagPalette.vue
  types/
    domain.ts
```

Frontend 职责：

- 加载 active document，使用 `localStorage` 保存最近打开的 `document_id`。
- 通过 `summary` + paged `sentences` API 读取数据，避免在 browser 中保存完整长文档。
- 处理 token drag selection、keyboard shortcuts、mobile swipe、tag CRUD、suggestion review 和 export actions。
- 所有 mutations 都走 backend API，并以 API response 刷新本地状态，避免 browser 直接写 SQLite 或 JSONL。

当前没有引入 `vue-router` 或 `pinia`。这符合现阶段单 workspace 的复杂度。等 project setup、runs、exports 和 settings 变成独立 screen 后，再引入 route-level views 和小型 app store。

## Backend

Backend 位于 `backend/app/`，入口是 `backend.app.main:app`。

```text
backend/app/
  main.py
  schemas.py
  settings.py
  llm.py
  rag.py
  rebuild.py
  storage.py
  text_processing.py
  api/
    annotations.py
    audit.py
    dependencies.py
    documents.py
    exports.py
    health.py
    runs.py
    suggestions.py
    tags.py
  db/
    connection.py
    migrations.py
    schema.py
  events/
    outbox.py
    replay.py
  repositories/
    documents.py
    runs.py
    tags.py
  services/
    annotations.py
    audit.py
    documents.py
    exports.py
    runtime_settings.py
    suggestion_decisions.py
    suggestions.py
    tags.py
```

Backend 当前边界：

- `api/*` 只做 routing、validation error mapping 和 response model 绑定。
- `schemas.py` 集中维护 Pydantic request / response contracts。
- `storage.py` 是当前 API 兼容 facade；SQLite migration 在 `db/`，event outbox/replay 在 `events/`，document import/merge/session、runtime settings、annotation、suggestion generation / decision、tag schema、audit/export workflow 已开始迁入 `services/`。
- `rag.py` 实现低算力 Character RAG：lexical exact、contains、char-ngram、Unicode NFKC、quote/dash/slash folding、casefold + whitespace normalization。
- `llm.py` 使用 OpenAI-compatible `/chat/completions`，用于 suggestion LLM review，并在错误信息中 redact API key。
- `rebuild.py` 支持从 `events.jsonl` 重建 SQLite 的 CLI / service 能力，并复用 `events/replay.py` 的可重放事件校验与 apply 逻辑；API 先提供 non-destructive preview。

API 目录见 [API Surface](/guide/api)。

## Runtime Storage

当前 storage 策略是 **SQLite runtime store + JSONL durable event log**。

SQLite tables：

```text
tags
documents
sentences
tokens
annotations
annotation_suggestions
annotation_runs
annotation_run_sentences
annotation_run_candidate_spans
annotation_suggestion_reviews
annotation_sessions
event_outbox
```

关键规则：

- mutation 先在 SQLite transaction 中写 domain rows 和 `event_outbox`。
- transaction 成功后，pending outbox rows flush 到 `<DATA_ROOT>/<project_id>/events.jsonl`。
- `annotation_sessions` 保存 runtime-only 标注会话状态，例如当前句游标；普通导航不进入 JSONL audit log。
- audit API 统计 event type、actor、schema version、pending outbox 和 replay issues。
- rebuild preview 使用临时 SQLite database 重放 `events.jsonl`，不会破坏当前 runtime database。

JSONL schema 当前包括：

```text
annopilot.event.v1
annopilot.task.v1
annopilot.export_manifest.v1
prodigy.ner_manual.compat.v1
prodigy.spans_manual.compat.v1
annopilot.tag_schema.v1
annopilot.run_provenance.v1
```

更细的 schema、event 和 export 说明见 [Runtime Storage](/guide/runtime-storage)。

## 数据流

### TXT Import

1. Browser 上传 `.txt` 到 `POST /api/projects/{project_id}/import-txt`。
2. Backend 统一 UTF-8、换行和 sentence splitting。
3. Backend 写入 `documents`、`sentences`、`tokens`，并追加 `document.imported` snapshot event。
4. UI 读取 document summary 和 sentence window。

### Manual Annotation

1. UI 选择 token span 和 tag。
2. Backend 校验 token range，写入 `annotations`。
3. Event log 追加 `annotation.created` 或 `annotation.deleted`。
4. Sentence completion 写入 `answer`，支持 `pending`、`accept`、`ignore`、`reject`。

### Character RAG Suggestions

1. UI 可针对当前 sentence 或整个 document 触发 suggestions run。
2. `services/suggestions.py` 从 tag examples、已确认 annotations 和 rejected suggestions 构建正负例。
3. `rag.py` 生成候选 span，service 保存 live `annotation_suggestions`，并在 `annotation_run_sentences` / `annotation_run_candidate_spans` 固化该 run 的完整句子级输出。
4. Run config 记录 tag schema hash、examples hash、negative examples hash、match keys 和 retrieval 规则；Goldsmith/Rosetta consistency export 使用最近 5 个完整 run snapshot 做 span-set self-consistency。
5. UI 可单条 accept/reject、当前句批量处理、全文 auto-accept / auto-reject，或一键运行 Character RAG 并自动接受高置信 span。

### LLM Review

1. UI 对 pending suggestion 发起 LLM review。
2. `services/suggestions.py` 构造包含 sentence、candidate span、tag definitions/examples、existing annotations、Engagement boundary guidance 和 `span_context` 的结构化 context。
3. OpenAI-compatible provider 返回 `accept`、`reject` 或 `uncertain` recommendation。
4. Backend 保存 review row 和 `suggestion.llm_reviewed` event，并记录 `context_sha256`。

### Import / Export

- `import-annotations-jsonl` 可导入 Prodigy / AnnoPilot style JSONL annotation records，并尽量按 `sentence_id`、`sentence_index` 或 sentence text 匹配；`annotations.imported` event 会保留逐行 source manifest，记录 record hash、匹配结果、目标 sentence 和 Prodigy-style source metadata。
- Task JSONL 以 sentence 为粒度导出 token、span、suggestion、answer、meta 和 Prodigy-style stable hashes / session metadata。
- Prodigy export 保持 `ner_manual` / `spans_manual` compatible fields，并提供 bundle ZIP 把 Prodigy JSONL、label config、tag schema、Goldsmith review queue、label statistics、contrastive examples、reflection plans、prompt package、verification report、bootstrap report 和 manifest 收拢成一个交付包。
- Manifest 汇总 tasks、Prodigy、events、tag schema、run provenance、annotation import history、artifact hashes、audit summary 和稳定 content hash。

## Docker Deployment

当前 Dockerfile 是 two-stage build：

```text
Stage 1: node:22-alpine
  npm ci
  npm run build

Stage 2: python:3.12-slim
  pip install backend requirements
  copy backend
  copy frontend dist to /app/static
  run uvicorn backend.app.main:app on 0.0.0.0:8080
```

Runtime env：

```text
DATA_ROOT=/data/projects
DATABASE_PATH=/data/runtime/annopilot.sqlite
STATIC_DIR=/app/static
LLM_BASE_URL=https://api.aixhan.com/v1
LLM_API_KEY=<optional>
LLM_MODEL=gpt5.5
```

`docker-compose.yml` 默认：

```text
ports: 8080:8080
volumes: ./data:/data
mem_limit: 1g
healthcheck: GET /api/health
```

## 1 GB 内存策略

当前实现用以下方式控制 footprint：

- 单个 Uvicorn worker。
- SQLite WAL + paged query，不把完整文档和建议队列塞进 frontend。
- Sentence window 默认小窗口加载，frontend 只持有当前附近 sentences。
- Character RAG 是 lexical / char-ngram，不加载本地 embedding 或 LLM model。
- LLM review 走外部 OpenAI-compatible API，只传单条 suggestion context。
- JSONL event/export 使用 line-oriented payload，避免把项目 artifacts 当成长期内存状态。

## 优化建议

短期优先级：

- 继续把 `storage.py` 按 repository/service 拆分：reset 和 annotation import 仍可继续迁出，减少 facade 膨胀。
- 继续加强 `import-annotations-jsonl` 的混合中英 offset round-trip 覆盖，确保外部 Prodigy / AnnoPilot JSONL 审阅结果导回后不会产生 span 漂移。
- 给 OpenAPI schema 生成 TypeScript types，替代长期手写 `src/types/domain.ts`。
- 将 health / audit / rebuild preview 做成更明确的 diagnostics panel，方便部署后快速定位 JSONL 或 LLM 配置问题。

中期演进：

- 引入 `vue-router`，把 reader、runs、exports、settings 拆成 route-level views。
- 当 suggestion run 或 batch annotation 变慢时，再添加 SSE progress endpoint 或 background worker。
- 当项目数量和并发用户真正增长时，再考虑 project management、auth、Postgres 或 object storage。

## 决策摘要

当前架构是 **Vue 3 + Vite + TypeScript frontend、FastAPI backend、SQLite runtime store、JSONL durable artifacts、single-container Docker deployment**。这条路线已经淘汰 Streamlit，保留 local-first、mobile-friendly、auditable 和 1 GB runtime budget 的核心约束。
