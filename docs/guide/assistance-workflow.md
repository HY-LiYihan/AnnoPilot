# 滚动式 Assistance 工作流

滚动式 assistance 是 AnnoPilot 的人机协同标注路径。它从纯手工标注开始，只在单个标签拥有足够人工确认样例后生成草稿。机器草稿从不是正式 annotation，绝不覆盖人工 annotation。

## 冷启动和激活

- 每个标签独立激活，固定阈值为 **5**。
- 只有已完成句子中的 `source=human` span 计入该标签的 `human_verified_count`。
- API 返回每个标签的 `human_verified_count`、`trusted_count`、`threshold=5` 和 `active`，UI 可显示 `3/5` 或“辅助已激活”。
- 未激活标签仍可人工标注，用于继续积累冷启动样例。

因此最初的工作流完全手工：选择 token/span、赋予标签、完成句子。没有 assistance draft 时，`Enter` 保持普通的完成当前句语义。

## 队列和生成

一个 document 的 open 队列最多保留 5 个 `queued`、`running` 或 `ready` job。候选句必须同时是未完成、没有任何正式 annotation、且没有既有 assistance job 的 untouched sentence。

FastAPI lifespan 启动单进程 `AssistanceWorker`。worker 通过 SQLite 原子 claim 和 90 秒 lease 获取 job，最多并发 5 个外部 LLM 请求；进程中断后 lease 过期的 `running` job 可被重新 claim。LLM 未配置时 worker 不 claim job，队列维持 `queued`，不会伪造结果。

每个 job 冻结 `knowledge_revision`、active tags 和 tag schema hash，并从已确认样例、历史修正和负例选择上下文。LLM 输出必须通过原文、label、字符 offset、token boundary、confidence 与 span overlap verifier；首次不通过会带验证问题重试一次，仍失败才进入 `failed`。

在调用 LLM 前和写入结果前都会二次检查句子是否仍 untouched。若人工已开始标注、句子已完成或 tag schema 改变，job 被取消且不会写入草稿。合法结果写入 pending `annotation_suggestions` 并关联 `assistance_job_id`；正式 `annotations` 仍为空。

## 人工决策

当前句有 ready 或已 skip 的 draft 时，主流程只有三种决策：

| 操作 | 结果 |
| --- | --- |
| `Enter` / Confirm | 原子接受未修改草稿。空草稿同样可确认，作为可信的无 span 结果。 |
| 修改后 `Enter` / Confirm modified | 前端仅编辑 local draft；提交时一次性写最终 spans。 |
| Skip | 保留原草稿并将 job 移到队尾，不重新生成、不删除；之后会重新回到队列。 |

草稿状态下的选词、删除和标签快捷键只更新本地 draft。修改后可选错误原因：`missed_span`、`extra_span`、`wrong_label`、`boundary_too_wide`、`boundary_too_narrow`、`other`。不选不阻塞 correct；这类 feedback 会由低优先级 LLM 任务补充 `llm_inferred` 分类。

Confirm/correct 在一个 SQLite transaction 内完成 draft version 校验、annotation 写入、suggestion 状态更新、句子 `answer=accept`、feedback、job 状态、知识版本与 outbox event enqueue。任何失败整体回滚。`draft_id + draft_version` 过期、schema 改变、草稿非 ready 或人工已开始标注会返回 `409 Conflict`，前端保留 local draft。

Confirm 生成 `source=accepted_suggestion` annotation；correct 生成 `source=human` annotation。两种确认结果都会成为后续检索的可信样例；旧 draft 保持冻结，不因知识库增长而被改写。

## 持久化和审计

SQLite runtime 保存：

- `assistance_settings`：document 开关和知识版本；
- `assistance_jobs`：queue order、status、lease、模型、prompt/retrieval hash、原始响应、verifier、usage 和 draft version；
- `assistance_feedback`：原 draft、最终 spans、错误原因和说明；
- `annotation_suggestions.assistance_job_id`：草稿 span 与 job 的关联。

所有 mutation 在同一 SQLite transaction 内写 domain rows 和 `event_outbox`，随后 flush 到 `events.jsonl`。`assistance.activated`、`assistance.settings.updated`、`assistance.draft.generated`、`assistance.sentence.skipped`、`assistance.draft.confirmed`、`assistance.draft.corrected`、`assistance.error.classified` 都是 **audit-only** events。JSONL rebuild 的 canonical 状态仍来自 confirm/correct 同时写入的 `annotation.created` 和 `sentence.completed` events，不会因重放过程事件而重复写 annotation。

## HTTP API

```text
GET  /api/projects/{project_id}/documents/{document_id}/assistance
PUT  /api/projects/{project_id}/documents/{document_id}/assistance/settings
POST /api/projects/{project_id}/sentences/{sentence_id}/assistance/decision
```

`GET` 返回 `enabled`、`seed_per_tag=5`、`concurrency=5`、知识版本、标签进度及队列。每个 queue item 包含 `draft_id`、`draft_version`、状态、queue order、active labels、verifier 状态、usage 和草稿 spans。

`PUT` 请求体仅为：

```json
{ "enabled": true }
```

禁用会暂停 queued/running job，重新启用会把 paused job 放回 queued；已生成 draft 会保留。

`POST` 请求体示例：

```json
{
  "action": "correct",
  "draft_id": "assist_...",
  "draft_version": 1,
  "final_spans": [
    { "tag_id": "tag_...", "start_token_index": 2, "end_token_index": 3 }
  ],
  "error_reasons": ["boundary_too_wide"],
  "error_note": "optional"
}
```

`action` 只能是 `confirm`、`skip`、`correct`。confirm/skip 不需要 `final_spans`；correct 的 `final_spans` 可为空，代表人工确认该句无实体。成功响应返回 action、当前 sentence、完成状态、下一条 ready sentence id 和更新的 queue；`400` 为非法 action/reason/span，`404` 为资源不存在，`409` 为 stale/非 ready draft、schema 变化或人工抢占。

## OpenNER HTTP 实验

实验脚本为 `scripts/experiments/openner_assistance.py`。它只经公开 REST API 导入 TXT、读取 tokens/sentences、创建人工 annotation 和提交 assistance decision，绝不直接读取 SQLite。

经 `--help` 确认的参数：

```text
--api-base URL          required，例如 http://127.0.0.1:8888
--language {zh,en}     required
--limit N              default 100
--seed N               default 9
--project-id ID        default openner-experiment
--output-dir PATH      default tmp/openner/experiments
--skip-every N         default 0；用于循环队列场景
```

先以 seed 9 跑 100 句 pilot：

```bash
python3 scripts/experiments/openner_assistance.py --api-base http://127.0.0.1:8888 --language zh --limit 100 --seed 9
python3 scripts/experiments/openner_assistance.py --api-base http://127.0.0.1:8888 --language en --limit 100 --seed 9
```

通过 offset 对齐、无人工覆盖和无卡死 job 的 pilot 检查后，将相同命令中的 `--limit` 改为 `1000` 分别跑中文和英文全量。结果写入被 Git 忽略的 `tmp/openner/experiments/`，包含 typed exact P/R/F1、boundary F1、sentence exact、confirm/correct/skip/manual 计数、人工 span 编辑数、API 调用数、延迟、token usage、对齐覆盖率、覆盖违规计数和学习曲线。
