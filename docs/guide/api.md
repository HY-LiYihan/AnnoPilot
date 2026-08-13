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
GET  /api/projects/{project_id}/documents/{document_id}/review-queue?limit=20&order=position|random|uncertain|goldsmith|hybrid
GET  /api/projects/{project_id}/annotation-imports?document_id={document_id}&limit=5
POST /api/projects/{project_id}/documents/{document_id}/session/cursor
POST /api/projects/{project_id}/sentences/{sentence_id}/complete
```

- `import-txt` 接收 UTF-8 `.txt`，当前大小上限为 10 MB。
- `import-annotations-jsonl` 接收 Prodigy / AnnoPilot style `.jsonl` annotation records；frontend 在右侧 metrics/export panel 暴露 `Import JSONL` 入口。
- `documents` 返回最近 runtime documents 的轻量索引，包含 progress、annotation count 和 pending suggestion count，供 frontend 切换已导入文档。
- `summary` 返回 document meta、tags、metrics、全局 sentence queue 和当前 runtime session cursor，不返回完整 tokens；metrics 会包含 `annotation_label_counts` / `suggestion_label_counts`，suggestion 的 `suggestion_status_counts`、`suggestion_review_counts` / `reviewed_suggestion_count`，以及 pending suggestion 的 `suggestion_source_counts` / `suggestion_confidence_counts`，用于快速判断当前 label coverage、待审队列质量、LLM 评审分布和已处理建议分布。
- `sentences` 返回分页 sentence window，当前 API limit 上限为 200。
- `review-queue` 返回未完成且存在 pending suggestions 的句子列表，以及每句第一条候选，供 UI 快速跳转待审任务；`order=random` 提供稳定伪随机 baseline，`order=uncertain` 会按最低 Character RAG confidence 优先排序，`order=goldsmith` 会按低置信度、句内候选密度、latest LLM review 风险、Rosetta-style judge 风险和候选标签冲突综合排序，`order=hybrid` 会保留高风险优先，同时插入少量高置信且无 LLM / judge 风险的 `calibration` 抽检样本。队列 item 同时返回 `min_confidence`、`lexical_risk_score`、`llm_review_risk_score`、`judge_review_risk_score`、`candidate_disagreement_score`、`risk_score`、`risk_reason_codes` 和 `review_route`，其中 `lexical_risk_score=(1-min_confidence)×suggestion_count`，`candidate_disagreement_score` 表示同一句 pending candidates 在 label 或边界上互相冲突的强度，`judge_review_risk_score` 来自 latest review 的 `judge` 中低 `overall_score` / `boundary_score`、高 `missed_span_risk` / `extra_span_risk`、`needs_review`、`error_types` 和 `risk_flags`，`risk_score=lexical_risk_score+llm_review_risk_score+judge_review_risk_score+candidate_disagreement_score`。`risk_reason_codes` 是稳定原因码，例如 `candidate_conflict`、`llm_reject`、`llm_uncertain`、`judge_boundary`、`judge_missing_span`、`low_confidence` 和 `dense_candidates`，UI 和 Goldsmith JSONL 导出共用同一批原因；旧字段 `priority_score` 保持为 `min_confidence` 以兼容已有调用。
- `annotation-imports` 从 `events.jsonl` 读取最近的 JSONL annotation import history；frontend 用它在刷新页面后恢复最近导入摘要。
- `session/cursor` 保存默认人工会话的当前句位置，用于刷新后恢复标注阅读器状态；该状态保存在 SQLite runtime，不写入 JSONL audit log。
- `complete` 写入 `completed` 和 Prodigy-compatible `answer`，当前支持 `accept`、`ignore`、`reject`，以及 `completed=false` reopen 回 `pending`。

## Sample Presets

```text
GET  /api/projects/{project_id}/sample-presets
POST /api/projects/{project_id}/sample-presets/{preset_id}/load
```

- `sample-presets` 返回后端内置的轻量样例索引；当前包含通用、新闻/政策、学术/方法、平台复核、客服反馈、合规/法律、社交舆情、财报/投资者沟通、医疗/科学传播、AI 教育、气候/能源，以及 Goldsmith/Rosetta 校准场景十二类 bilingual Engagement 样例。
- `load` 会导入对应 label schema、导入 TXT 文档，并默认运行一次高置信 Character RAG suggestions；Goldsmith/Rosetta 校准样例会改用 `calibration_seed` 受控候选，保留重叠 span 和 label/boundary 分歧，便于测试 review queue 与 consistency/candidate-runs 导出。响应返回 document id、tag 列表、sentence/token 数量、suggestion run id 和本次候选统计。
- 该接口不改变现有手动 `tags/schema/import`、`import-txt` 或 `suggestions/run` API，只是把演示/测试工作流合成一个快捷入口。

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
POST /api/projects/{project_id}/documents/{document_id}/suggestions/auto-annotate
POST /api/projects/{project_id}/documents/{document_id}/suggestions/auto-reject
POST /api/projects/{project_id}/documents/{document_id}/suggestions/apply-llm-review
POST /api/projects/{project_id}/suggestions/{suggestion_id}/accept
POST /api/projects/{project_id}/suggestions/{suggestion_id}/reject
POST /api/projects/{project_id}/sentences/{sentence_id}/suggestions/accept
POST /api/projects/{project_id}/sentences/{sentence_id}/suggestions/reject
POST /api/projects/{project_id}/suggestions/{suggestion_id}/llm-review
POST /api/projects/{project_id}/sentences/{sentence_id}/suggestions/llm-review
POST /api/projects/{project_id}/sentences/{sentence_id}/suggestions/apply-llm-review
```

