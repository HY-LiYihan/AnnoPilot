# Rolling Assistance 架构

Rolling Assistance 是当前 annotation workspace 的人机协同闭环。它在已有人工样本足够时，持续为后续句子准备一小批可编辑 draft；模型输出必须经过程序校验和人工确认，不能直接写入正式 annotation。

## 设计目标

- 让人工标注形成 few-shot knowledge，并逐步减少重复劳动。
- 始终以人工 decision 作为 annotation commit boundary。
- 在单容器、单 Uvicorn worker、1 GB 内存预算下运行。
- 服务重启后恢复 queue，不依赖进程内任务状态。
- 保留模型输入依据、输出、校验、人工纠正和 token usage 的审计线索。

## 组件边界

```text
Reader UI
  useReaderAssistance.ts
        |
        | GET status / POST decision
        v
api/assistance.py
        |
AssistanceService ----------------------+
        |                               |
        | queue / decision transaction  | generation context
        v                               v
SQLite                           AssistanceWorker
                                        |
                              assistance_generation.py
                                        |
                              OpenAI-compatible provider
```

- `api/assistance.py` 只负责 HTTP contract 和 `400/404/409` error mapping。
- `services/assistance.py` 拥有 activation、queue、lease、generation result、decision 和 feedback 的事务规则。
- `assistance_worker.py` 在 FastAPI lifespan 内启动，轮询 SQLite，最多并发处理 5 个 job。
- `assistance_generation.py` 负责 prompt、example selection、provider call 和 deterministic verifier。
- `useReaderAssistance.ts` 轮询状态，并维护尚未提交的本地 draft edits。

## 激活与滚动队列

每个 tag 的 activation threshold 固定为 5 个 `source=human` annotations。`accepted_suggestion`、自动 Monogloss 和未确认模型 draft 不计入这个门槛，避免模型输出自我强化。

只要至少一个 tag active，`ensure_queue` 会为当前 document 维持最多 5 个开放 job。候选句必须同时满足：

- `completed = 0`；
- 当前没有 annotation；
- 过去没有对应 assistance job。

队列按 sentence position 创建。Skip 不丢弃 draft，而是移到队尾；没有新句可补充时，skipped item 可以再次回到 ready。

## Job 状态机

```text
                 disable
queued --------------------------> paused
  |                                  |
  | claim                            | enable
  v                                  v
running -- verified --> ready <--- queued
  |                    |  |  
  | error              |  | skip
  v                    |  v
queued / failed        | skipped
                       |
                       +-- confirm --> confirmed
                       +-- correct --> corrected

running -- stale context --> cancelled
```

- Worker claim 时写入 90 秒 `lease_until`；过期的 running job 可被重新 claim。
- 生成或校验失败时重试整个 job 一次，第二次失败进入 `failed`。
- 暂停 assistance 会把 queued/running 改为 `paused`，重新启用后恢复为 queued。
- 生成结束前如果句子已有人工 annotation、已完成，或 tag schema hash 改变，job 会 cancelled，绝不覆盖新状态。

## Generation 与 verifier

Worker 生成一条 draft 时会冻结：

- `knowledge_revision`；
- active tag ids 与 `tag_schema_sha256`；
- 每个标签的相似人工 examples 和近期 corrections；
- rejected/negative examples；
- `prompt_sha256`、model、raw response 和 usage。

Example selection 最多保留每个标签 20 条数据；大集合按 character bigram similarity 选 8 条，再补最多 4 条近期 correction。LLM 使用 temperature 0 和 JSON object response format。

Provider 输出需要满足：

- 返回文本与 source sentence 完全一致；
- label 必须来自冻结的 active schema；
- character offsets 合法，且严格对齐本地 token boundaries；
- span text 必须等于原文切片；
- confidence 位于 0 到 1；
- spans 不重复、不重叠。

第一次校验失败时，issues 会作为修正上下文再请求一次；第二次仍失败则 job 失败。通过校验的 span 写入 `annotation_suggestions`，以 `assistance_job_id` 关联，但状态仍是 pending。

