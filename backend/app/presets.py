from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class CalibrationCandidate:
    sentence_contains: str
    text: str
    tag_id: str
    confidence: float
    evidence_text: str | None = None


APPRAISAL_ENGAGEMENT_TAG_SCHEMA: dict[str, Any] = {
    "schema_version": "annopilot.tag_schema.v1",
    "record_type": "tag_schema",
    "tags": [
        {
            "id": "engagement_monogloss",
            "name": "Monogloss 单声宣称",
            "description": "没有显性引入其他声音、可能性或反驳空间的直接命题。该类通常需要人工按整句或关键断言标注，默认不参与 lexical suggestion。",
            "examples": [],
            "shortcut": "1",
            "color": "#4F6F82",
        },
        {
            "id": "engagement_entertain",
            "name": "Entertain 可能化",
            "description": "通过可能性、推测、似然或主观判断打开对话空间。常见线索包括 may, might, perhaps, likely, 可能, 也许, 或许, 似乎。",
            "examples": ["may", "might", "perhaps", "likely", "suggests", "it seems", "possibly", "可能", "也许", "或许", "似乎", "大概"],
            "shortcut": "2",
            "color": "#326BD8",
        },
        {
            "id": "engagement_attribute_acknowledge",
            "name": "Attribute Acknowledge 归因承认",
            "description": "中性引用或归因他人声音，不明显拉开距离。常见线索包括 said, according to, reported, 表示, 指出, 认为, 称。",
            "examples": ["said", "according to", "reported", "noted", "argues", "told", "表示", "指出", "认为", "称", "说"],
            "shortcut": "3",
            "color": "#0B7565",
        },
        {
            "id": "engagement_attribute_distance",
            "name": "Attribute Distance 归因疏离",
            "description": "引用他人声音时保留怀疑、疏离或非承诺立场。常见线索包括 claim, allegedly, 据称, 声称, 所谓。",
            "examples": ["claim", "claimed", "allegedly", "reportedly", "so-called", "据称", "声称", "所谓", "号称"],
            "shortcut": "4",
            "color": "#7A3DB8",
        },
        {
            "id": "engagement_proclaim_endorse",
            "name": "Proclaim Endorse 认同背书",
            "description": "作者借证据、结果或事实表达支持，让命题显得被验证。常见线索包括 shows, demonstrates, proves, 表明, 证明, 显示。",
            "examples": ["shows", "showed", "demonstrates", "proved", "proves", "表明", "证明", "显示", "证实"],
            "shortcut": "5",
            "color": "#B98600",
        },
        {
            "id": "engagement_proclaim_pronounce",
            "name": "Proclaim Pronounce 强化宣称",
            "description": "通过显然性、确定性或强调表达收缩对话空间。常见线索包括 clearly, indeed, undoubtedly, 显然, 当然, 毫无疑问。",
            "examples": ["clearly", "indeed", "undoubtedly", "obviously", "显然", "当然", "毫无疑问", "无疑", "必然"],
            "shortcut": "6",
            "color": "#B43B59",
        },
        {
            "id": "engagement_proclaim_concur",
            "name": "Proclaim Concur 共识承认",
            "description": "把命题包装成读者也会认可的共同立场。常见线索包括 of course, naturally, 诚然, 的确, 确实。",
            "examples": ["of course", "naturally", "admittedly", "certainly", "诚然", "的确", "确实", "自然"],
            "shortcut": "7",
            "color": "#8A5F2F",
        },
        {
            "id": "engagement_disclaim_deny",
            "name": "Disclaim Deny 否认",
            "description": "直接否定某命题或声音。常见线索包括 not, never, cannot, no, 不是, 没有, 并非, 不能。",
            "examples": ["not", "never", "cannot", "no", "does not", "不是", "没有", "并非", "不能", "未能"],
            "shortcut": "8",
            "color": "#C45A2E",
        },
        {
            "id": "engagement_disclaim_counter",
            "name": "Disclaim Counter 转折反驳",
            "description": "承接或预设一种期待后转折、修正或反驳。常见线索包括 but, however, yet, nevertheless, 但是, 但, 然而, 不过。",
            "examples": ["but", "however", "yet", "nevertheless", "although", "但是", "但", "然而", "不过", "尽管"],
            "shortcut": "9",
            "color": "#C00000",
        },
    ],
}


