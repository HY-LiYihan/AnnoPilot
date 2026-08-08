# AnnoPilot 架构设计

AnnoPilot 应该是一个轻量、local-first 的 Web application，而不是 Streamlit app。目标运行形态是单个 Docker container、浏览器优先的 UI、良好的手机适配、SQLite 作为 runtime state，以及 JSONL 作为 durable source of truth。

Frontend 架构已经确定：AnnoPilot 使用 **Vue 3 + Vite + TypeScript** 构建产品 UI。

## 设计目标

- 提供快速、清晰、适配 mobile 和 desktop 的 Web UI，用于 annotation、review、calibration 和 export。
- 默认部署保持简单：一个 container、一个挂载数据目录、不依赖外部服务。
- 普通 local project 的运行内存控制在 1 GB 以内。
- 每个 project 都可以从 JSONL artifacts 审计、恢复和 rebuild。
- 架构保留自然拆分空间，后续 batch processing、multi-user hosting 或 background workers 增长时可以渐进演化。

## 非目标

- 不使用 Streamlit 作为 product UI。
- 默认 local deployment 不强依赖 Postgres、Redis、object storage 或独立 queue。
- 在 1 GB runtime budget 内，不默认假设 local LLM 可用。
- 不把 SQLite 作为唯一 durable data format；SQLite 是 runtime index 和 cache。

## 总体架构

```text
Mobile/Desktop Browser
        |
 Static Web UI
 Vue 3 + Vite
        |
 FastAPI Backend
 REST + SSE for progress events
        |
 SQLite Runtime Store
 queues / indexes / sessions / derived views
        |
 JSONL Durable Store
 projects / examples / annotations / reviews / runs / exports
        |
 Optional External Providers
 LLM APIs / embedding APIs / import sources
```

## 架构决策

AnnoPilot 使用 **Vue 3 + Vite** 作为 Static Web UI。

这个选择主要优化以下几点：

- 支持 mobile-friendly interaction，不受 notebook-style app framework 限制。
- local development 快，production static assets 小。
- 适合 form-heavy product surface，例如 project setup、guidelines、labels、provider settings、review forms 和 export controls。
- 可以从简单 landing shell 平滑演进到 routed product views。
- frontend 可以在 Docker build 阶段构建一次，再由 backend 在同一个 image 内 serve。

明确不采用的 UI 路线：

- **Streamlit**：不适合 polished mobile review flow 和 product-grade interaction design。
- **Nuxt 或其他 full meta-framework**：当前不需要 SSR 或 meta-framework，backend 已经负责 API、persistence 和 deployment。
- **大型 component/admin framework 默认引入**：后续可以按需引入，但不能牺牲 bundle size 和 mobile ergonomics。

## 推荐技术栈

### Frontend

- 使用 **Vue 3 + Vite** 作为默认 product UI stack。
- 使用 **TypeScript** 管理 UI code 和 API contracts。
- UI 构建为 static bundle，由 backend 或轻量 static file server serve。
- Review workflow 采用 mobile-first responsive layout。
- 优先使用 Vue single-file components、轻量 local state 和 request-level caching，再考虑更重的 app framework。

选择 Vue 3 的原因：

- 适合 project setup、annotation review、dashboards 和 settings screens 这类 form-heavy 界面。
- single-file components 和 scoped styles 开发体验好。
- routing、state management、tables、forms、mobile-friendly UI patterns 的生态成熟。
- 团队上手成本低，同时比 full meta-framework 更轻。

Vite 继续作为 build tool，因为它能保持 local development 快速，并输出易于放入 Docker image 的 static assets。

推荐 frontend dependencies：

- `vue`：component runtime。
- `@vitejs/plugin-vue`、`vite`、`typescript`、`vue-tsc`：development 和 production build。
- `vue-router`：当 app 从初始 landing shell 演进到 project、review、run、export routes 时引入。
- `pinia`：只有当 cross-view state 足够明确时再引入；dataset 和 queue state 保持在 server。

默认避免：

- 强制 desktop-first layout 的 heavy UI suites。
- Client-side database layer。
- 在 browser state 中保存完整 dataset。
- SSR-only assumptions。

### Backend

