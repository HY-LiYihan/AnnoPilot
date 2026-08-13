# 当前项目状态

本文记录 AnnoPilot 当前 repo 中已经落地的实现状态。它用于把代码现状同步到 docs，避免文档只停留在早期 architecture proposal。

## 当前维护的两个入口

AnnoPilot 现在同时维护两个 surface：

- **Product Web UI**：根目录的 Vue 3 + Vite + TypeScript app，当前已经是一个 TXT reader / annotation workspace。
- **Documentation Site**：`docs/` 下的 VitePress site，发布到 GitHub Pages，用于维护架构、开发方式和项目演进说明。

## 已落地能力

### Product Web UI

当前 frontend 已拆成 `src/api/*`、`src/composables/*` 和 `src/features/reader/*`，实现了一个本地 annotation reader：

- 上传 `.txt` 文件到 backend。
- 自动加载上一次 active document，document id 保存在 `localStorage`。
- 中间 header 提供 runtime document switcher，可在最近导入的 TXT 文档之间切换，并显示 progress、span count 和待确认建议数。
- 当前句位置会写入 SQLite runtime session cursor；刷新页面或重新打开同一 document 时，reader 会回到上次停留的句子。
- 将文档切分为 sentences，并展示 token-level annotation UI。
- 中间 reader 使用 sentence window 加载，避免长文档一次性把所有 tokens / annotations / suggestions 塞给前端。
- 左侧保留全局 sentence dot grid，绿色表示已完成，紫灰色表示已忽略，黄色表示有待确认建议，灰色表示未开始。
- 支持先选择 token/span，再用数字键或左侧 tag 应用标签；`S` 可把当前整句设为 pending span，`M` 可一键把当前句标为 Monogloss、写入 accept 并前进。
- 支持撤销最近一次人工 span 创建/删除，按钮和 `Ctrl/Cmd+Z` 均可触发，仍通过 backend API 写入 SQLite/JSONL。
- 右侧 metrics panel 集中展示快捷键和移动端手势，便于进入高频标注模式。
- 支持新增 / 重命名 / 编辑准则说明 / 维护低算力 RAG 词面种子 / 删除 tag；删除已使用 tag 前会提示对应 annotations 和 pending suggestions 会被一并删除。
- Project-level tag schema 可在无 active document 时独立加载，刷新后不会丢失已新增 tags。
- 支持 sentence completion，并保留 Prodigy-compatible `answer`：`Enter` 写入 `accept`，`Space` / `I` 写入 `ignore`，`J` 写入 `reject`，`E` 可 reopen 回 `pending`；UI 中展示 progress、reviewed count、annotation count 和 answer 分布。
- 支持移动端横向滑动切换上一句/下一句；token 上的拖选标注不会触发滑动切句。
- 支持只为当前句生成 Character RAG suggestions，也支持全文 suggestions run、批量接受 / 拒绝和 LLM review。
- Suggestion row 会展示匹配方法、标签、置信度、token/char range、evidence text 和原文上下文窗口，方便人工 review 时判断是否接受。
- LLM review context 会包含 tag description/examples、candidate tag definition、已有句内 annotations、同标签 boundary feedback 和 Engagement-specific boundary guidance；`context_sha256` 用于审计当次复核依据。
- 当前句可用 `Tab` / `Shift+Tab` 在 suggestions 间切换活跃 `Y/N target`，键盘 `Y` 接受活跃候选、`N` 拒绝活跃候选；`A` / `X` 仍用于当前句批量接受 / 拒绝。
- 当前句 `A` / `X` 走 sentence-scoped batch endpoint，在一个 SQLite transaction 中处理该句所有 pending suggestions，避免前端逐条循环写入。
- 支持独立 review queue API 和右侧 review queue 列表，可按原文位置、稳定随机 baseline、低 confidence、Goldsmith risk 或 hybrid calibration 排序；Goldsmith / hybrid 会把 latest LLM review 的 `reject` / `uncertain` 作为额外风险信号；summary metrics 会直接展示建议状态、LLM 评审推荐分布、待审建议来源、置信度分布和 human-calibrated error discovery 曲线；处理完当前句建议后会自动跳到下一句待确认，保留 `R` 快捷键手动跳转。
- 支持导出 task JSONL、Prodigy `ner_manual` / `spans_manual` JSONL、Goldsmith/Rosetta-style review queue、human choices、hard examples、boundary feedback、consistency scores 与 candidate runs JSONL、manifest JSON、events JSONL 和 Character RAG run provenance JSON。