APPRAISAL_ENGAGEMENT_TEXT = """The researcher said the pilot may help teachers, but it does not prove every classroom will improve.
According to the report, the new interface clearly reduces hesitation and shows where annotators disagree.
Some reviewers allegedly claimed the method was too simple, yet the audit trail demonstrates each decision.
Of course, a small gold set cannot solve every edge case; however, rejected suggestions become useful boundary examples.

研究员表示，这个试点可能帮助教师，但它不能证明每个课堂都会改善。
报告指出，新界面显然减少了犹豫，并显示标注者在哪里存在分歧。
有些评审据称声称这个方法过于简单，然而审计记录证明每一次决定都有依据。
诚然，一个小型 gold 样本不能解决所有边界问题；不过，被拒绝的建议会变成有用的反例。
"""


APPRAISAL_ENGAGEMENT_NEWS_POLICY_TEXT = """City officials said the transit pilot may shorten commutes, but residents noted that late-night routes are still unreliable.
According to the budget office, the new plan clearly protects essential services and shows where emergency funds were used.
Opposition leaders claimed the figures were inflated; however, the published audit does not support that allegation.
Of course, faster buses will not solve every housing problem, yet the mayor argues they can reduce daily pressure on workers.

市政府表示，交通试点可能缩短通勤时间，但居民指出，夜间线路仍然不可靠。
预算办公室称，新方案显然保护了基本服务，并显示应急资金被用在何处。
反对派声称这些数字被夸大；然而，公开审计并不能支持这种说法。
当然，更快的公交不能解决所有住房问题，不过市长认为它可以减轻工人的日常压力。
"""


APPRAISAL_ENGAGEMENT_ACADEMIC_METHOD_TEXT = """The authors argue that mixed annotation improves reliability, although the baseline does not include expert adjudication.
Prior work suggests that uncertainty sampling may reduce review cost, but the evidence remains limited across languages.
The ablation results clearly show that rejected spans become useful negative examples for the next retrieval pass.
Some participants reportedly felt the label names were too abstract; nevertheless, the guideline examples helped them converge.

作者认为，混合标注可以提高可靠性，尽管基线并未包含专家仲裁。
已有研究表明，不确定性抽样可能降低复核成本，但跨语言证据仍然有限。
消融结果清楚显示，被拒绝的 span 会成为下一轮检索中有用的负例。
一些参与者据称觉得标签名称过于抽象；不过，准则示例帮助他们逐渐达成一致。
"""


APPRAISAL_ENGAGEMENT_PLATFORM_REVIEW_TEXT = """The user said the tool may reduce review time, but it does not replace expert judgment.
According to the pilot notes, the hybrid queue clearly shows which spans need attention.
Some annotators allegedly claimed the labels were confusing; however, the examples demonstrate the intended boundary.
Of course, one export cannot prove corpus quality, yet the Prodigy file shows every accepted span.

用户表示，这个工具可能减少复核时间，但它不能替代专家判断。
试点记录指出，混合队列显然显示哪些 span 需要关注。
一些标注者据称声称标签令人困惑；然而，示例证明了预期边界。
当然，一次导出不能证明语料质量，不过 Prodigy 文件显示每个被接受的 span。
"""


APPRAISAL_ENGAGEMENT_CUSTOMER_SUPPORT_TEXT = """Customer support said the refund may arrive today, but the app does not clearly show the pending case.
According to the chat transcript, the agent reported that the label issue was fixed; however, users claimed the fix was not visible.
Some reviewers allegedly claimed the queue was confusing, yet the dashboard demonstrates which spans need review.
Of course, the workflow cannot solve every dispute; nevertheless, it proves accepted spans can be exported.

客服表示，退款可能今天到账，但应用并不能显然显示待处理工单。
聊天记录指出，坐席称标签问题已经修复；然而，用户声称修复并非可见。
一些复核者据称声称队列令人困惑，不过，仪表盘证明哪些 span 需要复核。
当然，这个流程不能解决所有争议；诚然，它显示被接受的 span 可以导出。
"""