- 使用 **FastAPI**，默认一个 Uvicorn worker。
- 暴露 REST API，覆盖 project、example、annotation、review、run 和 export。
- 使用 SSE 处理 long-running batch progress 和轻量 live status updates。
- 默认 single-container deployment 中，background jobs 先使用 in-process runner。
- 只有当 batch workload 超出 1 GB local target 时，再拆出单独 worker process。

### Runtime Database

- 使用 **SQLite** 保存 runtime state、indexes、queues 和 derived views。
- 启用 WAL mode。
- 使用受控 cache settings，保证内存可预期。
- SQLite 必须可以从 project JSONL files rebuild。

SQLite 的职责：

- 快速 filtering 和 pagination。
- Review queue state。
- Batch job state。
- Prompt/run indexes。
- Dashboard summaries 和 derived metrics。
- Import 与 background task 的 idempotency keys。

### Durable Storage

- 使用 **JSONL** 作为 canonical project artifact format。
- 尽量将变更保存为 append-only event records。
- Export files 保持兼容 Prodigy-style JSONL。
- 使用 manifest 记录 reproducibility 信息。

推荐 project layout：

```text
/data/
  runtime/
    annopilot.sqlite
    annopilot.sqlite-wal
  projects/
    <project_id>/
      manifest.json
      events.jsonl
      examples.jsonl
      annotations.jsonl
      reviews.jsonl
      guidelines.md
      runs/
        <run_id>.jsonl
      exports/
        prodigy_<timestamp>.jsonl
```

## Frontend 应用框架

Vue app 应保持 static SPA。Backend 负责 persistence、background work 和 API boundaries；frontend 负责 interaction quality。

推荐 frontend layout：

```text
src/
  main.ts
  App.vue
  styles.css
  router/
    index.ts
  api/
    client.ts
    projects.ts
    examples.ts
    reviews.ts
    runs.ts
    exports.ts
  components/
    AppShell.vue
    MobileNav.vue
    StatusBadge.vue
    EmptyState.vue
  features/
    projects/
    setup/
    calibration/
    batch/
    review/
    exports/
  stores/
    appStore.ts
    projectStore.ts
  composables/
    useApi.ts
    useSse.ts
    useResponsiveLayout.ts
  types/
    api.ts
    domain.ts
```

当前 homepage/prototype 可以继续放在 `App.vue`。当 product views 变多后，将 view-level screens 移到 `src/features/*`，并让 `App.vue` 只负责 layout/router host。

### Frontend Responsibilities

- 渲染 project setup、calibration、batch status、review、reports 和 export workflows。
- Review interaction 同时支持 keyboard-friendly 和 touch-friendly 操作。
- Examples 和 review queue 通过 paginated API 获取。
- Long-running run status 使用 SSE，避免高频 polling。
- Browser 内只保留 active page state。
- Mutation 完成后以 backend response 作为 source of truth。

### Frontend State Model

使用三层 state：

```text
Local component state
  form drafts / modal state / transient UI details

Small app store
  active project / current route context / theme / API health

Server state
  examples / annotations / review queue / runs / reports
```

不要把完整 dataset mirror 到 Pinia。List page 和 review queue 都应 page by page 获取，review queue pages 保持小尺寸。

### Routing Model

初始 product routes：

```text
/
/projects
/projects/:projectId/setup
/projects/:projectId/calibrate
/projects/:projectId/batch
/projects/:projectId/review
/projects/:projectId/runs
/projects/:projectId/exports
/projects/:projectId/reports
```

Mobile layout 优先暴露 `/review`、`/runs` 和 `/exports`。Desktop layout 可以展示完整 project workflow。

### API Contract

Backend 使用 FastAPI 发布 OpenAPI。API 稳定后，frontend types 应从 OpenAPI schema 生成；稳定前保持小型 hand-written TypeScript domain types，并集中在 `src/types/api.ts` 附近。

API client 规则：

- 使用一个小型 `fetch` wrapper，处理 JSON、error normalization 和 abort support。
- Endpoint-specific functions 放在 `src/api/*`。
- Route transition、search 和 filter changes 使用 `AbortController`。
- Backend validation errors 转换为 UI-friendly shape。
- 除非 recovery path 很明确，否则不要对 failed mutations 做隐藏式 optimistic update。

### Visual System

UI 应该像 workbench，而不是 marketing site。

