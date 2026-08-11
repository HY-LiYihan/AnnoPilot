# 0003 暂缓重型运行时依赖

- Status: Accepted
- Date: 2026-08-11

## Context

AnnoPilot 当前是 local-first annotation app，主要工作流是导入 TXT、人工 span 标注、Character RAG suggestions、LLM review、JSONL/Prodigy export 和审计。当前部署目标仍是单机 Docker、1 GB 内存预算和简单持久化目录。

项目还没有真实多用户协作、长耗时后台队列、大规模并发 suggestion generation 或跨项目权限模型。

## Decision

当前阶段暂不引入 Postgres、Redis、Celery、复杂 job queue、多用户 auth、大型 UI framework、Pinia 或 `vue-router`：

- 后端继续用 FastAPI + SQLite + JSONL + 外部 OpenAI-compatible LLM provider。
- 前端继续用 Vue 3 + Vite + page-level composables。
- 只有当 runs、exports、settings 或 project management 成为独立 screen 时，再引入 `vue-router`。
- 只有当 suggestion generation 或批量任务明显阻塞交互时，再引入 SSE progress 或后台 worker。

## Consequences

- 好处：保持运行时轻量、部署简单、开发反馈快，符合当前 1 GB 内存目标。
- 好处：避免过早为未验证的并发/协作场景付出架构复杂度。
- 代价：现阶段不支持复杂权限、多租户和横向扩展。
- 触发重评条件：真实并发用户、SQLite 写锁成为瓶颈、batch annotation 明显需要后台执行、或产品上出现独立 route-level 设置/任务/运行页。