APPRAISAL_ENGAGEMENT_LEGAL_COMPLIANCE_TEXT = """Counsel said the disclosure may satisfy regulators, but it does not guarantee approval.
According to the filing, the new control clearly shows how user data is retained and proves the audit trail was preserved.
The complainants allegedly claimed the notice was misleading; however, the internal review does not support that claim.
Of course, a compliance memo cannot resolve every dispute, yet it demonstrates which statements require legal review.

律师表示，该披露可能满足监管要求，但它并不能保证获得批准。
文件指出，新的控制措施清楚显示用户数据如何被保留，并证明审计轨迹已经保存。
投诉人据称声称该通知具有误导性；然而，内部复核并不支持这种说法。
当然，一份合规备忘录不能解决所有争议，不过它证明哪些陈述需要法律复核。
"""


APPRAISAL_ENGAGEMENT_SOCIAL_OPINION_TEXT = """The blogger said the update may help creators, although some users claimed it was only a cosmetic change.
According to community posts, the new dashboard clearly shows payout delays and demonstrates where complaints cluster.
Several accounts reportedly argued the policy was unfair; however, the company does not admit the ranking system is biased.
Of course, one viral thread cannot prove platform-wide harm, yet it suggests which claims deserve closer annotation.

博主表示，这次更新可能帮助创作者，尽管一些用户声称它只是表面变化。
社区帖子指出，新仪表盘清楚显示付款延迟，并证明投诉集中在哪里。
几个账号据称认为该政策不公平；然而，公司并不承认排序系统存在偏见。
当然，一条爆红帖子不能证明平台范围的伤害，不过它提示哪些说法值得更仔细标注。
"""


APPRAISAL_ENGAGEMENT_FINANCE_INVESTOR_TEXT = """The CFO said margin may recover next quarter, but the guidance does not guarantee free cash flow growth.
According to management, the retention data clearly shows enterprise demand is stabilizing and demonstrates pricing discipline.
Some analysts reportedly claimed the backlog was overstated; however, the company does not confirm that interpretation.
Of course, one strong quarter cannot prove a durable turnaround, yet the board argues the new controls reduce execution risk.

财务负责人表示，利润率可能在下个季度恢复，但该指引并不能保证自由现金流增长。
管理层指出，留存数据清楚显示企业需求正在稳定，并证明定价纪律有所改善。
一些分析师据称声称积压订单被夸大；然而，公司并不确认这种解读。
当然，一个强劲季度不能证明持久转型，不过董事会认为新的控制措施降低了执行风险。
"""


APPRAISAL_ENGAGEMENT_HEALTH_SCIENCE_TEXT = """The health agency said the booster may reduce severe cases, but it does not eliminate infection risk.
According to the trial report, the data clearly shows stronger protection in older adults and demonstrates a lower hospitalization rate.
Some commentators allegedly claimed the warning was exaggerated; however, the review does not support that conclusion.
Of course, one study cannot settle every clinical question, yet the authors argue it suggests which groups need follow-up.

卫生机构表示，加强针可能减少重症病例，但它并不能消除感染风险。
试验报告指出，数据清楚显示老年人保护力更强，并证明住院率较低。
一些评论者据称声称该警告被夸大；然而，复核并不支持这种结论。
当然，一项研究不能解决所有临床问题，不过作者认为它提示哪些群体需要随访。
"""


APPRAISAL_ENGAGEMENT_AI_EDUCATION_TEXT = """The district said the AI tutor may support struggling students, but it does not replace trained teachers.
According to the evaluation team, the pilot clearly shows faster feedback and demonstrates where learners still need human guidance.
Some parents reportedly claimed the system was biased; however, the review does not confirm that interpretation.
Of course, one semester cannot prove long-term learning gains, yet the committee argues the evidence suggests careful expansion.

学区表示，AI 辅导工具可能支持学习困难的学生，但它并不能取代受过训练的教师。
评估小组指出，试点清楚显示反馈速度更快，并证明学习者仍然需要人工指导的地方。
一些家长据称声称该系统存在偏见；然而，复核并不确认这种解读。
当然，一个学期不能证明长期学习收益，不过委员会认为这些证据提示可以谨慎扩展。
"""


