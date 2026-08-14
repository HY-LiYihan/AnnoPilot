# Appraisal Engagement 标注样例

AnnoPilot 当前可以直接承载 appraisal theory 里的 **Engagement** 系统标注：用 project-level span labels 表达 label schema，用 Character RAG lexical examples 生成候选，再通过人工 accept/reject、LLM review、JSONL audit 和 Prodigy export 完成闭环。

## 样例文件

仓库内置以下可直接导入的样例文件：

```text
samples/appraisal-engagement-tag-schema.json
samples/appraisal-engagement-cn-en.txt
samples/appraisal-engagement-news-policy-cn-en.txt
samples/appraisal-engagement-academic-method-cn-en.txt
samples/appraisal-engagement-platform-review-cn-en.txt
samples/appraisal-engagement-customer-support-cn-en.txt
samples/appraisal-engagement-legal-compliance-cn-en.txt
samples/appraisal-engagement-social-opinion-cn-en.txt
samples/appraisal-engagement-finance-investor-cn-en.txt
samples/appraisal-engagement-health-science-cn-en.txt
samples/appraisal-engagement-ai-education-cn-en.txt
samples/appraisal-engagement-climate-energy-cn-en.txt
samples/appraisal-engagement-workplace-labor-cn-en.txt
samples/appraisal-engagement-product-safety-cn-en.txt
samples/appraisal-engagement-crisis-response-cn-en.txt
samples/appraisal-engagement-election-factcheck-cn-en.txt
samples/appraisal-engagement-calibration-cn-en.txt
```

Web UI 空白阅读器中也会显示同一组内置样例按钮；点击后会自动加载 schema、TXT，并运行一次高置信 Character RAG suggestions。`Engagement Goldsmith/Rosetta 校准样例` 会改用内置受控候选，预置中英重叠 span 和 label/boundary 分歧，用来测试 review queue、consistency score 和 candidate runs 导出。

`appraisal-engagement-tag-schema.json` 使用 `annopilot.tag_schema.v1`，覆盖：

| Label | 用途 |
| --- | --- |
| `Monogloss 单声宣称` | 没有显性打开对话空间的直接断言，通常由人工标整句或关键断言 |
| `Entertain 可能化` | may / 可能 / 似乎 等推测或可能性线索 |
| `Attribute Acknowledge 归因承认` | said / according to / 表示 / 指出 等中性归因 |
| `Attribute Distance 归因疏离` | allegedly / claim / 据称 / 声称 等疏离归因 |
| `Proclaim Endorse 认同背书` | shows / demonstrates / 表明 / 证明 等证据背书 |
| `Proclaim Pronounce 强化宣称` | clearly / undoubtedly / 显然 / 毫无疑问 等强化表达 |
| `Proclaim Concur 共识承认` | of course / admittedly / 诚然 / 的确 等共同立场表达 |
| `Disclaim Deny 否认` | not / cannot / 不是 / 不能 等否定线索 |
| `Disclaim Counter 转折反驳` | but / however / 但是 / 然而 等转折反驳线索 |

## 建议工作流

1. 在空白阅读器中选择一个内置样例：通用标注流程、新闻/政策叙事、学术/方法讨论、平台复核、客服反馈、合规/法律、社交舆情、财报/投资者沟通、医疗/科学传播、AI 教育、气候/能源转型、职场/劳动关系、产品安全/公众意见、危机回应/公共警示、选举/事实核查，或 Goldsmith/Rosetta 校准场景。
2. 如果想手动复现，也可以左侧导入 `samples/appraisal-engagement-tag-schema.json`，再在中间导入任一 `samples/appraisal-engagement-*.txt`。
3. 人工 accept/reject suggestions：accept 会生成 `source=accepted_suggestion` annotation；human reject 和 latest LLM review `reject` 都会成为同 label 的 negative example，下一轮 Character RAG 会避开同一错误 span；已 LLM-reviewed 的 pending suggestions 会保留等待人工决策。
4. 对 Monogloss 或整句断言，可直接按 `M` 创建整句 Monogloss span、完成当前句并前进；需要先微调标签时，也可按 `S` 先把当前整句设为 pending span，再按对应数字快捷键或点击左侧 label 应用标签。
5. 完成句子后用右侧 `Export Prodigy` 或 `Export Prodigy Spans` 导出，可直接得到 Prodigy-compatible JSONL。

## Goldsmith 对齐

这里采用的是轻量版 Goldsmith 思路，而不是把 Rosetta 的完整 prompt-training runner 搬入 Web runtime：

- **Gold seed**：label schema 中的 bilingual lexical examples 是最小 gold-like seed。
- **Score review**：suggestion confidence、LLM review recommendation 和人工 accept/reject 共同形成 review signal。
- **Random baseline**：右侧 review queue 支持 `random` 稳定伪随机基线，方便和 uncertainty / risk routing 比较人工审核收益。
- **Hybrid review**：右侧 review queue 支持 `hybrid`，先排高风险句子，同时保留少量高置信样本抽检，用来估计自动通过样本的真实错误率；UI 的 `R` / next review 跳转会跟随当前选择的 position、random、uncertain、risk 或 hybrid 队列排序。
- **Candidate conflict risk**：Goldsmith / hybrid 风险排序会把同一句 pending candidates 的 label 或 span boundary 分歧纳入 `candidate_disagreement_score`，让多候选互相冲突的 Engagement 边界样本更早进入人工复核。
- **Error discovery**：Document metrics 会用已有 human accept/reject 决策和 latest LLM review 生成累计错配发现曲线，比较 random、uncertainty、Goldsmith risk 和 hybrid queue 在前 5 条复核中发现多少错配。
- **Review artifacts**：右侧运行状态可导出 Goldsmith/Rosetta-style `candidate_runs.jsonl`、`consistency_scores.jsonl`、`label_statistics.jsonl`、`contrastive_examples.jsonl`、`reflection_plans.jsonl`、`prompt_package.jsonl`、`verification_report.jsonl`、`bootstrap_report.md`、`human_review_queue.jsonl`、`human_choices.jsonl` 和 `hard_examples.jsonl`，把 engagement 标注中的候选、Rosetta route、不确定性分数、token-level entity/context/other 概率、similar/boundary examples、reflection checks、prompt tasks、verifier checks、人工优先复核报告、candidate conflict risk、待审队列、人工选择、LLM review / judge、错配标记和边界失败样例交给离线优化/评估流程。
- **Guided LLM review**：LLM review context 会携带 Engagement label definitions、bilingual examples、candidate span context、已有句内标注、同标签 boundary feedback 和边界规则；review 可保存 Rosetta-style `judge` scores（format、concept fit、boundary、missed/extra span risk、overall、risk flags），`context_sha256` 会随该结构化上下文一起进入 audit log，便于追踪当次复核依据。
- **Boundary feedback**：rejected suggestions 作为 negative examples，人工拒绝、pending LLM reject、LLM/人工分歧、低置信和 LLM uncertain 样本也会进入下一条同标签 LLM review context，形成在线 hard-example 反馈。
- **Auditability**：SQLite 保存 runtime state，`events.jsonl` 保存可审计 durable event；manifest 会记录 export hash、run provenance 和 audit summary。

这个版本符合当前 1GB 内存目标：不引入向量库、队列或重型 optimizer；当前已把 Rosetta-style contrastive retrieval、reflection、prompt package、verifier report 和 bootstrap report 作为导出型离线接口接入，后续再把更重的 prompt optimization runner 放到离线 pipeline。
