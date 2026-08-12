# 0002 AnnotationStorage Facade 拆分

- Status: Accepted
- Date: 2026-08-11

## Context

`AnnotationStorage` 曾经集中承载 document import、annotation、tag、suggestion、runs、exports、audit、reset、event outbox 和 rebuild helper。这个单文件聚合降低了早期迭代成本，但随着 reader、suggestion review、Prodigy export、audit/rebuild 逐步成型，它已经成为主要维护风险。

API routers 当前比较薄，调用 `AnnotationStorage` 的方法名也已经被测试覆盖。一次性改 API 层会扩大回归面。

## Decision

保留 `AnnotationStorage` 作为兼容 facade，内部按领域渐进迁移到 `services/` 和 `repositories/`：

- `repositories/` 承接 SQL 查询、read model 和只读导出组装。
- `services/` 承接事务、校验、业务编排和 event-producing workflows。
- `events/` 承接 outbox 写入、JSONL flush、replay validation 和 replay apply。
- routers 暂时继续依赖 `AnnotationStorage`，公开 API path、request/response shape 不随拆分变化。

## Consequences

- 好处：每一刀都能用现有 API tests 兜底，不需要一次性迁移所有调用方。
- 好处：新功能可以优先进入明确 service/repository，不再继续扩大 `storage.py`。
- 代价：过渡期会存在 facade 代理和少量重复 helper，需要定期清理。
- 约束：拆分不能改变 JSONL event schema、export shape、run provenance hash 或 API response shape，除非单独提出新 ADR。

## Implementation Notes

- `events/outbox.py` 负责 event outbox enqueue / JSONL flush，`events/replay.py` 负责 rebuild preview 的事件校验和重放。
- `services/suggestion_decisions.py` 负责 suggestion accept/reject、sentence batch decision、review apply 和 document auto decision 的事务边界。
- `services/suggestions.py` 负责 Character RAG suggestion generation、LLM review context、review event 记录和 suggestion read model 查询；`AnnotationStorage` 仅保留兼容代理方法。
- `services/tags.py` 负责 tag CRUD、tag schema import/export、legacy seed 清理和默认 tag backfill；`AnnotationStorage` 仅保留兼容代理方法。