当前 backend 和 frontend 已支持 Prodigy / AnnoPilot style annotations JSONL 导入，入口位于右侧 metrics/export panel。

当前默认 tags：

```text
1 名词：人、物、地点、抽象概念等实体或对象；默认种子包括 小猫、柳树、小河、石桥、叶子 等。
2 动词：动作、变化、状态或行为；默认种子包括 发芽、走来、看见、伸出、漂走 等。
3 形容词：性质、状态、颜色、程度等修饰性词语；默认种子包括 金色、安静、轻轻、慢慢。
```

### Backend API

当前 backend 使用 FastAPI，入口为 `backend.app.main:app`。

`GET /api/health` 会返回 SQLite/API 存活状态和非敏感 LLM runtime 信息，包括是否已配置、模型名和 provider host；不会返回 `LLM_API_KEY`。

已实现 API：

```text
GET    /api/health
POST   /api/projects/{project_id}/import-txt
POST   /api/projects/{project_id}/documents/{document_id}/import-annotations-jsonl
GET    /api/projects/{project_id}/documents?limit=50
GET    /api/projects/{project_id}/documents/{document_id}
GET    /api/projects/{project_id}/documents/{document_id}/summary
GET    /api/projects/{project_id}/documents/{document_id}/sentences?offset=0&limit=50
GET    /api/projects/{project_id}/documents/{document_id}/review-queue?limit=20&order=position|random|uncertain|goldsmith|hybrid
POST   /api/projects/{project_id}/documents/{document_id}/session/cursor
POST   /api/projects/{project_id}/sentences/{sentence_id}/annotations
DELETE /api/projects/{project_id}/annotations/{annotation_id}
POST   /api/projects/{project_id}/sentences/{sentence_id}/complete
GET    /api/projects/{project_id}/tags
POST   /api/projects/{project_id}/tags
POST   /api/projects/{project_id}/tags/schema/import
PATCH  /api/projects/{project_id}/tags/{tag_id}
DELETE /api/projects/{project_id}/tags/{tag_id}
POST   /api/projects/{project_id}/documents/{document_id}/suggestions/run
POST   /api/projects/{project_id}/documents/{document_id}/sentences/{sentence_id}/suggestions/run
POST   /api/projects/{project_id}/documents/{document_id}/suggestions/auto-accept
POST   /api/projects/{project_id}/documents/{document_id}/suggestions/auto-annotate
POST   /api/projects/{project_id}/documents/{document_id}/suggestions/auto-reject
POST   /api/projects/{project_id}/documents/{document_id}/suggestions/apply-llm-review
POST   /api/projects/{project_id}/suggestions/{suggestion_id}/accept
POST   /api/projects/{project_id}/suggestions/{suggestion_id}/reject
POST   /api/projects/{project_id}/sentences/{sentence_id}/suggestions/accept
POST   /api/projects/{project_id}/sentences/{sentence_id}/suggestions/reject
POST   /api/projects/{project_id}/suggestions/{suggestion_id}/llm-review
POST   /api/projects/{project_id}/sentences/{sentence_id}/suggestions/llm-review
POST   /api/projects/{project_id}/sentences/{sentence_id}/suggestions/apply-llm-review
GET    /api/projects/{project_id}/runs
GET    /api/projects/{project_id}/runs/{run_id}/provenance.json
GET    /api/projects/{project_id}/audit
POST   /api/projects/{project_id}/rebuild/preview
GET    /api/projects/{project_id}/documents/{document_id}/export.jsonl
GET    /api/projects/{project_id}/documents/{document_id}/export.prodigy.jsonl
GET    /api/projects/{project_id}/documents/{document_id}/export.prodigy.spans.jsonl
GET    /api/projects/{project_id}/documents/{document_id}/export.goldsmith.review-queue.jsonl
GET    /api/projects/{project_id}/documents/{document_id}/export.goldsmith.human-choices.jsonl
GET    /api/projects/{project_id}/documents/{document_id}/export.goldsmith.hard-examples.jsonl
GET    /api/projects/{project_id}/documents/{document_id}/export.goldsmith.boundary-feedback.jsonl
GET    /api/projects/{project_id}/documents/{document_id}/export.goldsmith.consistency-scores.jsonl
GET    /api/projects/{project_id}/documents/{document_id}/export.goldsmith.candidate-runs.jsonl
GET    /api/projects/{project_id}/documents/{document_id}/export.manifest.json
GET    /api/projects/{project_id}/events.jsonl
GET    /api/projects/{project_id}/tags/schema.json
```

