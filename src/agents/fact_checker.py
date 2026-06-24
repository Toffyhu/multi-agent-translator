"""⑤ 事实核查Agent — 独立校验 + 搜索交叉验证

核心职责：
专门排查"翻译准确但事实错误"的问题：
- 人名、地名译法是否与权威来源一致
- 数字、时间、日期是否准确转换
- 专业概念是否被误解
- 引用文献是否真实可查

这是纯翻译模型的高频盲区，必须由独立角色调用搜索工具交叉验证。
"""

from __future__ import annotations

import json
from typing import Optional

from src.agents.base import BaseAgent, AgentResult


class FactCheckerAgent(BaseAgent):
    """事实核查Agent — 搜索+交叉验证"""

    agent_name = "fact_checker"
    agent_description = "事实验证：人名/地名/数字/概念/引用独立核查"

    def execute(
        self,
        drafted_chapters: list[dict],
        source_texts: Optional[list[dict]] = None,
        verify_dimensions: Optional[list[str]] = None,
    ) -> AgentResult:
        """
        对统稿后的全稿执行事实核查。

        Args:
            drafted_chapters: 主编统稿后的章节
            source_texts: 对应原文（用于比对）
            verify_dimensions: 要校验的维度，默认全部

        Returns:
            AgentResult.data 包含 {errors, suggestions, verified_count}
        """
        verify_dimensions = verify_dimensions or [
            "person_names",       # 人名
            "place_names",        # 地名
            "numeric_values",     # 数字
            "professional_concepts",  # 专业概念
            "citations",          # 引用
        ]

        all_errors = []
        total_verified = 0

        for ch in drafted_chapters:
            chapter_text = ch.get("edited", ch.get("translation", ""))

            # 1. 提取所有需要核查的实体
            entities = self._extract_verifiable_entities(chapter_text)

            # 2. 对每类实体执行核查
            for dimension in verify_dimensions:
                dimension_entities = entities.get(dimension, [])
                if not dimension_entities:
                    continue

                # 调用LLM核查（需要搜索能力的模型）
                errors = self._verify_dimension(
                    chapter_text, dimension, dimension_entities,
                    ch.get("chapter_id", "?"),
                )
                all_errors.extend(errors)
                total_verified += len(dimension_entities)

        # 汇总
        critical = [e for e in all_errors if e.get("severity") == "critical"]
        minor = [e for e in all_errors if e.get("severity") == "minor"]

        return AgentResult(
            success=True,
            data={
                "total_verified": total_verified,
                "errors_found": len(all_errors),
                "critical_errors": critical,
                "minor_errors": minor,
                "all_errors": all_errors,
                "suggestions": self._generate_suggestions(all_errors),
            },
            context=self._last_context,
        )

    def _extract_verifiable_entities(self, text: str) -> dict[str, list[str]]:
        """从译文中提取可核查实体"""
        system = """你是一个命名实体识别专家。从译文中提取以下类型的实体：

1. person_names: 人名（包括历史人物、作者、提及的学者等）
2. place_names: 地名（城市、国家、地区、机构等）
3. numeric_values: 数字信息（年份、数量、百分比、金额等）
4. professional_concepts: 专业概念（学术术语、法律术语、技术名词等）
5. citations: 引用信息（书名、论文标题、法律条文编号等）

输出JSON: {"person_names": [...], "place_names": [...], ...}

只输出JSON，不要解释。"""

        response, ctx = self._call_llm(system, text[:8000], max_tokens=2048)
        self._last_context = ctx

        try:
            text_resp = response.strip()
            if text_resp.startswith("```"):
                lines = text_resp.split("\n")
                text_resp = "\n".join(lines[1:-1]) if lines[-1].strip() == "```" else "\n".join(lines[1:])
            return json.loads(text_resp)
        except Exception:
            return {}

    def _verify_dimension(
        self,
        full_text: str,
        dimension: str,
        entities: list[str],
        chapter_id: str,
    ) -> list[dict]:
        """对特定维度的实体执行核查"""

        dimension_prompts = {
            "person_names": "核对此译文中的人名翻译是否与权威中文资料（如维基百科、官方译名）一致。如不一致，给出正确译名。",
            "place_names": "核对此译文中的地名翻译是否与标准中文地名一致。注意同一地名在不同时期可能有不同译法。",
            "numeric_values": "核对此译文中的数字转换是否准确（如英文的billion转中文的亿、日期格式等）。",
            "professional_concepts": "核对此译文中的专业术语翻译是否准确理解原意，是否存在望文生义的错误。",
            "citations": "核对此译文中的引用信息（书名、作者、年份等）是否准确，是否与已知文献一致。",
        }

        prompt = dimension_prompts.get(dimension, "核对此译文的准确性。")

        # 使用双花括号转义，避免被python f-string解析
        system = f"""你是一位专业的事实核查员。

请仔细审阅以下译文，{prompt}

对每个有问题的实体，输出一行:
[错误] 章节{chapter_id} | 类型:{dimension} | "{{entity}}" | 问题:xxx | 建议:xxx | 严重性:critical|minor

如果没有发现问题，输出: [通过] 类型:{dimension} 未发现事实性错误

注意：
- 只有你确定有误时才报告
- 不确定的标注为minor而非critical
- 人名地名以权威来源为准，不以个人记忆为准"""

        entities_text = "\n".join(f"- {e}" for e in entities[:30])
        user = f"""译文内容：
{full_text[:5000]}

需要核查的实体：
{entities_text}

请逐一核查并报告结果。"""

        response, ctx = self._call_llm(system, user, max_tokens=4096)
        self._last_context = ctx

        # 解析错误报告
        errors = []
        for line in response.split("\n"):
            line = line.strip()
            if line.startswith("[错误]"):
                parts = line.replace("[错误] ", "").split(" | ")
                if len(parts) >= 6:
                    errors.append({
                        "chapter_id": chapter_id,
                        "dimension": parts[1].split(":")[-1].strip() if ":" in parts[1] else parts[1],
                        "entity": parts[2].strip('"').split(":")[-1].strip() if ":" in parts[2] else parts[2],
                        "problem": parts[3].split(":")[-1].strip() if ":" in parts[3] else parts[3],
                        "suggestion": parts[4].split(":")[-1].strip() if ":" in parts[4] else parts[4],
                        "severity": parts[5].split(":")[-1].strip() if ":" in parts[5] else "minor",
                    })

        return errors

    def _generate_suggestions(self, errors: list[dict]) -> list[str]:
        """基于错误生成修复建议"""
        suggestions = []
        dimensions = {}
        for e in errors:
            dim = e.get("dimension", "unknown")
            if dim not in dimensions:
                dimensions[dim] = []
            dimensions[dim].append(e)

        for dim, dim_errors in dimensions.items():
            suggestions.append(
                f"维度 [{dim}] 发现 {len(dim_errors)} 个问题，"
                f"其中严重 {sum(1 for e in dim_errors if e.get('severity') == 'critical')} 个，"
                f"建议逐一核实并修正。"
            )

        return suggestions

    def _default_prompt(self) -> tuple[str, str]:
        return ("你是事实核查专家。", "请核对此译文。")
