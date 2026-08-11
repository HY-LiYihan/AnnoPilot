# 0001 Local-first SQLite + JSONL Runtime

- Status: Accepted
- Date: 2026-08-11

## Context

AnnoPilot 当前目标是轻量、可本地部署、可审计的文本标注工作台。核心约束是单机运行、移动端友好、1 GB 内存预算、Docker 部署简单，并且要能导出 / 重建标注事实。

项目已经形成两个数据职责：SQLite 负责 runtime state 和高频查询，JSONL 负责 durable audit trail、导出和 rebuild 基础。

## Decision

继续采用 **SQLite runtime store + JSONL durable artifacts**：

- SQLite 保存 documents、sentences、tokens、annotations、tags、suggestions、runs、sessions 和 event outbox。
- JSONL 保存 project-level event log、task export、Prodigy-compatible export、manifest 和 run provenance。
- mutation 先在 SQLite transaction 中写 domain rows 和 event outbox；transaction 成功后再 flush 到 `events.jsonl`。
- session cursor 等纯 runtime state 保留在 SQLite，不进入 durable event log。

## Consequences

- 好处：部署轻、内存低、查询快、无需外部数据库服务，适合当前 local-first 产品阶段。
- 好处：JSONL 能作为审计、导出和 rebuild 的稳定边界，不依赖 SQLite 文件永远不损坏。
- 代价：多用户并发和横向扩展能力有限；如果未来有真实协作/多租户需求，需要重新评估 Postgres 或 object storage。
- 代价：SQLite schema migration 和 event replay 必须保持严格测试，否则 runtime store 与 durable artifacts 会漂移。