- 使用 responsive full-width layouts，保持 dense but readable panels。
- Cards 只用于 repeated items、review records 和 compact summaries。
- Mobile review flow 使用 bottom navigation 或 segmented controls。
- Desktop 使用 persistent sidebar 或 top-level project nav。
- Typography、spacing 和 colors 先放在 `src/styles.css` 的 CSS variables 中。
- 优先使用 native controls 和小型 custom components，再考虑大型 UI framework。

## Backend Modules

```text
app/
  main.py
  api/
    projects.py
    guidelines.py
    examples.py
    annotations.py
    reviews.py
    runs.py
    exports.py
  services/
    project_service.py
    ingest_service.py
    calibration_service.py
    annotation_service.py
    review_service.py
    export_service.py
    rebuild_service.py
  storage/
    sqlite.py
    jsonl_store.py
    event_log.py
    repositories.py
  workers/
    batch_runner.py
    retry_policy.py
  schemas/
    project.py
    example.py
    annotation.py
    review.py
    run.py
    event.py
```

## Repository Layout

在 homepage/prototype 阶段，Vue app 可以继续保留在 repository root，因为当前 Vite scaffold 已经在这里。目标 production layout 应保持 frontend 和 backend 边界清晰：

```text
.
  package.json
  vite.config.ts
  tsconfig.json
  index.html
  src/                    # Vue 3 + Vite static web UI
  app/                    # FastAPI backend package
  tests/                  # backend tests
  docs/
    architecture.md
  data/                   # local development data, ignored by git
  Dockerfile
  docker-compose.yml
```

如果 backend 后续变大，可以再迁移到 `web/` 和 `server/` 目录。但第一版 implementation 不应为了目录洁癖引入 churn，应保持当前 Vite scaffold 稳定，避免干扰 homepage 工作。

## Runtime Boundaries