当前 backend 还负责 production static serving：当 `STATIC_DIR` 存在时，FastAPI 会 serve `/assets` 和 Vue SPA history fallback。

### Text Processing

当前 `backend/app/text_processing.py` 已实现轻量 text processing：

- `normalize_text`：统一换行符。
- `split_sentences`：支持英文句号、中文 `。！？`、英文 `?!` 和换行 boundary。
- `tokenize_sentence`：生成 document-level offsets，支持 CJK character token、ASCII word token 和 punctuation token。

这让当前 reader 可以处理中英混合文本，并保持 annotation offsets 可追溯。

### Runtime Storage

当前 backend 使用 `backend/app/storage.py` 作为 API 兼容 facade，底层通过 SQLite 保存 runtime store，通过 JSONL event log 保存 durable audit trail；document import/merge/session、runtime settings、annotation mutation、tag schema、suggestion generation/review、suggestion decisions、audit/export 和 event replay/outbox 已逐步迁入 `services/` / `events/`。
右侧 Accuracy 指标当前定义为 LLM review recommendation 与已执行 accept/reject 动作的一致率；没有已 review 且已决策的样本时显示等待数据，不伪造 gold accuracy。
Character RAG 会使用已确认 annotations 作为正例，并把项目内 human rejected suggestions 以及 latest LLM review 为 `reject` 的 pending suggestions 的 `tag_id + text` 作为负例，后续生成建议时跳过同样的词面/标签组合；匹配判断会对空白和大小写做归一化，因此英文大小写变体不会重复打扰 review 队列。重新运行 suggestions 时只清理未 review 的 pending suggestions，已带 LLM review 的 pending suggestions 会保留，避免丢失复核信号。
候选 span 文本会根据 token offsets 还原英文词间空格，因此 `carbon emissions` 这类英文短语种子可以 exact match；中文连续字符仍按无空格词面匹配。导出的 suggestion text、evidence text 和 char offsets 保留原始文本，方便回放和审计。
Run history 会展示每次 Character RAG run 的 pending / accepted / rejected counts、来源分布、置信度分布和基于已决策样本的 acceptance rate，便于判断自动建议队列质量。每个 run 可导出 provenance JSON，追踪 config、当次正例/负例词表快照、归一化 match keys、evidence、LLM review 和 accept/reject decision event。

SQLite 当前保存：

- `tags`
- `documents`
- `sentences`
- `tokens`
- `annotations`
- `annotation_suggestions`
- `annotation_runs`
- `annotation_suggestion_reviews`
- `annotation_sessions`
- `event_outbox`

JSONL 当前保存：

```text
.runtime/projects/<project_id>/events.jsonl
```

每条 JSONL event 都带 `actor_type` / `actor_id`，用于区分人工操作、Character RAG 系统动作和 LLM review；Audit API 和右侧面板会聚合展示 human / system / llm 事件分布。

当前写入的 event types：

