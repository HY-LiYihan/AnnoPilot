from __future__ import annotations

from dataclasses import dataclass
from typing import Any


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
            "examples": ["may", "might", "perhaps", "likely", "it seems", "可能", "也许", "或许", "似乎", "大概"],
            "shortcut": "2",
            "color": "#326BD8",
        },
        {
            "id": "engagement_attribute_acknowledge",
            "name": "Attribute Acknowledge 归因承认",
            "description": "中性引用或归因他人声音，不明显拉开距离。常见线索包括 said, according to, reported, 表示, 指出, 认为, 称。",
            "examples": ["said", "according to", "reported", "noted", "表示", "指出", "认为", "称", "说"],
            "shortcut": "3",
            "color": "#0B7565",
        },
        {
            "id": "engagement_attribute_distance",
            "name": "Attribute Distance 归因疏离",
            "description": "引用他人声音时保留怀疑、疏离或非承诺立场。常见线索包括 claim, allegedly, 据称, 声称, 所谓。",
            "examples": ["claim", "claimed", "allegedly", "so-called", "据称", "声称", "所谓", "号称"],
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
    )
}


def list_sample_presets() -> list[dict[str, Any]]:
    return [preset.summary() for preset in BUILTIN_SAMPLE_PRESETS.values()]


def get_sample_preset(preset_id: str) -> SamplePreset | None:
    return BUILTIN_SAMPLE_PRESETS.get(preset_id)
