# 架构决策记录

本文维护 AnnoPilot 的长期架构决策。ADR 只记录会影响后续实现方向、依赖选择、模块边界或部署方式的决定；短期 UI 细节和普通 bugfix 不进入这里。

## 决策列表

- [0001 Local-first SQLite + JSONL Runtime](./0001-local-first-sqlite-jsonl)：继续使用 SQLite 作为 runtime store，JSONL 作为 durable audit/export artifacts。
- [0002 AnnotationStorage Facade 拆分](./0002-storage-facade-refactor)：保留 `AnnotationStorage` 对 API 的兼容入口，内部按 services/repositories 渐进拆分。
- [0003 暂缓重型运行时依赖](./0003-defer-heavy-runtime-dependencies)：在当前 1 GB 单机目标下暂不引入 Postgres、Redis、Celery、Pinia/router 等重型设施。

## 目标目录草图

后端目标是把 `AnnotationStorage` 收敛成 facade，并让业务域拥有清晰边界：

```text
backend/app/
  api/                    # FastAPI routers: routing, HTTP errors, response models
  db/                     # connection, schema, migrations
  repositories/           # read/write SQL query objects and read models
    documents.py
    tags.py
    runs.py
    annotations.py
    suggestions.py
    audit.py
    exports.py
  services/               # transactions, validation, event-producing workflows
    documents.py
    annotations.py
    suggestion_decisions.py
    suggestions.py
    tags.py
    exports.py
    audit.py
    reset.py
  events/                 # event builder, outbox flush, replay validation
    outbox.py
    replay.py
    schemas.py
  exports/                # JSONL, Prodigy, manifest, provenance shaping
  storage.py              # compatibility facade used by current routers/tests
```

前端目标是在不急着引入全局 store 的情况下，把 reader composable 按业务子域拆开：

```text
src/composables/
  useReaderState.ts       # wrapper that preserves ReaderWorkspace interface
  useReaderDocumentState.ts
  useReaderNavigation.ts
  useReaderAnnotations.ts
  useReaderSuggestions.ts
  useReaderExports.ts
  useReaderDiagnostics.ts
  useReaderKeyboard.ts
```

## 更新规则

- ADR 使用 `Accepted` / `Superseded` / `Deprecated` 等状态，不静默删除历史决策。
- 每个 ADR 必须写清 Context、Decision、Consequences。
- 实现已经改变时，优先新增或更新 ADR，再同步 `architecture.md` 和 `guide/current-state.md`。