```text
Vue UI
  Build 后只有 static files。
  调用 `/api/*`，并订阅 SSE endpoints。

FastAPI
  负责 validation、persistence、job orchestration 和 static file serving。

SQLite
  Runtime index 和 queue database。
  可从 JSONL rebuild。

JSONL
  Durable project artifacts 和 audit trail。
  Docker 中挂载到 `/data/projects`。
```

任何 browser route 都不能直接写 JSONL 或 SQLite。所有 mutations 必须通过 backend APIs，确保 trace records、validation 和 rebuild behavior 一致。

## 核心数据流

### Import

1. User 上传或粘贴 examples。
2. Backend 将 examples streaming 写入 JSONL。
3. Backend 将 examples index 到 SQLite。
4. UI 通过 SQLite-backed APIs 分页读取 examples。

### Calibration

1. User 编辑 guidelines 和 gold examples。
2. Backend 保存 guideline snapshots 和 calibration run metadata。
3. LLM calls 将 trace records streaming 写入 run JSONL files。
4. SQLite 保存 current run status 和 aggregate scores。

### Batch Annotation

1. User 启动 batch run。
2. Backend 创建 durable run manifest 和 SQLite job rows。
3. In-process runner 按 bounded chunks 处理 examples。
4. Outputs append 到 run JSONL 和 annotation JSONL。
5. Uncertain 或 conflicting cases index 到 review queue。

### Review

1. Mobile-friendly UI 获取一个 review item 或小 page。
2. User 执行 accept、edit、skip 或 mark uncertain。
3. Backend append review events 到 JSONL。
4. SQLite 更新 queue position 和 dashboard counts。

### Export

1. User 选择 export format 和 filters。
2. Backend 使用 SQLite 选择记录，并用 JSONL 保持 traceable records。
3. Backend 将 export files 写入 project export directory。
4. Export manifest 记录 source run ids、filters、timestamp 和 schema version。

## API Surface

初始 REST API：

```text
GET    /api/health
GET    /api/projects
POST   /api/projects
GET    /api/projects/{project_id}

GET    /api/projects/{project_id}/guidelines
PUT    /api/projects/{project_id}/guidelines

GET    /api/projects/{project_id}/examples
POST   /api/projects/{project_id}/examples/import

POST   /api/projects/{project_id}/calibration-runs
GET    /api/projects/{project_id}/runs
GET    /api/projects/{project_id}/runs/{run_id}
GET    /api/projects/{project_id}/runs/{run_id}/events

POST   /api/projects/{project_id}/batch-runs
GET    /api/projects/{project_id}/review-queue
POST   /api/projects/{project_id}/review-queue/{item_id}/decision

POST   /api/projects/{project_id}/exports
GET    /api/projects/{project_id}/exports
```

需要 progress stream 时，使用 `GET /events` 风格的 SSE endpoints。

## Mobile UI Structure

Phone experience 优先优化 review，而不是复杂 project administration。

Primary mobile routes：

- `/projects`：选择 active project。
- `/projects/:id/review`：accept/edit/skip review flow。
- `/projects/:id/runs`：查看 status。
- `/projects/:id/export`：轻量 export actions。

Primary desktop routes：

- `/projects/:id/setup`：guidelines、labels 和 gold examples。
- `/projects/:id/calibrate`：prompt testing 和 comparison。
- `/projects/:id/batch`：batch annotation runs。
- `/projects/:id/review`：queue triage。
- `/projects/:id/reports`：metrics 和 export manifests。

## Memory Budget

默认 container memory limit：1 GB。

粗略预算：

```text
FastAPI + Uvicorn                     80-150 MB
SQLite page/cache                     64-128 MB
In-process batch runner              100-250 MB
Vue static assets                      10-30 MB
JSONL streaming buffers                10-50 MB
LLM/embedding client state             20-80 MB
Operational headroom                  300-500 MB
```

默认配置：

```text
WEB_CONCURRENCY=1
BATCH_CONCURRENCY=2
SQLITE_CACHE_SIZE_MB=64
MAX_IMPORT_CHUNK_SIZE=500
MAX_UPLOAD_MB=100
JSONL_FLUSH_EVERY=1
```

Memory rules：

- JSONL reads 和 writes 都使用 streaming。
- 不把完整 project dataset 加载到 memory。
- Examples 和 review items 必须 pagination。
- LLM batch work 使用 bounded chunks。
- Embeddings 默认 external 或 optional，除非明确配置小型 local model。

## Docker Deployment

默认 single-container deployment：

```yaml
services:
  annopilot:
    build: .
    ports:
      - "8080:8080"
    volumes:
      - ./data:/data
    environment:
      APP_ENV: production
      DATA_ROOT: /data/projects
      DATABASE_PATH: /data/runtime/annopilot.sqlite
      WEB_CONCURRENCY: "1"
      BATCH_CONCURRENCY: "2"
    mem_limit: 1g
```

Container process：

```text
uvicorn app.main:app --host 0.0.0.0 --port 8080 --workers 1
```

Frontend 在 Docker image build 阶段构建，并复制到 backend image 作为 static files。

推荐 Docker build shape：

```text
Stage 1: node build
  npm ci
  npm run build

Stage 2: python runtime
  install backend dependencies
  copy FastAPI app
  copy dist/ to app static directory
  run uvicorn with one worker
```

Production container 应 serve：

```text
/api/*       FastAPI routes
/events/*    SSE streams where needed
/*           Vue static app with history fallback
```

## Rebuild and Recovery

由于 JSONL 是 canonical source，AnnoPilot 应提供 rebuild command：

```text
annopilot rebuild --project <project_id>
```

Rebuild flow：

1. 清理 project-specific SQLite rows。
2. 按 timestamp 顺序读取 manifest 和 JSONL artifacts。
3. 重建 examples、annotations、reviews、runs 和 queue state。
4. 重新计算 dashboard summaries。
5. 对 malformed 或 orphaned records 给出报告，不能 silent drop。

## Future Split Points

只有在确实需要时才拆分架构：

- 当 batch runs 在真实 workload 下阻塞 API，再增加 worker process。
- 只有 in-process jobs 不够时，再增加 Redis 或其他 queue。
- 只有 multi-user hosting 需要更强并发时，再引入 Postgres。
- 只有 project artifacts 超出 local mounted volumes 能力时，再引入 object storage。
- 只有 memory budget 超过 1 GB target 时，再考虑 local model sidecar。

## 决策摘要

最终架构为 **Vue 3 + Vite + TypeScript frontend、FastAPI backend、SQLite runtime store、JSONL durable project artifacts**。这条路线将 Streamlit 从 product path 中移除，同时保留 AnnoPilot 的 local-first、auditable、Docker-friendly 方向。