```text
document.imported
tag.created
tag.updated
tag.deleted
annotation.created
annotation.deleted
sentence.completed
annotations.imported
suggestions.generated
suggestion.accepted
suggestion.rejected
suggestion.llm_reviewed
```

`annotations.imported` 是导入批次 summary event；实际可重放状态仍由导入过程中产生的 `tag.created`、`annotation.deleted`、`annotation.created` 和 `sentence.completed` events 表达。该 summary event 还保存逐行 `source_record_results` manifest，用于追踪外部 Prodigy / AnnoPilot JSONL 每条记录的匹配结果、record hash 和源 session metadata。LLM review event 会包含 `context_sha256`，用于 hash 当次模型调用的完整结构化 review context；即使后续 tags、sentence annotations 或上下文变化，也能审计当时的 Aixhan / OpenAI-compatible review decision。

### Docker Deployment

当前 Dockerfile 已是 two-stage build：

```text
Stage 1: node:22-alpine
  npm ci
  npm run build

Stage 2: python:3.12-slim
  pip install backend requirements
  copy backend
  copy frontend dist to /app/static
  run uvicorn on 0.0.0.0:8080
  healthcheck /api/health
```

Runtime env：

```text
DATA_ROOT=/data/projects
DATABASE_PATH=/data/runtime/annopilot.sqlite
STATIC_DIR=/app/static
```

根目录 `docker-compose.yml` 提供单容器本地部署：

```text
ports: 8080:8080
volumes: ./data:/data
mem_limit: 1g
healthcheck: GET /api/health
```

服务器侧 AnnoPilot 部署已经补齐拆分镜像和 webhook 自动更新链路：`Deploy AnnoPilot` workflow 在 `CI` 对 `main` push 成功后构建 `annopilot-api` / `annopilot-web` GHCR 镜像，并通过签名 webhook 触发 `/opt/annopilot/bin/deploy.sh`。当前服务器 webhook 已安装为 `annopilot-webhook.service`，OpenResty 80 端口窄路由转发到本机 `9010`，新服务先使用灰度端口 `18501`，不影响旧 `8501` 服务。详细说明见 [AnnoPilot Docker 服务器部署](/guide/annopilot-docker-deployment)。

### Documentation Site

当前 docs site 使用 VitePress：

- `docs/index.md`：文档站首页。
- `docs/architecture.md`：总体架构设计。
- `docs/guide/documentation.md`：文档维护方式。
- `docs/guide/deployment.md`：GitHub Pages 部署方式。
- `.github/workflows/docs.yml`：GitHub Pages workflow。

GitHub Pages URL：

```text
https://hy-liyihan.github.io/AnnoPilot/
```

## 当前开发命令

```bash
npm install
python3 -m pip install -r backend/requirements-dev.txt
```

启动 API：

```bash
npm run api
```

启动 Product Web UI：

```bash
npm run dev
```

启动 Documentation Site：

```bash
npm run docs:dev
```

## 当前测试覆盖

Backend 已有 pytest 覆盖：

- TXT import。
- Document fetch。
- Annotation create。
- Sentence complete。
- JSONL export。
- JSONL event log 写入。
- Empty TXT validation。
- Prodigy-compatible export。
- Event audit 和 non-destructive rebuild preview。
- Health API 不泄露 `LLM_API_KEY`。
- LLM HTTP error redaction。
- Mixed-language sentence splitting。
- Document-level token offsets。
- Empty / punctuation / multiline text processing。

## 仍待演进

当前实现是一个有用的 first product slice，但还不是完整 architecture.md 中的长期目标。后续重点：

- 增加 project management，而不是只使用 `default` project。
- 将 tag schema 从当前 CRUD 演进为更完整的 project-level guideline / label setup。
- 给 annotations JSONL 导入补 frontend 入口，形成 import/export round-trip。
- 增加 calibration runs 和 batch annotation runs。
- 增加 API OpenAPI type generation，减少 frontend 手写 payload types。
- 将当前 sentence window 进一步演进为虚拟滚动和更细的预取策略。