## 人工 Decision Boundary

UI 展示 ready draft 时，会把 spans 复制为 local state。用户可删除、替换或新增 span；这些操作不会立即调用 annotation API。

最终有三类 decision：

- `confirm`：原样接受 draft。
- `correct`：提交本地修改后的 non-overlapping spans，可同时选择 missed span、extra span、wrong label、boundary too wide/narrow 或 other。
- `skip`：暂不判断并移到队尾，不完成句子。

Confirm/correct 使用 `draft_id + draft_version` 做 optimistic concurrency check，并在同一个 SQLite transaction 内：

1. 再次验证句子仍为空、span 和 tag 仍合法。
2. 创建 annotations，并更新关联 suggestions 的 accepted/rejected 状态。
3. 将 sentence 写为 `completed=true, answer=accept`。
4. 保存 original/final spans 和 error reasons 到 `assistance_feedback`。
5. 将 job 写为 confirmed/corrected，并推进 `knowledge_revision`。
6. 写入 canonical annotation/sentence events 和 assistance audit events。

若用户没有为 correction 选择 error reason，worker 会异步分类差异；该分类只补充 feedback，不改变已确认 annotations。

## SQLite 与 JSONL

Schema version 7 新增：

```text
assistance_settings
  project_id, document_id, enabled
  knowledge_revision, queue_sequence, updated_at

assistance_jobs
  identity: id, project_id, document_id, sentence_id, run_id
  queue: status, queue_order, lease_until, attempt_count
  snapshot: knowledge_revision, draft_version, active_tag_ids_json, tag_schema_sha256
  generation: retrieved_examples_json, prompt_sha256, model, raw_response, result_json
  verification: verifier_status, verifier_issues_json, error_message
  accounting: usage_json, created_at, updated_at

assistance_feedback
  job_id, action, original_spans_json, final_spans_json
  error_reasons_json, reason_source, error_note, created_at, classified_at
```

Assistance audit events 包括：

```text
assistance.activated
assistance.settings.updated
assistance.draft.generated
assistance.sentence.skipped
assistance.draft.confirmed
assistance.draft.corrected
assistance.error.classified
```

这些 assistance events 是 audit-only。可重建的最终业务状态仍由同一 transaction 产生的 `annotation.created` 和 `sentence.completed` canonical events 表达，避免 rebuild 对同一次确认重复落库。

## API

```text
GET  /api/projects/{project_id}/documents/{document_id}/assistance
PUT  /api/projects/{project_id}/documents/{document_id}/assistance/settings
POST /api/projects/{project_id}/sentences/{sentence_id}/assistance/decision
```

Status 返回 tag activation progress、queue counts/items、knowledge revision 和累计 token usage。Frontend 默认每 2.5 秒轮询；这满足当前单 workspace，但不是实时推送协议。

## 运行边界

`ASSISTANCE_WORKER_ENABLED` 默认开启。没有有效 LLM 配置时 worker 不 claim job，API 和人工标注仍可正常工作。

当前 worker 与 FastAPI 同进程，因此部署必须保持单 Uvicorn worker。若直接增加多个 API worker，每个进程都会启动 coordinator；虽然 SQLite claim 和 lease 能减少重复处理，但这不是当前正式支持的扩缩容模型。需要多副本时，应先把 worker 变成独立 deployment，并为 claim 增加更明确的 owner/heartbeat 与运行指标。

当前轮询、最多 5 个 in-flight job、每次只传单句与有限 examples，不加载本地模型，符合 1 GB runtime 目标。外部 provider 的延迟和 token 费用通过 queue counts、attempt count 与 usage totals 暴露。

## 验证覆盖

- API：activation、status、settings、confirm/correct/skip、stale draft conflict。
- Service/worker：lease reclaim、重试/失败、schema/context 冲突、feedback classification。
- Generation：JSON contract、offset/token boundary、overlap、example selection。
- Frontend：draft 初始化、span replacement、修改判断和 API interaction。
- OpenNER experiment：通过 public HTTP API 对中英文数据执行可复现实验，不直接读取 SQLite。