APPRAISAL_ENGAGEMENT_CLIMATE_ENERGY_TEXT = """The ministry said the offshore wind plan may lower emissions, but it does not guarantee cheaper power this winter.
According to grid operators, the new storage data clearly shows fewer peak-hour shortages and demonstrates why backup capacity is still needed.
Some industry groups allegedly claimed the timetable was unrealistic; however, the climate council does not accept that conclusion.
Of course, one regional project cannot prove a full energy transition, yet analysts argue it suggests which investments deserve faster review.

能源部门表示，海上风电计划可能降低排放，但它并不能保证今年冬天电价更低。
电网运营方指出，新的储能数据清楚显示高峰时段缺口减少，并证明为什么仍然需要备用容量。
一些行业组织据称声称该时间表不现实；然而，气候委员会并不接受这种结论。
当然，一个区域项目不能证明完整的能源转型，不过分析人士认为它提示哪些投资值得更快复核。
"""


APPRAISAL_ENGAGEMENT_WORKPLACE_LABOR_TEXT = """The union said the new schedule may reduce burnout, but managers argue it does not guarantee higher retention.
According to the staff survey, the pilot clearly shows shorter handover delays and demonstrates where weekend shifts remain unfair.
Some supervisors reportedly claimed the complaints were exaggerated; however, the HR review does not support that claim.
Of course, one workshop cannot resolve every workplace dispute, yet employees argue it suggests which policies need joint review.

工会表示，新的排班可能减少倦怠，但管理层认为它并不能保证更高留任率。
员工调查指出，试点清楚显示交接延迟缩短，并证明周末班次仍然不公平的地方。
一些主管据称声称投诉被夸大；然而，人力复核并不支持这种说法。
当然，一次工作坊不能解决所有职场争议，不过员工认为它提示哪些政策需要共同复核。
"""


APPRAISAL_ENGAGEMENT_CALIBRATION_TEXT = """The audit clearly shows where reviewers disagree, but it may also show only one pilot case.
The memo allegedly claimed the model proves accuracy; however, the results only suggest improvement.
The reviewer said the label is stable, yet the same evidence clearly shows uncertainty.
Of course, a calibration set cannot prove every label boundary; nevertheless, it demonstrates why review routing matters.

审计记录清楚显示复核者在哪里分歧，但它也可能只显示一个试点案例。
备忘录据称声称模型证明了准确率；然而，结果只是提示有所改善。
复核者表示这个标签很稳定，不过同一证据清楚显示仍有不确定性。
当然，一个校准样本不能证明所有标签边界；不过，它证明为什么复核路由很重要。
"""