- Suggestions 当前由 Character RAG 生成，支持 document scope 和 sentence scope。
- `limit_per_sentence` 默认 6，上限 20；`min_confidence` 取值范围 0 到 1。
- Generate / auto-annotate response 会返回 `source_counts` 和 `confidence_counts`，分别汇总本次 run 的候选来源分布与置信度分布；置信度 bucket 为 `high >= 0.90`、`medium >= 0.75`、`low < 0.75`。
- `auto-annotate` 会先运行 Character RAG suggestions，再按同一 `min_confidence` 自动接受高置信 span，形成一键低算力自动标注 slice。
- Suggestion payload 保留 `evidence_text`、`match_key` 和 `evidence_match_key`，用于审计字符规则实际命中的原始证据与归一化匹配键。
- Accept 会创建 `source=accepted_suggestion` annotation，并把 suggestion 状态改为 `accepted`。
- Reject 会把 suggestion 状态改为 `rejected`，后续 Character RAG 会把同 tag + text 当作 negative example；如果 pending suggestion 已有 latest LLM review 且 recommendation 为 `reject`，重新运行 Character RAG 时也会作为 LLM-sourced negative example，并保留该已 review pending suggestion 等待人工决策。
- Sentence-level accept/reject 会在一个 SQLite transaction 中批量处理当前句 pending suggestions，UI 的 `A` / `X` 快捷键走这组 endpoint。
- LLM review 使用 OpenAI-compatible `/chat/completions`，返回 `recommendation`、`confidence`、`rationale`、`context_sha256`，并可携带 Rosetta-style `judge` object：`format_score`、`concept_fit_score`、`boundary_score`、`missed_span_risk`、`extra_span_risk`、`overall_score`、`needs_review`、`error_types` 和 `risk_flags`；sentence-scoped endpoint 会批量评审当前句仍 pending 且未被已有 annotation 覆盖的 suggestions。
- `apply-llm-review` 支持 document scope 和 sentence scope，会在一个 SQLite transaction 中应用 latest LLM recommendations：`accept` 创建 `accepted_suggestion` annotation，`reject` 更新 suggestion 状态，`uncertain` 或未评审 suggestions 保持 pending。
- Document metrics 会输出 `calibration_count`、`calibration_disagreement_count` 和 `calibration_error_rate`：只统计已经有 human accept/reject 决策且有 latest LLM review 的 suggestions，用于估计 review routing / judge signal 与人工判断的错配率，不把 LLM judge score 当成真实 accuracy。
- Document metrics 还会输出 `review_efficiency_curves`，按 `position`、`random`、`uncertain`、`goldsmith` 和 `hybrid` 回放已校准 suggestions 的累计错配发现曲线。Goldsmith / hybrid 的风险排序会同时纳入低 confidence、句内候选密度和 `candidate_disagreement_score`，让候选标签或边界互相冲突的句子优先进入人工复核。每条 curve 包含总 review/disagreement 数、前 5 条发现错配数、首次发现错配 rank，以及最多前 20 个累计点，用来比较 Goldsmith risk routing 是否比 random baseline 更早发现人工错配。

## Runs And Audit

```text
GET  /api/projects/{project_id}/runs
GET  /api/projects/{project_id}/runs/{run_id}/provenance.json
GET  /api/projects/{project_id}/audit
POST /api/projects/{project_id}/rebuild/preview
```

- Runs 当前记录 Character RAG run history、config、input count、suggestion count 和 accepted/rejected/pending counts。
- Runs、run provenance、manifest run summary 和 `suggestions.generated` event 都返回 `source_counts` / `confidence_counts`，按候选来源与置信度 bucket 汇总每次低算力 RAG run 的质量分布。
- Run provenance JSON 记录 run config、match keys、evidence、latest LLM review 和 decision event。
- Audit 汇总 event count、schema version、event types、actor distribution、pending outbox 和 replay issues。
- Rebuild preview 使用临时 SQLite database 重放 `events.jsonl`，不会覆盖当前 runtime database。

## Exports

