"""② 术语与风格制定Agent — 术语提取 + 风格五维量化

核心职责：
1. 从全书分析报告和原文中提取高频专有名词，制定统一译法
2. 将模糊的"大师风格"拆解为五维可量化规则
3. 输出术语规范表和风格执行手册 → 写入全局资产库

强制卡点：输出必须经人工/用户确认后，才能发布给下游Agent使用。
"""

from __future__ import annotations

import json

from src.agents.base import BaseAgent, AgentResult
from src.assets.schema import (
    TerminologyTable, TermEntry, TermLevel,
    StyleManual, StyleDimension,
)


class TermStylistAgent(BaseAgent):
    """术语与风格制定Agent — 语言学专业分析"""

    agent_name = "term_stylist"
    agent_description = "术语规范提取 + 翻译风格五维量化拆解"

    def execute(
        self,
        source_lang: str = "en",
        target_lang: str = "zh",
        target_style: str = "傅雷风格 — 流畅地道的中文，避免欧化句式",
        reference_works: list[str] | None = None,
        target_audience: str = "中文母语读者",
        sample_texts: list[str] | None = None,
    ) -> AgentResult:
        """
        制定术语表和风格手册。

        Args:
            source_lang: 源语言
            target_lang: 目标语言
            target_style: 目标风格描述
            reference_works: 参考标杆译作列表
            target_audience: 目标读者画像
            sample_texts: 代表性原文片段（用于术语提取和风格分析）

        Returns:
            AgentResult.data 包含 {terminology, style_manual}
        """
        book_analysis = self.assets.get_book_analysis()
        reference_works = reference_works or []

        # 获取全书数据用于术语提取
        all_text = ""
        if book_analysis:
            all_text = book_analysis.summary + "\n" + "\n".join(
                f"第{c.chapter_id}章: {c.title} - {'; '.join(c.key_events)}"
                for c in book_analysis.chapters
            )
            sample_texts = sample_texts or [
                f"第{c.chapter_id}章核心事件: {'; '.join(c.key_events)}"
                for c in book_analysis.chapters[:5]
            ]

        # 1. 提取术语
        term_result = self._extract_terminology(
            source_lang, target_lang, all_text, sample_texts or []
        )

        # 2. 制定风格手册
        style_result = self._build_style_manual(
            target_style, reference_works, target_audience, sample_texts or []
        )

        # 3. 写入资产库（⚠️ 此处标记为待审批状态）
        self.assets.set_terminology(term_result)
        self.assets.set_style_manual(style_result)

        return AgentResult(
            success=True,
            data={
                "terminology": term_result.model_dump(),
                "style_manual": style_result.model_dump(),
                "approval_required": True,
                "approval_items": [
                    "术语表 — 请逐条确认译法，特别是Mandatory级别的核心术语",
                    "风格手册 — 请确认五维规则是否符合目标风格",
                ],
            },
            context=self._last_context,
        )

    def _extract_terminology(
        self,
        source_lang: str,
        target_lang: str,
        context: str,
        samples: list[str],
    ) -> TerminologyTable:
        """从上下文中提取高频术语并给出统一译法"""

        system = f"""你是一位专业的翻译术语管理专家。

请从提供的内容上下文中提取所有需要统一译法的术语和专有名词，包括：
- 人名、地名、机构名
- 专业概念、学术术语
- 高频关键词
- 容易产生歧义的词汇

对每个术语：
1. 给出源语言到{target_lang}的统一译法
2. 标注强制等级：
   - "mandatory": 必须统一使用的核心术语（如主角名、核心概念）
   - "flexible": 可以有变体但建议统一（如一般性描述词）

输出JSON格式：
{{
  "terms": [
    {{
      "source": "源语言术语",
      "target": "目标语言译法",
      "level": "mandatory|flexible",
      "domain": "领域",
      "notes": "说明"
    }}
  ]
}}"""

        samples_text = "\n---\n".join(samples) if samples else context[:5000]
        user = f"""请分析以下书籍内容的术语：

源语言: {source_lang} → 目标语言: {target_lang}

内容上下文:
{context[:3000] if context else "无"}

代表性片段:
{samples_text}

请提取所有需要统一译法的术语，输出JSON格式。"""

        response, ctx = self._call_llm(system, user, max_tokens=4096)
        self._last_context = ctx

        try:
            data = self._parse_json(response)
            entries = [
                TermEntry(
                    source=t["source"],
                    target=t["target"],
                    level=TermLevel(t.get("level", "mandatory")),
                    domain=t.get("domain", "general"),
                    notes=t.get("notes", ""),
                    alternatives=t.get("alternatives", []),
                )
                for t in data.get("terms", [])
            ]
        except Exception:
            entries = []

        return TerminologyTable(
            source_lang=source_lang,
            target_lang=target_lang,
            entries=entries,
        )

    def _build_style_manual(
        self,
        target_style: str,
        reference_works: list[str],
        target_audience: str,
        samples: list[str],
    ) -> StyleManual:
        """将目标风格拆解为五维量化规则"""

        system = """你是一位翻译风格分析专家。

请将目标翻译风格拆解为五个可量化维度：

1. **句式结构** (sentence_structure)：平均句长目标、长短句比例、主动/被动语态偏好
2. **词汇密度** (vocabulary_density)：实词/虚词比、四字格频率、成语使用倾向
3. **修辞偏好** (rhetoric_preference)：比喻/排比/反问等修辞格的使用倾向
4. **语气调性** (tone_register)：口语化/书面化比值、敬语/随意度
5. **文化适配** (cultural_adaptation)：归化/异化策略偏好、文化负载词处理方式

输出JSON：
{
  "dimensions": [
    {
      "name": "维度名称",
      "description": "详细说明",
      "metrics": ["量化指标1", "量化指标2"],
      "target_range": "目标区间描述",
      "reference_examples": [{"source": "原文", "good": "推荐译法", "bad": "禁忌译法"}]
    }
  ],
  "forbidden_patterns": ["禁止表述1"],
  "reference_works": []
}"""

        user = f"""请拆解以下翻译风格：

目标风格: {target_style}
目标读者: {target_audience}
参考译作: {', '.join(reference_works) if reference_works else '无指定'}

请输出五维量化规则的JSON。"""

        response, ctx = self._call_llm(system, user, max_tokens=4096)
        self._last_context = ctx

        try:
            data = self._parse_json(response)
            dimensions = [
                StyleDimension(
                    name=d["name"],
                    description=d.get("description", ""),
                    metrics=d.get("metrics", []),
                    target_range=d.get("target_range", ""),
                    reference_examples=d.get("reference_examples", []),
                )
                for d in data.get("dimensions", [])
            ]
        except Exception:
            dimensions = []

        return StyleManual(
            target_style=target_style,
            target_audience=target_audience,
            dimensions=dimensions,
            forbidden_patterns=data.get("forbidden_patterns", []) if 'data' in dir() else [],
            reference_works=reference_works,
        )

    def _parse_json(self, text: str) -> dict:
        text = text.strip()
        if text.startswith("```"):
            lines = text.split("\n")
            text = "\n".join(lines[1:-1]) if lines[-1].strip() == "```" else "\n".join(lines[1:])
        return json.loads(text)

    def _default_prompt(self) -> tuple[str, str]:
        return ("你是翻译术语和风格专家。", "请分析并输出JSON。")