APPRAISAL_ENGAGEMENT_CALIBRATION_CANDIDATES = (
    CalibrationCandidate("audit clearly shows", "clearly", "engagement_proclaim_pronounce", 0.96, "clearly"),
    CalibrationCandidate("audit clearly shows", "clearly shows", "engagement_proclaim_endorse", 0.88, "clearly shows"),
    CalibrationCandidate("audit clearly shows", "shows", "engagement_proclaim_endorse", 0.98, "shows"),
    CalibrationCandidate("may also show", "may", "engagement_entertain", 0.98, "may"),
    CalibrationCandidate("may also show", "but", "engagement_disclaim_counter", 0.98, "but"),
    CalibrationCandidate("allegedly claimed", "allegedly claimed", "engagement_attribute_distance", 0.92, "allegedly claimed"),
    CalibrationCandidate("allegedly claimed", "claimed", "engagement_attribute_acknowledge", 0.76, "claimed"),
    CalibrationCandidate("only suggest improvement", "however", "engagement_disclaim_counter", 0.98, "however"),
    CalibrationCandidate("only suggest improvement", "suggest", "engagement_entertain", 0.84, "suggest"),
    CalibrationCandidate("same evidence clearly shows", "clearly", "engagement_proclaim_pronounce", 0.96, "clearly"),
    CalibrationCandidate("same evidence clearly shows", "clearly shows", "engagement_proclaim_endorse", 0.88, "clearly shows"),
    CalibrationCandidate("calibration set cannot prove", "Of course", "engagement_proclaim_concur", 0.98, "Of course"),
    CalibrationCandidate("calibration set cannot prove", "cannot", "engagement_disclaim_deny", 0.98, "cannot"),
    CalibrationCandidate("nevertheless", "nevertheless", "engagement_disclaim_counter", 0.98, "nevertheless"),
    CalibrationCandidate("review routing matters", "demonstrates", "engagement_proclaim_endorse", 0.98, "demonstrates"),
    CalibrationCandidate("审计记录清楚显示", "清楚", "engagement_proclaim_pronounce", 0.96, "清楚"),
    CalibrationCandidate("审计记录清楚显示", "清楚显示", "engagement_proclaim_endorse", 0.88, "清楚显示"),
    CalibrationCandidate("审计记录清楚显示", "显示", "engagement_proclaim_endorse", 0.98, "显示"),
    CalibrationCandidate("它也可能只显示", "可能", "engagement_entertain", 0.98, "可能"),
    CalibrationCandidate("据称声称", "据称声称", "engagement_attribute_distance", 0.92, "据称声称"),
    CalibrationCandidate("据称声称", "声称", "engagement_attribute_acknowledge", 0.76, "声称"),
    CalibrationCandidate("结果只是提示", "然而", "engagement_disclaim_counter", 0.98, "然而"),
    CalibrationCandidate("结果只是提示", "提示", "engagement_entertain", 0.84, "提示"),
    CalibrationCandidate("同一证据清楚显示", "清楚", "engagement_proclaim_pronounce", 0.96, "清楚"),
    CalibrationCandidate("同一证据清楚显示", "清楚显示", "engagement_proclaim_endorse", 0.88, "清楚显示"),
    CalibrationCandidate("一个校准样本不能证明", "当然", "engagement_proclaim_concur", 0.98, "当然"),
    CalibrationCandidate("一个校准样本不能证明", "不能", "engagement_disclaim_deny", 0.98, "不能"),
    CalibrationCandidate("不过，它证明", "不过", "engagement_disclaim_counter", 0.98, "不过"),
    CalibrationCandidate("复核路由很重要", "证明", "engagement_proclaim_endorse", 0.98, "证明"),
)


@dataclass(frozen=True)
class SamplePreset:
    id: str
    title: str
    description: str
    filename: str
    text: str
    tag_schema: dict[str, Any]
    language_pair: str
    default_limit_per_sentence: int = 10
    default_min_confidence: float = 0.98
    calibration_candidates: tuple[CalibrationCandidate, ...] = ()
    auto_accept_on_load: bool = True
    complete_sentences_on_load: bool = True

    def summary(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "description": self.description,
            "filename": self.filename,
            "language_pair": self.language_pair,
            "tag_count": len(self.tag_schema["tags"]),
            "default_limit_per_sentence": self.default_limit_per_sentence,
            "default_min_confidence": self.default_min_confidence,
            "calibration_candidate_count": len(self.calibration_candidates),
            "auto_accept_on_load": self.auto_accept_on_load,
            "complete_sentences_on_load": self.complete_sentences_on_load,
        }