```text
GET /api/projects/{project_id}/documents/{document_id}/export.jsonl
GET /api/projects/{project_id}/documents/{document_id}/export.prodigy.jsonl
GET /api/projects/{project_id}/documents/{document_id}/export.prodigy.spans.jsonl
GET /api/projects/{project_id}/documents/{document_id}/export.prodigy.bundle.zip
GET /api/projects/{project_id}/documents/{document_id}/export.goldsmith.review-queue.jsonl?order=hybrid&limit=100
GET /api/projects/{project_id}/documents/{document_id}/export.goldsmith.human-choices.jsonl
GET /api/projects/{project_id}/documents/{document_id}/export.goldsmith.hard-examples.jsonl
GET /api/projects/{project_id}/documents/{document_id}/export.goldsmith.boundary-feedback.jsonl
GET /api/projects/{project_id}/documents/{document_id}/export.goldsmith.consistency-scores.jsonl
GET /api/projects/{project_id}/documents/{document_id}/export.goldsmith.candidate-runs.jsonl
GET /api/projects/{project_id}/documents/{document_id}/export.manifest.json
GET /api/projects/{project_id}/events.jsonl
GET /api/projects/{project_id}/tags/schema.json
GET /api/projects/{project_id}/tags/prodigy-labels.json
```

- Task JSONL 使用 `annopilot.task.v1`，按 sentence 输出 tokens、spans、annotations、suggestions、answer、meta，并包含 Prodigy-style `_input_hash`、`_task_hash`、`_session_id`、`_annotator_id` 和 `_view_id`。
- Prodigy JSONL 使用 `prodigy.ner_manual.compat.v1`，保持 `_view_id=ner_manual`、`_session_id`、`_annotator_id`、`_input_hash` 和 `_task_hash`；包含 annotations 但尚未人工 complete 的句子会以顶层 `answer=accept` 导出，原始 runtime answer 保留在 `meta.answer`。每条 record 的 `meta.tag_schema` 会带上当前 label schema hash、label definitions、examples、shortcut 和颜色，便于导入 Prodigy 后仍能复核 Engagement 标签含义。`tags/prodigy-labels.json` 会额外导出纯 label list、CSV label argument 和 `ner.manual` / `spans.manual` command templates，方便把 JSONL 直接交给外部 Prodigy 流程。`export.prodigy.bundle.zip` 会把 Prodigy JSONL、Spans JSONL、labels config、tag schema、Goldsmith review queue、README 和 manifest 打成一个交付包，manifest 里的 artifact hashes 与 zip 内文件保持一致。
- Goldsmith review queue JSONL 使用 `annopilot.goldsmith_review_queue.v1`，按当前 `order` 导出待人工复核句子、rank、lexical / LLM / candidate conflict / 综合 risk score、route 和首条 suggestion；首条 suggestion 的 `latest_review` 会保留可选 `judge` scores，可作为 Rosetta 风格 `human_review_queue.jsonl`。
- Goldsmith human choices JSONL 使用 `annopilot.goldsmith_human_choices.v1`，导出已被人工 accept/reject 的 suggestions、latest LLM review / judge、是否错配和 span payload，可作为 Rosetta 风格 `human_choices.jsonl`。
- Goldsmith hard examples JSONL 使用 `annopilot.goldsmith_hard_examples.v1`，从 human choices 中筛出人工拒绝、LLM/人工分歧、低置信或 LLM uncertain 样本，并附 `failure_note` 作为 Rosetta hard-example / boundary-feedback 输入。
- Goldsmith boundary feedback JSONL 使用 `annopilot.goldsmith_boundary_feedback.v1`，合并 human hard examples 与仍 pending 但 latest LLM review 为 `reject` / `uncertain` 的候选，供下一轮 label boundary、负例和 bilingual examples 优化。
- Goldsmith consistency scores JSONL 使用 `annopilot.goldsmith_consistency_scores.v1`，对当前可见 pending suggestions 输出 sentence-level `score / agreement / pairwise_span_f1 / exact_match_rate / average_model_confidence / uncertainty_score / rosetta_route / review_route / candidate_scores`，其中 `pairwise_span_f1`、`uncertainty_score` 和 `rosetta_route=high|medium|low` 对齐 Rosetta `consistency_scores.jsonl` 的核心字段；后续可替换为真正 k-run self-consistency 而不改下游文件名语义。
- Goldsmith candidate runs JSONL 使用 Rosetta `rosetta.prodigy_candidate.v1`，把当前 pending suggestions 转成候选标注记录，包含 Prodigy-style `text/tokens/spans`、inline markup、model confidence、review metadata、Rosetta `high/medium/low` route、uncertainty score 和句内 consistency 摘要，可作为 Rosetta `candidate_runs.jsonl` 或候选选择题式人工复核输入。
- Manifest JSON 使用 `annopilot.export_manifest.v1`，记录 artifact hashes、source run ids、annotation import history、run provenance summaries、live queue metrics、`prodigy_readiness`、`prodigy_labels_json` 和 event audit，并提供排除生成时间的稳定 `content_sha256`。`prodigy_readiness` 会输出 `ready/status/blockers`、句子完成数、label coverage、pending suggestions 和 Prodigy schema versions，用于在归档前判断是否仍需人工复核或补标。
- Events JSONL 是 project-level audit trail。
- `annotations.imported` event 会保存逐行 `source_record_results` manifest，用于审计外部 JSONL 每条记录的 hash、匹配状态、目标 sentence、answer 和 Prodigy-style source metadata。
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