BUILTIN_SAMPLE_PRESETS = {
    "appraisal-engagement-cn-en": SamplePreset(
        id="appraisal-engagement-cn-en",
        title="Appraisal Engagement 中英样例",
        description="内置 engagement label schema、双语测试文本和高置信 lexical suggestions，用于快速模拟人工标注任务。",
        filename="appraisal-engagement-cn-en.txt",
        text=APPRAISAL_ENGAGEMENT_TEXT,
        tag_schema=APPRAISAL_ENGAGEMENT_TAG_SCHEMA,
        language_pair="zh-en",
    ),
    "appraisal-engagement-news-policy-cn-en": SamplePreset(
        id="appraisal-engagement-news-policy-cn-en",
        title="Engagement 新闻/政策样例",
        description="面向新闻报道、政策争议和公共叙事的中英 engagement 线索，适合练习 attribute、disclaim 和 proclaim 的边界。",
        filename="appraisal-engagement-news-policy-cn-en.txt",
        text=APPRAISAL_ENGAGEMENT_NEWS_POLICY_TEXT,
        tag_schema=APPRAISAL_ENGAGEMENT_TAG_SCHEMA,
        language_pair="zh-en",
    ),
    "appraisal-engagement-academic-method-cn-en": SamplePreset(
        id="appraisal-engagement-academic-method-cn-en",
        title="Engagement 学术/方法样例",
        description="面向论文方法、实验结果和研究讨论的中英 engagement 线索，适合练习 evidence、uncertainty 和 countering。",
        filename="appraisal-engagement-academic-method-cn-en.txt",
        text=APPRAISAL_ENGAGEMENT_ACADEMIC_METHOD_TEXT,
        tag_schema=APPRAISAL_ENGAGEMENT_TAG_SCHEMA,
        language_pair="zh-en",
    ),
    "appraisal-engagement-platform-review-cn-en": SamplePreset(
        id="appraisal-engagement-platform-review-cn-en",
        title="Engagement 平台复核样例",
        description="面向产品反馈、标注平台复核和 Prodigy 导出的中英 engagement 线索，适合测试 mixed review workflow。",
        filename="appraisal-engagement-platform-review-cn-en.txt",
        text=APPRAISAL_ENGAGEMENT_PLATFORM_REVIEW_TEXT,
        tag_schema=APPRAISAL_ENGAGEMENT_TAG_SCHEMA,
        language_pair="zh-en",
    ),
    "appraisal-engagement-customer-support-cn-en": SamplePreset(
        id="appraisal-engagement-customer-support-cn-en",
        title="Engagement 客服反馈样例",
        description="面向客服记录、用户反馈和产品支持复盘的中英 engagement 线索，适合练习 reported voice、denial、countering 和 export-ready spans。",
        filename="appraisal-engagement-customer-support-cn-en.txt",
        text=APPRAISAL_ENGAGEMENT_CUSTOMER_SUPPORT_TEXT,
        tag_schema=APPRAISAL_ENGAGEMENT_TAG_SCHEMA,
        language_pair="zh-en",
    ),
    "appraisal-engagement-legal-compliance-cn-en": SamplePreset(
        id="appraisal-engagement-legal-compliance-cn-en",
        title="Engagement 合规/法律样例",
        description="面向披露、监管、申诉和法律复核的中英 engagement 线索，适合练习 claim、deny、endorse 和 counter 的高风险边界。",
        filename="appraisal-engagement-legal-compliance-cn-en.txt",
        text=APPRAISAL_ENGAGEMENT_LEGAL_COMPLIANCE_TEXT,
        tag_schema=APPRAISAL_ENGAGEMENT_TAG_SCHEMA,
        language_pair="zh-en",
    ),
    "appraisal-engagement-social-opinion-cn-en": SamplePreset(
        id="appraisal-engagement-social-opinion-cn-en",
        title="Engagement 社交舆情样例",
        description="面向社交媒体、平台争议和用户评论的中英 engagement 线索，适合练习 reported voice、countering、denial 和 uncertainty。",
        filename="appraisal-engagement-social-opinion-cn-en.txt",
        text=APPRAISAL_ENGAGEMENT_SOCIAL_OPINION_TEXT,
        tag_schema=APPRAISAL_ENGAGEMENT_TAG_SCHEMA,
        language_pair="zh-en",
    ),
    "appraisal-engagement-finance-investor-cn-en": SamplePreset(
        id="appraisal-engagement-finance-investor-cn-en",
        title="Engagement 财报/投资者沟通样例",
        description="面向财报电话会、管理层指引和分析师质疑的中英 engagement 线索，适合练习 guidance、evidence、denial 和 risk framing。",
        filename="appraisal-engagement-finance-investor-cn-en.txt",
        text=APPRAISAL_ENGAGEMENT_FINANCE_INVESTOR_TEXT,
        tag_schema=APPRAISAL_ENGAGEMENT_TAG_SCHEMA,
        language_pair="zh-en",
    ),
    "appraisal-engagement-health-science-cn-en": SamplePreset(
        id="appraisal-engagement-health-science-cn-en",
        title="Engagement 医疗/科学传播样例",
        description="面向公共卫生、试验结果和风险传播的中英 engagement 线索，适合练习 hedging、endorsement、claim distancing 和 clinical uncertainty。",
        filename="appraisal-engagement-health-science-cn-en.txt",
        text=APPRAISAL_ENGAGEMENT_HEALTH_SCIENCE_TEXT,
        tag_schema=APPRAISAL_ENGAGEMENT_TAG_SCHEMA,
        language_pair="zh-en",
    ),
    "appraisal-engagement-ai-education-cn-en": SamplePreset(
        id="appraisal-engagement-ai-education-cn-en",
        title="Engagement AI 教育样例",
        description="面向 AI 辅导、教育政策和课堂试点评估的中英 engagement 线索，适合练习 cautious expansion、bias claim、human guidance 和 evidence framing。",
        filename="appraisal-engagement-ai-education-cn-en.txt",
        text=APPRAISAL_ENGAGEMENT_AI_EDUCATION_TEXT,
        tag_schema=APPRAISAL_ENGAGEMENT_TAG_SCHEMA,
        language_pair="zh-en",
    ),
    "appraisal-engagement-climate-energy-cn-en": SamplePreset(
        id="appraisal-engagement-climate-energy-cn-en",
        title="Engagement 气候/能源样例",
        description="面向气候政策、能源转型和基础设施争议的中英 engagement 线索，适合练习 hedging、industry claims、countering 和 evidence-backed planning。",
        filename="appraisal-engagement-climate-energy-cn-en.txt",
        text=APPRAISAL_ENGAGEMENT_CLIMATE_ENERGY_TEXT,
        tag_schema=APPRAISAL_ENGAGEMENT_TAG_SCHEMA,
        language_pair="zh-en",
    ),
    "appraisal-engagement-workplace-labor-cn-en": SamplePreset(
        id="appraisal-engagement-workplace-labor-cn-en",
        title="Engagement 职场/劳动关系样例",
        description="面向工会沟通、HR 复核、排班争议和员工反馈的中英 engagement 线索，适合练习 institutional voice、countering、denial 和 policy review framing。",
        filename="appraisal-engagement-workplace-labor-cn-en.txt",
        text=APPRAISAL_ENGAGEMENT_WORKPLACE_LABOR_TEXT,
        tag_schema=APPRAISAL_ENGAGEMENT_TAG_SCHEMA,
        language_pair="zh-en",
    ),
    "appraisal-engagement-calibration-cn-en": SamplePreset(
        id="appraisal-engagement-calibration-cn-en",
        title="Engagement Goldsmith/Rosetta 校准样例",
        description="内置重叠 span、相邻候选和中英边界冲突，用于测试 Goldsmith review queue、Rosetta consistency 和 Prodigy bundle。",
        filename="appraisal-engagement-calibration-cn-en.txt",
        text=APPRAISAL_ENGAGEMENT_CALIBRATION_TEXT,
        tag_schema=APPRAISAL_ENGAGEMENT_TAG_SCHEMA,
        language_pair="zh-en",
        default_limit_per_sentence=20,
        default_min_confidence=0.75,
        calibration_candidates=APPRAISAL_ENGAGEMENT_CALIBRATION_CANDIDATES,
        auto_accept_on_load=False,
        complete_sentences_on_load=False,
    ),
}


def list_sample_presets() -> list[dict[str, Any]]:
    return [preset.summary() for preset in BUILTIN_SAMPLE_PRESETS.values()]


def get_sample_preset(preset_id: str) -> SamplePreset | None:
    return BUILTIN_SAMPLE_PRESETS.get(preset_id)
